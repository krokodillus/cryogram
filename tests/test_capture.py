# Tests: demonstration-capture import: scrub-on-import is the secrets invariant at the door - auth headers/cookies/token-like fields masked to shape descriptors (field kept, value never stored), URLs and JSON bodies walked, domains extracted, and the sample entry written with replace-by-name
from __future__ import annotations

import json
import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from storage import blobstore
from agent import capture
from runtime import scrub

def _rec():
    return {
        "version": 1, "url": "https://portal.example/orders",
        "actions": [{"ts": 1, "kind": "click", "target": 'button "Log in"'}],
        "calls": [
            {"ts": 2, "method": "POST", "url": "https://api.portal.example/login",
             "request": {"headers": {"Content-Type": "application/json"},
                         "body": json.dumps({"user": "ulrik", "password": "hunter2"})},
             "response": {"status": 200,
                          "headers": {"Set-Cookie": "sid=SECRETCOOKIE123"},
                          "body": json.dumps({"access_token": "tok_ABC123XYZ",
                                              "expires_in": 3600})}},
            {"ts": 3, "method": "GET",
             "url": "https://api.portal.example/orders?api_key=sk_live_99&page=1",
             "request": {"headers": [{"name": "Authorization",
                                      "value": "Bearer tok_ABC123XYZ"}]},
             "response": {"status": 200, "headers": {},
                          "body": json.dumps({"orders": [{"id": 7}]})}},
        ],
    }

class ScrubTest(unittest.TestCase):
    def test_credentials_masked_shape_kept(self):
        clean = scrub.recording(_rec())
        blob = json.dumps(clean)
        for secret in ("hunter2", "tok_ABC123XYZ", "SECRETCOOKIE123", "sk_live_99"):
            self.assertNotIn(secret, blob)
        login, orders = clean["calls"]

        self.assertIn("redacted", json.loads(login["response"]["body"])["access_token"])
        self.assertIn("redacted", login["response"]["headers"]["Set-Cookie"])
        self.assertIn("redacted", orders["request"]["headers"][0]["value"])
        self.assertEqual(orders["request"]["headers"][0]["name"], "Authorization")
        self.assertIn("api_key=%3Credacted", orders["url"])
        self.assertIn("page=1", orders["url"])

        self.assertEqual(json.loads(orders["response"]["body"]), {"orders": [{"id": 7}]})
        self.assertEqual(json.loads(login["response"]["body"])["expires_in"], 3600)

    def test_domains_extracted(self):
        self.assertEqual(capture.domains_of(_rec()), ["api.portal.example"])

class ImportTest(unittest.TestCase):
    def test_import_scrubs_stores_and_replaces_by_name(self):
        p = _workflow(pid="p_cap1")
        r = capture.import_recording(p, json.dumps(_rec()), name="orders.json")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["calls"], 2)
        self.assertEqual(r["domains"], ["api.portal.example"])
        s = p["samples"][0]
        self.assertEqual((s["name"], s["kind"]), ("orders.json", "recording"))
        stored = blobstore.get(s["ref"]).decode()
        self.assertNotIn("hunter2", stored)

        capture.import_recording(p, json.dumps(_rec()), name="orders.json")
        self.assertEqual(len(p["samples"]), 1)

    def test_secret_lists_under_token_keys_are_masked(self):
        rec = {"version": 1, "actions": [], "calls": [{
            "ts": 1, "url": "https://api.portal.example/x", "method": "POST",
            "request": {"headers": {},
                        "body": json.dumps({"api_keys": ["sk-liveSECRETVALUE123"],
                                            "note": "fine"})},
            "response": {"status": 200, "headers": {}, "body": "{}"}}]}
        p = _workflow(pid="p_cap3")
        r = capture.import_recording(p, json.dumps(rec), name="r.json")
        self.assertTrue(r["ok"], r)
        stored = blobstore.get(p["samples"][0]["ref"]).decode()
        self.assertNotIn("sk-liveSECRETVALUE123", stored)
        self.assertIn("fine", stored)

    def test_reasoned_refusals(self):
        p = _workflow(pid="p_cap2")
        self.assertIn("error", capture.import_recording(p, "not json at all"))
        self.assertIn("error", capture.import_recording(p, json.dumps({"version": 1})))
        self.assertIn("error", capture.import_recording(p, "x" * (26 * 1024 * 1024)))

def _har():
    return {"log": {"version": "1.2", "entries": [
        {"startedDateTime": "2026-08-04T10:00:00Z",
         "request": {"method": "POST",
                     "url": "https://api.portal.example/login",
                     "headers": [{"name": "Content-Type",
                                  "value": "application/json"}],
                     "postData": {"mimeType": "application/json",
                                  "text": json.dumps(
                                      {"password": "hunter2"})}},
         "response": {"status": 200,
                      "headers": [{"name": "Set-Cookie",
                                   "value": "sid=SECRETCOOKIE123"}],
                      "content": {"mimeType": "application/json",
                                  "text": json.dumps(
                                      {"token": "tok_ABC123"})}}},
        {"startedDateTime": "2026-08-04T10:00:02Z",
         "request": {"method": "GET",
                     "url": "https://api.portal.example/orders?page=1",
                     "headers": []},
         "response": {"status": 200, "headers": [],
                      "content": {"text": json.dumps(
                          {"orders": [{"id": 7}]})}}}]}}

