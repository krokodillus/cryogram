# Tests: provider auth seams (offline): the claude-subscription auth type powers the builder only - node ai_call refuses it, is_ready excludes it, and the master AI feeds the SDK the right env var
from __future__ import annotations

import unittest

from tests import _bootstrap

from agent import orchestrator
import providers
from storage import secrets_store
from storage import settings

def _register(auth: str | None, key_name: str = "AUTH_TEST_KEY",
              model: str = "claude-test-model") -> None:
    provider = {"adapter": "anthropic", "endpoint": "", "key_name": key_name,
                "tags": [], "models": [{"name": model}]}
    if auth:
        provider["auth"] = auth
    settings.update({"providers": [provider], "master_ai": {"model": model}})
    secrets_store.set_secret(key_name, "tok-or-key-value", secrets_store.OWNER_APP)

class AuthTypeTest(unittest.TestCase):
    def test_default_is_api_key(self):
        self.assertEqual(providers.auth_type({}), "api-key")
        self.assertEqual(providers.auth_type({"auth": ""}), "api-key")

    def test_resolve_carries_auth(self):
        _register("claude-subscription")
        self.assertEqual(providers.resolve({"model": "claude-test-model"})
                         .get("key_name"), "")
        _register("api-key")
        self.assertEqual(providers.resolve({"model": "claude-test-model"})["auth"],
                         "api-key")

    def test_subscription_is_never_node_ready(self):
        _register("claude-subscription")
        self.assertFalse(providers.is_ready({"model": "claude-test-model"}))
        _register("api-key")
        self.assertTrue(providers.is_ready({"model": "claude-test-model"}))

    def test_node_call_refuses_subscription(self):
        _register("claude-subscription")
        r = providers.call({"model": "claude-test-model"}, "prompt", {"a": 1})
        self.assertIn("auth error", r.get("text", ""))
        self.assertIn("API key", r["text"])

class NodePreferenceTest(unittest.TestCase):
    def test_node_lookup_prefers_api_key_owner_of_same_model(self):
        settings.update({"providers": [
            {"adapter": "anthropic", "auth": "claude-subscription", "use": "builder",
             "key_name": "SUB_KEY", "tags": [], "models": [{"name": "shared-model"}]},
            {"adapter": "anthropic", "auth": "api-key", "use": "workflow",
             "key_name": "WF_KEY", "tags": [], "models": [{"name": "shared-model"}]},
        ]})
        secrets_store.set_secret("WF_KEY", "wf-key-value", secrets_store.OWNER_APP)
        self.assertTrue(providers.is_ready({"model": "shared-model"}))
        self.assertEqual(providers.resolve({"model": "shared-model"})["auth"], "api-key")

        self.assertEqual(providers.provider_for_model("shared-model")["key_name"], "SUB_KEY")

    def test_master_lookup_prefers_builder_regardless_of_list_order(self):
        settings.update({"providers": [
            {"adapter": "anthropic", "auth": "api-key", "use": "workflow",
             "key_name": "WF_KEY2", "tags": [], "models": [{"name": "shared-2"}]},
            {"adapter": "anthropic", "auth": "claude-subscription", "use": "builder",
             "key_name": "SUB_KEY2", "tags": [], "models": [{"name": "shared-2"}]},
        ]})
        self.assertEqual(providers.provider_for_model("shared-2")["key_name"],
                         "SUB_KEY2")

        self.assertEqual(
            providers.find_model("shared-2", for_node=True)[0]["key_name"], "WF_KEY2")

class MasterAuthTest(unittest.TestCase):
    def test_api_key_feeds_anthropic_env_var(self):
        _register("api-key")
        value, key_name, env_var = orchestrator._master_auth()
        self.assertEqual((value, key_name, env_var),
                         ("tok-or-key-value", "AUTH_TEST_KEY", "ANTHROPIC_API_KEY"))

    def test_subscription_feeds_oauth_env_var(self):
        _register("claude-subscription")
        value, key_name, env_var = orchestrator._master_auth()
        self.assertEqual(env_var, "CLAUDE_CODE_OAUTH_TOKEN")
        self.assertEqual(value, "tok-or-key-value")

    def test_the_sign_in_route_answers_from_the_connection_check(self):
        from unittest import mock
        from agent import claude_code
        _register("claude-subscription")
        with mock.patch.object(claude_code, "connected_hint", lambda fresh=False: True):
            self.assertEqual(orchestrator._master_key(), ("claude", ""))
        with mock.patch.object(claude_code, "connected_hint", lambda fresh=False: False):
            self.assertEqual(orchestrator._master_key(), (None, ""))

class ErrorKindTest(unittest.TestCase):
    def _http(self, code):
        import io
        import urllib.error
        return urllib.error.HTTPError("u", code, "err", {},
                                      io.BytesIO(b"body"))

    def test_kinds(self):
        import urllib.error

        import providers
        self.assertEqual(providers._err_kind(self._http(401)), "auth")
        self.assertEqual(providers._err_kind(self._http(403)), "auth")
        self.assertEqual(providers._err_kind(self._http(429)), "http-429")
        self.assertEqual(providers._err_kind(self._http(529)), "http-529")
        self.assertEqual(providers._err_kind(TimeoutError("t")), "timeout")
        self.assertEqual(
            providers._err_kind(urllib.error.URLError(TimeoutError("t"))),
            "timeout")
        self.assertEqual(providers._err_kind(ConnectionError("x")), "transport")

    def test_timeout_text_is_plain(self):
        import providers
        self.assertIn("timed out waiting for the model",
                      providers._err_text(TimeoutError("t")))

    def test_subscription_refusal_is_auth_kind(self):
        _register("claude-subscription")
        res = providers.call({"model": "claude-test-model"}, "p", {})
        self.assertEqual(res.get("error_kind"), "auth")

