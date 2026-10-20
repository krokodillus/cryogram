import ast
import re
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from tests import _bootstrap
from agent import skills
from runtime import contracts

def examples(sid):
    return re.findall(r"```python\n(.*?)```", skills.read_static(sid)["content"], re.S)

class PromptExamplesTest(unittest.TestCase):
    def test_python_examples_compile_and_use_current_injected_signatures(self):
        found = 0
        for path in skills.STATIC_DIR.rglob("*.md"):
            for code in re.findall(r"```python\n(.*?)```", path.read_text(), re.S):
                with self.subTest(path=str(path)):
                    tree = ast.parse(code)
                    self.assertEqual(contracts.check_calls(tree), [])
                    found += 1
        self.assertGreater(found, 3)

    def _loop(self, items, saved, send):
        outputs = {}
        ns = {"read_input": lambda name: items if name == "items" else 60,
              "checkpoints": lambda: dict(saved), "checkpoint": saved.__setitem__,
              "checkpoint_values": lambda keys=None: [saved[k] for k in (keys if keys is not None else saved) if k in saved],
              "send_one": send, "heartbeat": lambda message: None,
              "write_output": outputs.__setitem__}
        with patch("time.sleep"):
            exec(examples("browser/api-routes")[1], ns)
        return outputs

    def test_loop_rejects_duplicate_identity_before_any_send(self):
        sent = []
        with self.assertRaisesRegex(ValueError, "not unique"):
            self._loop([{"id": "same"}, {"id": "same"}], {}, lambda item: sent.append(item))
        self.assertEqual(sent, [])

    def test_loop_resumes_current_items_without_returning_old_unrelated_items(self):
        saved = {"old": {"id": "old"}, "a": {"id": "a"}}
        sent = []
        def send(item):
            sent.append(item["id"])
            return {"id": item["id"], "status": "sent"}
        out = self._loop([{"id": "a"}, {"id": "b"}], saved, send)
        self.assertEqual(sent, ["b"])
        self.assertEqual([r["id"] for r in out["sent"]], ["a", "b"])

    def test_loop_propagates_ambiguous_send_without_checkpoint_or_repeat(self):
        attempts = []
        saved = {}
        def send(item):
            attempts.append(item["id"])
            raise TimeoutError("acceptance unknown")
        with self.assertRaises(TimeoutError):
            self._loop([{"id": "a"}], saved, send)
        self.assertEqual(attempts, ["a"])
        self.assertEqual(saved, {})

    def _request_helper(self):
        ns = {"heartbeat": lambda message: None}
        exec(examples("61-connector-practice/resilient-request")[0], ns)
        return ns["request_with_retry"]

    def test_http_helper_never_retries_ordinary_post(self):
        helper = self._request_helper()
        req = urllib.request.Request("https://example.invalid", data=b"{}", method="POST")
        with patch("urllib.request.urlopen", side_effect=TimeoutError("unknown")) as call:
            with self.assertRaises(TimeoutError):
                helper(req)
        self.assertEqual(call.call_count, 1)
        with self.assertRaisesRegex(ValueError, "idempotency"):
            helper(req, tries=2)

    def test_http_helper_respects_a_retry_after_beyond_its_wait_budget(self):
        helper = self._request_helper()
        req = urllib.request.Request("https://example.invalid")
        err = urllib.error.HTTPError(req.full_url, 429, "wait", {"Retry-After": "120"}, None)
        with patch("urllib.request.urlopen", side_effect=err) as call, patch("time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "120s"):
                helper(req, max_wait=60)
        self.assertEqual(call.call_count, 1)
        sleep.assert_not_called()

if __name__ == "__main__":
    unittest.main()