class HarImportTest(unittest.TestCase):
    def test_har_normalised_and_scrubbed(self):
        p = _workflow(pid="p_har1")
        r = capture.import_recording(p, json.dumps(_har()), name="cap.har")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["calls"], 2)
        self.assertEqual(r["domains"], ["api.portal.example"])
        stored = blobstore.get(p["samples"][0]["ref"]).decode()
        for secret in ("hunter2", "SECRETCOOKIE123", "tok_ABC123"):
            self.assertNotIn(secret, stored)
        self.assertIn("orders", stored)

    def test_looks_like_recording_sniff(self):
        self.assertTrue(capture.looks_like_recording(json.dumps(_har())))
        self.assertTrue(capture.looks_like_recording(json.dumps(_rec())))
        self.assertFalse(capture.looks_like_recording(
            json.dumps({"orders": [1, 2]})))
        self.assertFalse(capture.looks_like_recording("plain text"))
        self.assertFalse(capture.looks_like_recording(
            json.dumps({"log": "a string, not a HAR log"})))

class ImportToolTest(unittest.TestCase):
    def test_from_blob(self):
        from agent import node_tools
        p = _workflow(pid="p_har2")
        ref = blobstore.put(json.dumps(_har()).encode(),
                            "application/json", {"name": "cap.har"}, owner=blobstore.OWNER_APP)
        r = node_tools.tool_import_recording(p, name="site traffic",
                                             from_blob=ref)
        self.assertTrue(r.get("ok"), r)
        self.assertIn("domains", r)
        self.assertEqual(p["samples"][0]["kind"], "recording")

    def test_from_sample_replaces_the_raw_upload(self):
        from agent import node_tools
        p = _workflow(pid="p_har3")
        raw = json.dumps(_har()).encode()
        p["samples"] = [{"name": "cap.har",
                         "ref": blobstore.put(raw, "application/json",
                                              {"name": "cap.har"}, owner=blobstore.OWNER_APP),
                         "mime": "application/json", "size": len(raw)}]
        r = node_tools.tool_import_recording(p, from_sample="cap.har")
        self.assertTrue(r.get("ok"), r)
        names = [s["name"] for s in p["samples"]]
        self.assertEqual(names.count("cap.har"), 1)
        self.assertEqual(p["samples"][0]["kind"], "recording")

    def test_raw_paths_and_bad_sources_refused(self):
        from agent import node_tools
        p = _workflow(pid="p_har4")
        self.assertIn("error", node_tools.tool_import_recording(
            p, from_blob="/tmp/cap.har"))
        self.assertIn("error", node_tools.tool_import_recording(p))
        self.assertIn("error", node_tools.tool_import_recording(
            p, from_sample="nope"))

if __name__ == "__main__":
    unittest.main()

