from __future__ import annotations

import http.client
import inspect
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tests import _bootstrap

import server as server_mod
from server import ROUTES, Handler, _match

METHODS = ("GET", "POST", "PUT", "DELETE")

def _segments(pattern: str) -> list:
    return pattern.split("/")

def _wildcards(pattern: str) -> list:
    return [s[1:-1] for s in _segments(pattern) if s.startswith("{")]

def _filled(pattern: str, value: str = "none") -> str:
    return "/" + "/".join(value if s.startswith("{") else s
                          for s in _segments(pattern))

class RouteTableShapeTest(unittest.TestCase):
    def test_rows_are_well_formed(self):
        self.assertGreater(len(ROUTES), 60)
        seen = set()
        for method, pattern, handler in ROUTES:
            self.assertIn(method, METHODS, pattern)
            self.assertTrue(pattern.startswith("api/"), pattern)
            self.assertNotIn("//", pattern)
            self.assertFalse(pattern.endswith("/"), pattern)
            for seg in _segments(pattern):
                self.assertTrue(seg, pattern)
                if seg.startswith("{"):
                    self.assertTrue(seg.endswith("}") and seg[1:-1].isidentifier(),
                                    pattern)
            names = _wildcards(pattern)
            self.assertEqual(len(names), len(set(names)), pattern)
            self.assertNotIn((method, pattern), seen, "duplicate row")
            seen.add((method, pattern))
            self.assertTrue(handler.startswith("_"), handler)

    def test_every_handler_exists_and_takes_the_wildcards(self):
        for method, pattern, handler in ROUTES:
            fn = getattr(Handler, handler, None)
            self.assertTrue(callable(fn), f"{method} {pattern}: no Handler.{handler}")
            params = [p for p in inspect.signature(fn).parameters if p != "self"]
            self.assertEqual(len(params), len(_wildcards(pattern)),
                             f"{handler} takes {params} for {pattern}")

    def test_no_two_rows_can_match_the_same_path(self):
        rows = [(m, _segments(p)) for m, p, _ in ROUTES]
        for i, (m1, a) in enumerate(rows):
            for m2, b in rows[i + 1:]:
                if m1 != m2 or len(a) != len(b):
                    continue
                overlap = all(x == y or x.startswith("{") or y.startswith("{")
                              for x, y in zip(a, b))
                self.assertFalse(overlap, f"{m1} {'/'.join(a)} overlaps {'/'.join(b)}")

class MatchTest(unittest.TestCase):
    def test_literals_must_equal_and_wildcards_capture_in_order(self):
        pat = ["api", "workflows", "{wid}", "runs", "{rid}"]
        self.assertEqual(_match(pat, ["api", "workflows", "w1", "runs", "r7"]),
                         ["w1", "r7"])
        self.assertIsNone(_match(pat, ["api", "workflows", "w1", "nodes", "r7"]))
        self.assertIsNone(_match(pat, ["api", "workflows", "w1", "runs"]))
        self.assertIsNone(_match(pat, ["api", "workflows", "w1", "runs", "r7", "x"]))
        self.assertEqual(_match(["api", "version"], ["api", "version"]), [])

    def test_dispatch_reaches_each_row_with_its_values(self):
        hits = []

        class Probe(Handler):
            pass

        probe = Probe.__new__(Probe)
        for method, pattern, handler in ROUTES:
            setattr(Probe, handler,
                    lambda self, *a, _h=handler: hits.append((_h, a)))
            parts = [f"v{i}" if s.startswith("{") else s
                     for i, s in enumerate(_segments(pattern))]
            self.assertTrue(probe._dispatch(method, parts), pattern)
            want = tuple(p for p, s in zip(parts, _segments(pattern))
                         if s.startswith("{"))
            self.assertEqual(hits[-1], (handler, want), pattern)
        self.assertFalse(probe._dispatch("GET", ["api", "no-such-route"]))
        self.assertFalse(probe._dispatch("PATCH", ["api", "version"]))

class EveryRouteAnswersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request(method, path, body=body, headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def _stubs(self):
        import updater
        import providers
        from agent import codex_engine
        from storage import secrets_store
        empty = Path(tempfile.mkdtemp(prefix="cryogram_route_"))
        return [
            mock.patch.object(updater, "check", lambda: {"available": False}),
            mock.patch.object(server_mod, "_native_pick_folder",
                              lambda want="folder": {"path": None}),
            mock.patch.object(providers, "check_key",
                              lambda *a, **k: {"ok": False, "error": "stub"}),
            mock.patch.object(providers, "list_models", lambda p: {"models": []}),
            mock.patch.object(codex_engine, "check_login",
                              lambda: {"ok": False, "error": "stub"}),
            mock.patch.object(secrets_store, "forget_key", lambda: None),
            mock.patch.object(server_mod.config, "DATA_DIR", empty),
        ]

    def test_every_row_is_answered_by_its_handler(self):
        stubs = self._stubs()
        for s in stubs:
            s.start()
        try:
            for method, pattern, handler in ROUTES:
                body = "{}" if method in ("POST", "PUT") else None
                status, data = self._req(method, _filled(pattern), body)
                self.assertNotEqual(status, 0, pattern)
                try:
                    payload = json.loads(data or b"null")
                except ValueError:
                    payload = None
                self.assertFalse(isinstance(payload, dict)
                                 and payload.get("error") == "unknown route",
                                 f"{method} {pattern} never reached {handler}")
        finally:
            for s in stubs:
                s.stop()

    def test_an_unknown_route_is_a_plain_404(self):
        for method in METHODS:
            status, data = self._req(method, "/api/no-such-thing",
                                     "{}" if method in ("POST", "PUT") else None)
            self.assertEqual(status, 404, method)
            self.assertEqual(json.loads(data), {"error": "unknown route"})