class TokenCeilingTest(unittest.TestCase):
    def test_clamp_is_the_one_normaliser(self):
        import config
        d = config.AI_MAX_TOKENS
        self.assertEqual(providers.clamp_tokens(None), d)
        self.assertEqual(providers.clamp_tokens(""), d)
        self.assertEqual(providers.clamp_tokens("abc"), d)
        self.assertEqual(providers.clamp_tokens(0), d)
        self.assertEqual(providers.clamp_tokens(10_000_000), 10_000_000)
        self.assertEqual(providers.clamp_tokens(4096), 4096)
        self.assertEqual(providers.clamp_tokens("8192"), 8192)

    def test_the_default_is_generous(self):
        import config

        self.assertGreaterEqual(config.AI_MAX_TOKENS, 16_384)

    def test_a_truncated_structured_answer_is_never_presented_as_one(self):
        import json
        import providers as P
        payload = {"stop_reason": "max_tokens",
                   "content": [{"type": "tool_use", "name": "emit_output",
                                "input": {"summary": "half an ans"}}]}

        class _Resp:
            def read(self): return json.dumps(payload).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        orig = P.urllib.request.urlopen
        P.urllib.request.urlopen = lambda *a, **k: _Resp()
        try:
            res = P.anthropic_structured("m", [], "k", {"type": "object"})
        finally:
            P.urllib.request.urlopen = orig
        self.assertEqual(res.get("error_kind"), "max-tokens")
        self.assertNotIn("structured", res)
        self.assertIn("cut off", res["text"])

    def test_a_complete_structured_answer_still_comes_through(self):
        import json
        import providers as P
        payload = {"stop_reason": "tool_use",
                   "content": [{"type": "tool_use", "name": "emit_output",
                                "input": {"summary": "done", "triaged": []}}]}

        class _Resp:
            def read(self): return json.dumps(payload).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        orig = P.urllib.request.urlopen
        P.urllib.request.urlopen = lambda *a, **k: _Resp()
        try:
            res = P.anthropic_structured("m", [], "k", {"type": "object"})
        finally:
            P.urllib.request.urlopen = orig
        self.assertEqual(res["structured"]["summary"], "done")

    def test_openai_finish_reason_length_is_the_same_failure(self):
        import json
        import providers as P
        payload = {"choices": [{"finish_reason": "length",
                                "message": {"content": '{"partial":'}}]}

        class _Resp:
            def read(self): return json.dumps(payload).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        orig = P.urllib.request.urlopen
        P.urllib.request.urlopen = lambda *a, **k: _Resp()
        try:
            res = P.openai_structured("m", [], "k", {"type": "object"})
        finally:
            P.urllib.request.urlopen = orig
        self.assertEqual(res.get("error_kind"), "max-tokens")
        self.assertNotIn("structured", res)

class TemperatureTest(unittest.TestCase):
    def test_clamp_is_deterministic_first(self):
        self.assertEqual(providers.clamp_temperature(None), 0.0)
        self.assertEqual(providers.clamp_temperature("abc"), 0.0)
        self.assertEqual(providers.clamp_temperature(0.3), 0.3)
        self.assertEqual(providers.clamp_temperature(2.5), 1.0)
        self.assertEqual(providers.clamp_temperature(-1), 0.0)

    def test_resolve_carries_temperature(self):
        _register("api-key")
        prof = providers.resolve({"model": "claude-test-model",
                                  "temperature": 0.7})
        self.assertEqual(prof["temperature"], 0.7)
        self.assertEqual(providers.resolve({"model": "claude-test-model"})["temperature"], 0.0)

    def test_anthropic_payload_carries_temperature(self):
        import json as _json
        import providers as P
        seen = {}

        class _Resp:
            def read(self):
                return _json.dumps({"stop_reason": "tool_use", "content": [
                    {"type": "tool_use", "input": {"v": 1}}]}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        orig = P.urllib.request.urlopen
        def fake(req, **k):
            seen.update(_json.loads(req.data))
            return _Resp()
        P.urllib.request.urlopen = fake
        try:
            P.anthropic_structured("m", [], "k", {"type": "object"},
                                   temperature=0.4)
        finally:
            P.urllib.request.urlopen = orig
        self.assertEqual(seen.get("temperature"), 0.4)

    def test_openai_payload_uses_current_token_key(self):
        import json as _json
        import providers as P
        seen = {}

        class _Resp:
            def read(self):
                return _json.dumps({"choices": [{
                    "finish_reason": "stop",
                    "message": {"content": "{}"}}]}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        orig = P.urllib.request.urlopen
        def fake(req, **k):
            seen.update(_json.loads(req.data))
            return _Resp()
        P.urllib.request.urlopen = fake
        try:
            P.openai_structured("m", [], "k", {"type": "object"},
                                max_tokens=4096, temperature=0.2)
        finally:
            P.urllib.request.urlopen = orig
        self.assertEqual(seen.get("max_completion_tokens"), 4096)
        self.assertNotIn("max_tokens", seen)
        self.assertEqual(seen.get("temperature"), 0.2)

if __name__ == "__main__":
    unittest.main()
