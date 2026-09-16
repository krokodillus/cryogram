# Tests for backend/telemetry.py: the one channel, and that it never sends unasked
from __future__ import annotations

import json
import unittest
from unittest import mock

from tests import _bootstrap

import telemetry
from runtime import corpus
from storage import secrets_store
from storage import settings
from storage import store

def _workflow(pid="p_tel"):
    p = {"id": pid, "name": "Weekly digest",
         "nodes": [
             {"id": "n1", "name": "Fetch posts", "type": "connector",
              "read_only": True, "external_impact": "",
              "inputs": [{"name": "url", "type": "text"}],
              "outputs": [{"name": "posts", "type": "any"}],
              "config": {"code": "r = http_get(read_input('url'))\n"
                                 "write_output('posts', r)",
                         "browser": False}},
             {"id": "n2", "name": "Summarise", "type": "ai",
              "inputs": [{"name": "posts", "type": "any"}],
              "outputs": [{"name": "digest", "type": "text"}],
              "config": {"prompt": "Summarise the posts."}},
         ],
         "edges": [{"src": "n1", "dst": "n2"}],
         "variables": [{"name": "search_url", "value": "https://x/private-q",
                        "secret": False, "persistent": True}],
         "tickets": [], "transcript": [], "transcript_seq": 0,
         "egress_allowlist": ["api.example.com"]}
    store.save(p)
    return p

class IssueReportBoundary(unittest.TestCase):
    def setUp(self):
        self.p = _workflow()
        secrets_store.set_secret("tel_key", "sk-tel-secret-77aa", secrets_store.OWNER_APP)
        case_id = corpus.record_failure(
            "p_tel", "n1", {"url": "https://x"}, cause="TimeoutError: the read operation timed out (token sk-tel-secret-77aa)",
            cls="transient-network", output={"posts": ["a" * 5000]})
        self.ticket = {"id": "tkt_tel1", "node_id": "n1", "case_id": case_id,
                       "reason": "checks-failed",
                       "verdict": {"n1": ["posts: never produced"]},
                       "entry_inputs": {"search_url": "https://x/private-q"}}

    def test_carries_the_actionable_parts(self):
        r = telemetry.issue_report(self.p, self.ticket)
        self.assertEqual(r["kind"], "issue")
        self.assertEqual(r["reason"], "checks-failed")
        self.assertEqual(r["failure_class"], "transient-network")
        self.assertIn("TimeoutError", r["error"])
        self.assertIn("http_get", r["step"]["code"])
        self.assertEqual(r["step"]["type"], "connector")
        self.assertEqual([s["name"] for s in r["workflow"]["steps"]],
                         ["Fetch posts", "Summarise"])
        self.assertEqual(r["workflow"]["edges"],
                         [{"src": "Fetch posts", "dst": "Summarise"}])

        self.assertEqual(r["output_shape"]["redaction"], "structural")

        self.assertNotIn("machine", r)
        self.assertNotIn("client", r)

    def test_the_envelope_carries_the_browser_and_the_os_and_nothing_else(self):
        from unittest import mock
        seen = {}
        class _R:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def fake_open(req, **kw):
            seen["headers"] = dict(req.header_items())
            return _R()
        from storage import settings
        with mock.patch.object(telemetry.urllib.request, "urlopen", fake_open), \
             mock.patch.object(settings, "get", return_value={"report": {"url": "https://example.test/report"}}):
            self.assertTrue(telemetry.send_issue(self.p, self.ticket, "Safari/1"))
        names = {k.lower() for k in seen["headers"]}
        self.assertEqual(seen["headers"].get("User-agent"), "Safari/1")
        self.assertTrue(seen["headers"].get("X-cryogram-os"))
        self.assertFalse({n for n in names if "token" in n or "install" in n})

    def test_secrets_and_user_data_never_cross(self):
        r = telemetry.issue_report(self.p, self.ticket)
        blob = json.dumps(r)
        self.assertNotIn("sk-tel-secret-77aa", blob)
        self.assertIn("<secret>", r["error"])
        self.assertNotIn("private-q", blob)
        self.assertNotIn("entry_inputs", blob)
        self.assertNotIn("a" * 100, blob)

    def test_error_text_is_capped_with_a_marker(self):
        self.ticket["verdict"] = {"n1": ["x" * 5000]}
        r = telemetry.issue_report(self.p, self.ticket)
        v = r["verdict"]["n1"][0]
        self.assertLessEqual(len(v), telemetry.ERROR_TEXT_CAP + 3)
        self.assertTrue(v.endswith("..."))

class ConsentAndTransport(unittest.TestCase):
    def test_report_mode_defaults_to_ask(self):
        self.assertEqual(telemetry.report_mode(), "ask")
        settings.update({"preferences": {"share_reports": "never"}})
        try:
            self.assertEqual(telemetry.report_mode(), "never")
        finally:
            settings.update({"preferences": {"share_reports": "ask"}})

    def test_there_is_no_send_without_being_asked(self):
        settings.update({"preferences": {"share_reports": "always"}})
        try:
            self.assertEqual(telemetry.report_mode(), "ask")
        finally:
            settings.update({"preferences": {"share_reports": "ask"}})

    def test_nothing_is_sent_in_the_background(self):
        for gone in ("pulse", "maybe_pulse", "usage_opted_in", "PULSE_INTERVAL"):
            self.assertFalse(hasattr(telemetry, gone), gone)

    def test_the_send_adds_no_token_or_install_id(self):
        import ast, inspect
        code = ast.unparse(ast.parse(inspect.getsource(telemetry._send) + inspect.getsource(telemetry.envelope)))
        for forbidden in ("token", "Token", "install", "Install"):
            self.assertNotIn(forbidden, code)

    def test_failed_send_never_raises(self):
        with mock.patch.object(telemetry.urllib.request, "urlopen",
                               side_effect=OSError("down")):
            self.assertFalse(telemetry._send({"kind": "issue"}))

    def test_no_address_means_no_send(self):
        before = (settings.get().get("report") or {}).get("url")
        try:
            settings.update({"report": {"url": ""}})
            with mock.patch.object(telemetry.urllib.request, "urlopen",
                                   side_effect=AssertionError("must not send")):
                self.assertFalse(telemetry._send({"kind": "issue"}))
        finally:
            settings.update({"report": {"url": before}})

if __name__ == "__main__":
    unittest.main()
