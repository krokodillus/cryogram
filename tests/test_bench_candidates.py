import io
import re
import unittest
import urllib.error
import urllib.request
from email.utils import formatdate
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent / "bench/versions"
REF = "backend/agent/skills/agent/61-connector-practice/resilient-request.md"

def helper(version):
    source = (ROOT / version / REF).read_text()
    code = re.search(r"```python\n(.*?)\n```", source, re.S).group(1)
    namespace = {"heartbeat": lambda *_: None}
    exec(compile(code, str(ROOT / version / REF), "exec"), namespace)
    return namespace

@unittest.skipUnless((ROOT / "harness-v2" / REF).exists(), "create managed candidate versions first")
class RequestRevisionTest(unittest.TestCase):
    def test_baseline_retries_ambiguous_write_but_candidates_do_not(self):
        req = urllib.request.Request("https://example.invalid/write", data=b"{}", method="POST")
        for version, expected in (("baseline", 3), ("skills-v1", 1), ("harness-v2", 1)):
            with self.subTest(version=version), patch("urllib.request.urlopen", side_effect=TimeoutError) as call, patch("time.sleep"):
                with self.assertRaises(TimeoutError):
                    helper(version)["request_with_retry"](req)
                self.assertEqual(call.call_count, expected)

    def test_safe_read_retries_and_returns_body(self):
        response = io.BytesIO(b"ok")
        with patch("urllib.request.urlopen", side_effect=[TimeoutError(), response]) as call, patch("time.sleep"):
            got = helper("skills-v1")["request_with_retry"](urllib.request.Request("https://example.invalid/read"))
        self.assertEqual(got, b"ok")
        self.assertEqual(call.call_count, 2)

    def test_non_transient_and_egress_errors_are_not_retried(self):
        for error in (urllib.error.HTTPError("https://example.invalid", 403, "forbidden", {}, io.BytesIO(b"no")),
                      urllib.error.URLError("egress blocked")):
            with patch("urllib.request.urlopen", side_effect=error) as call:
                with self.assertRaises((RuntimeError, urllib.error.URLError)):
                    helper("skills-v1")["request_with_retry"](urllib.request.Request("https://example.invalid"))
                self.assertEqual(call.call_count, 1)

    def test_retry_after_dates_invalid_headers_and_long_wait(self):
        h = helper("skills-v1")
        with patch("time.time", return_value=1000):
            self.assertEqual(h["_retry_after"](formatdate(1010, usegmt=True), 1), 10)
            self.assertEqual(h["_retry_after"]("invalid", 3), 3)
        error = urllib.error.HTTPError("https://example.invalid", 429, "slow", {"Retry-After": "600"}, io.BytesIO())
        with patch("urllib.request.urlopen", side_effect=error) as call, patch("time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "retry later"):
                h["request_with_retry"](urllib.request.Request("https://example.invalid"))
            self.assertEqual(call.call_count, 1)
            sleep.assert_not_called()

    def test_unsafe_explicit_retry_is_rejected_before_request(self):
        with patch("urllib.request.urlopen") as call:
            with self.assertRaises(ValueError):
                helper("skills-v1")["request_with_retry"](urllib.request.Request("https://example.invalid", data=b"x"), tries=3)
            call.assert_not_called()

if __name__ == "__main__":
    unittest.main()