class TheBrowsersOwnRecordingIsMaskedTooTest(unittest.TestCase):
    def _har(self):
        return {"log": {"version": "1.2", "entries": [{
            "request": {
                "method": "POST",
                "url": "https://api.example.com/v1/items?api_key=SEKRIT&page=2",
                "headers": [{"name": "Cookie", "value": "session=SEKRIT"},
                            {"name": "Authorization", "value": "Bearer SEKRITSEKRIT"},
                            {"name": "Accept", "value": "application/json"}],
                "queryString": [{"name": "api_key", "value": "SEKRIT"},
                                {"name": "page", "value": "2"}],
                "cookies": [{"name": "session", "value": "SEKRIT"}],
                "postData": {"mimeType": "application/json",
                             "text": json.dumps({"token": "SEKRIT", "note": "keep me"})}},
            "response": {
                "status": 200,
                "headers": [{"name": "Set-Cookie", "value": "session=SEKRIT"},
                            {"name": "Content-Type", "value": "application/json"}],
                "cookies": [{"name": "session", "value": "SEKRIT"}],
                "content": {"mimeType": "application/json",
                            "text": json.dumps({"items": [1, 2, 3], "secret": "SEKRIT"})}}}]}}

    def test_every_credential_goes_and_the_page_content_stays(self):
        from runtime import scrub
        out = scrub.har_bytes(json.dumps(self._har()).encode())
        self.assertNotIn("SEKRIT", out.decode())
        d = json.loads(out.decode())
        e = d["log"]["entries"][0]

        self.assertEqual(d["log"]["version"], "1.2")
        self.assertEqual(json.loads(e["response"]["content"]["text"])["items"], [1, 2, 3])
        self.assertEqual(json.loads(e["request"]["postData"]["text"])["note"], "keep me")
        self.assertIn("page=2", e["request"]["url"])
        names = {h["name"]: h["value"] for h in e["response"]["headers"]}
        self.assertEqual(names["Content-Type"], "application/json")
        self.assertTrue(names["Set-Cookie"].startswith("<redacted"))
        self.assertTrue(e["request"]["cookies"][0]["value"].startswith("<redacted"))

    def test_a_recording_that_will_not_parse_is_not_stored(self):
        from runtime import scrub
        self.assertIsNone(scrub.har_bytes(b"<html>not json</html>"))
        self.assertIsNone(scrub.har_bytes(b'{"json": true, "har": false}'))

    def test_the_runner_stores_the_masked_bytes_and_never_the_raw_ones(self):
        import os
        import tempfile
        from unittest import mock
        from runtime import _sandbox_runner, capability
        path = os.path.join(tempfile.mkdtemp(), "traffic.har")
        with open(path, "w") as f:
            json.dump(self._har(), f)
        stored = {}

        def fake_write(name, data):
            stored["name"], stored["data"] = name, data
            return "blob:fake"
        with mock.patch.object(capability, "har_recording", return_value=path), \
             mock.patch.object(capability, "write_file", fake_write):
            ref = _sandbox_runner._store_har()
        self.assertEqual(ref, "blob:fake")
        self.assertEqual(stored["name"], "browser-traffic.har")
        self.assertNotIn("SEKRIT", stored["data"].decode())

    def test_an_unreadable_recording_stores_nothing_at_all(self):
        import os
        import tempfile
        from unittest import mock
        from runtime import _sandbox_runner, capability
        path = os.path.join(tempfile.mkdtemp(), "traffic.har")
        with open(path, "w") as f:
            f.write("Set-Cookie: session=SEKRIT")
        calls = []
        with mock.patch.object(capability, "har_recording", return_value=path), \
             mock.patch.object(capability, "write_file",
                               lambda *a: calls.append(a) or "blob:no"):
            self.assertEqual(_sandbox_runner._store_har(), "")
        self.assertEqual(calls, [], "nothing we cannot read may be stored")

class TheHarnessOwnsTheWindowTest(unittest.TestCase):
    def test_the_window_is_no_longer_the_steps_to_open(self):
        from pathlib import Path as _P
        src = _P(__file__).resolve().parent.parent / "backend" / "runtime" / "_sandbox_runner.py"
        text = src.read_text()
        surface = text[text.index('surface = {'):text.index('ns = dict(surface)')]
        self.assertNotIn("browser_page", surface,
                         "the step surface no longer carries the window")
        self.assertIn("browser_goto", surface, "the page work is still the step's")

    def test_the_checks_run_before_the_window_closes(self):
        from pathlib import Path as _P
        text = (_P(__file__).resolve().parent.parent / "backend" / "runtime"
                / "_sandbox_runner.py").read_text()
        checked = text.index("_keep_if_the_outputs_are_wrong(job.get(")
        closed = text.index("stack.close()", checked)
        self.assertLess(checked, closed)

    def test_wrong_outputs_keep_the_recording_without_deciding_anything(self):
        from unittest import mock
        from runtime import _sandbox_runner, capability
        ports = [{"name": "count", "type": "number"}]
        out = {"count": "not a number"}
        with mock.patch.object(capability, "window_opened", return_value=True), \
             mock.patch.object(_sandbox_runner, "_stored_captures",
                               return_value={"har": "blob:har"}):
            _sandbox_runner._keep_if_the_outputs_are_wrong(ports, out)
        self.assertEqual(out["$capture"]["har"], "blob:har")
        self.assertEqual(out["count"], "not a number",
                         "the outputs come back untouched - the parent gives the verdict")

    def test_outputs_that_pass_keep_nothing(self):
        from unittest import mock
        from runtime import _sandbox_runner, capability
        ports = [{"name": "count", "type": "number"}]
        out = {"count": 2}
        with mock.patch.object(capability, "window_opened", return_value=True), \
             mock.patch.object(_sandbox_runner, "_stored_captures",
                               return_value={"har": "blob:har"}):
            _sandbox_runner._keep_if_the_outputs_are_wrong(ports, out)
        self.assertNotIn("$capture", out)

    def test_a_browser_step_asks_the_sandbox_for_its_window(self):
        from unittest import mock
        from runtime import sandbox
        seen = {}

        def fake(code, entry, inputs, secrets, **kw):
            seen.update(kw)
            return {}
        with mock.patch.object(sandbox, "_run_subprocess", fake):
            sandbox.run("x", None, {}, {}, browser=True,
                        browser_url="https://example.test/inbox",
                        output_ports=[{"name": "rows", "type": "list"}])
        self.assertTrue(seen["browser"])
        self.assertEqual(seen["browser_url"], "https://example.test/inbox")
        self.assertEqual(seen["output_ports"], [{"name": "rows", "type": "list"}])
