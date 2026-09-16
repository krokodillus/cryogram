# Tests: provider registry v2 (offline): the enabled switch, the HARD builder/workflow boundary for node lookups (a model listed only under the builder is missing for steps), the run-start registry pin, per-provider usage counts, id backfill and the minimal key check
from __future__ import annotations

import json
import unittest
from unittest import mock

from tests import _bootstrap

import providers
from storage import node_stats
from storage import secrets_store
from storage import settings
from storage import store

def _wf(pid: str, model: str, key_name: str = "WF_REG_KEY",
        enabled=None, **extra) -> dict:
    p = {"id": pid, "name": pid, "adapter": "anthropic", "auth": "api-key",
         "use": "workflow", "endpoint": "", "key_name": key_name, "tags": [],
         "models": [{"name": model}]}
    if enabled is not None:
        p["enabled"] = enabled
    p.update(extra)
    return p

class EnabledSwitchTest(unittest.TestCase):
    def test_absent_means_enabled(self):
        self.assertTrue(providers.is_enabled({}))
        self.assertTrue(providers.is_enabled({"enabled": True}))
        self.assertFalse(providers.is_enabled({"enabled": False}))

    def test_disabled_provider_is_invisible_to_nodes(self):
        settings.update({"providers": [_wf("p_off", "model-off", enabled=False)]})
        secrets_store.set_secret("WF_REG_KEY", "k", secrets_store.OWNER_APP)
        self.assertEqual(providers.find_model("model-off", for_node=True), ({}, {}))
        self.assertFalse(providers.is_ready({"model": "model-off"}))
        self.assertNotIn("model-off", [m["name"] for m in providers.node_models()])

    def test_builder_lookup_ignores_enabled(self):
        settings.update({"providers": [
            {"id": "builder", "adapter": "anthropic", "auth": "api-key",
             "use": "builder", "enabled": False, "key_name": "B_KEY",
             "tags": [], "models": [{"name": "builder-model"}]}]})
        self.assertEqual(providers.provider_for_model("builder-model")
                         .get("key_name"), "B_KEY")

class BuilderBoundaryTest(unittest.TestCase):
    def test_builder_only_model_is_missing_for_nodes(self):
        settings.update({"providers": [
            {"id": "builder", "adapter": "anthropic", "auth": "api-key",
             "use": "builder", "key_name": "B_KEY2", "tags": [],
             "models": [{"name": "only-on-builder"}]}]})
        secrets_store.set_secret("B_KEY2", "k", secrets_store.OWNER_APP)
        self.assertEqual(providers.find_model("only-on-builder", for_node=True),
                         ({}, {}))
        self.assertFalse(providers.is_ready({"model": "only-on-builder"}))
        self.assertEqual([m for m in providers.node_models()
                          if m["name"] == "only-on-builder"], [])

    def test_workflow_owner_wins_for_nodes_whatever_the_order(self):
        settings.update({"providers": [
            {"id": "builder", "adapter": "anthropic", "auth": "api-key",
             "use": "builder", "key_name": "B_KEY3", "tags": [],
             "models": [{"name": "shared-reg"}]},
            _wf("p_wf3", "shared-reg", key_name="WF_KEY3")]})
        self.assertEqual(
            providers.find_model("shared-reg", for_node=True)[0]["key_name"],
            "WF_KEY3")

        self.assertEqual(providers.provider_for_model("shared-reg")["key_name"],
                         "B_KEY3")

class ModelStateTest(unittest.TestCase):
    def test_the_four_states(self):
        settings.update({"providers": [
            {"id": "builder", "adapter": "anthropic", "auth": "api-key",
             "use": "builder", "key_name": "B_ST", "tags": [],
             "models": [{"name": "builder-only-model"}]},
            _wf("p_st_ready", "st-ready", key_name="ST_READY_KEY"),
            _wf("p_st_nokey", "st-nokey", key_name="ST_NOKEY_KEY"),
            _wf("p_st_off", "st-off", key_name="ST_OFF_KEY", enabled=False)]})
        secrets_store.set_secret("ST_READY_KEY", "k", secrets_store.OWNER_APP)
        secrets_store.delete_secret("ST_NOKEY_KEY", secrets_store.OWNER_APP)
        self.assertEqual(providers.node_model_state("st-ready")[0], "ready")
        state, prov = providers.node_model_state("st-nokey")
        self.assertEqual((state, prov["id"]), ("no-key", "p_st_nokey"))
        state, prov = providers.node_model_state("st-off")
        self.assertEqual((state, prov["id"]), ("disabled", "p_st_off"))

        self.assertEqual(providers.node_model_state("builder-only-model")[0],
                         "missing")
        self.assertEqual(providers.node_model_state("never-heard-of-it")[0],
                         "missing")

    def test_offered_is_not_usable(self):
        settings.update({"providers": [_wf("p_st2", "st2-model",
                                           key_name="ST2_KEY")]})
        secrets_store.delete_secret("ST2_KEY", secrets_store.OWNER_APP)
        self.assertTrue(providers.node_models())
        self.assertFalse(providers.any_node_model_ready())
        secrets_store.set_secret("ST2_KEY", "k", secrets_store.OWNER_APP)
        self.assertTrue(providers.any_node_model_ready())

class PinnedRegistryTest(unittest.TestCase):
    def test_pin_survives_a_mid_run_disable(self):
        settings.update({"providers": [_wf("p_pin", "pin-model",
                                           key_name="PIN_KEY")]})
        secrets_store.set_secret("PIN_KEY", "k", secrets_store.OWNER_APP)
        with providers.pinned_registry():
            self.assertTrue(providers.is_ready({"model": "pin-model"}))
            settings.update({"providers": [_wf("p_pin", "pin-model",
                                               key_name="PIN_KEY",
                                               enabled=False)]})

            self.assertTrue(providers.is_ready({"model": "pin-model"}))

        self.assertFalse(providers.is_ready({"model": "pin-model"}))

    def test_pin_is_reentrant(self):
        settings.update({"providers": [_wf("p_pin2", "pin-model-2",
                                           key_name="PIN_KEY2")]})
        with providers.pinned_registry():
            settings.update({"providers": []})
            with providers.pinned_registry():
                self.assertTrue(
                    providers.find_model("pin-model-2", for_node=True)[1])
            self.assertTrue(
                providers.find_model("pin-model-2", for_node=True)[1])
        self.assertEqual(providers.find_model("pin-model-2", for_node=True),
                         ({}, {}))

class UsageCountTest(unittest.TestCase):
    def test_counts_workflows_and_nodes_per_provider(self):
        settings.update({"providers": [
            {"id": "builder", "adapter": "anthropic", "auth": "api-key",
             "use": "builder", "key_name": "B_USE", "tags": [],
             "models": [{"name": "usage-model-a"}]},
            _wf("p_use_a", "usage-model-a", key_name="USE_A"),
            _wf("p_use_b", "usage-model-b", key_name="USE_B", enabled=False)]})

        def ai(nid, model):
            return {"id": nid, "name": nid, "type": "ai",
                    "config": {"model": {"model": model}},
                    "inputs": [], "outputs": [], "tests": []}
        store.save({"id": "p_usage_one", "nodes": [
            ai("n1", "usage-model-a"), ai("n2", "usage-model-a"),
            ai("n3", "usage-model-b"),
            {"id": "n4", "type": "code", "config": {"code": "x=1"},
             "inputs": [], "outputs": [], "tests": []}],
            "edges": [], "variables": [], "chat": []})
        store.save({"id": "p_usage_two", "nodes": [ai("n5", "usage-model-a")],
                    "edges": [], "variables": [], "chat": []})

        usage = node_stats.provider_usage()
        self.assertEqual(usage["p_use_a"], {"workflows": 2, "nodes": 3})

        self.assertEqual(usage["p_use_b"], {"workflows": 1, "nodes": 1})

        self.assertNotIn("builder", usage)
        store.delete_workflow("p_usage_one")
        store.delete_workflow("p_usage_two")

class ProviderIdBackfillTest(unittest.TestCase):
    def test_update_mints_missing_ids_and_keeps_existing(self):
        s = settings.update({"providers": [
            {"id": "keep-me", "adapter": "anthropic", "models": []},
            {"adapter": "openai", "models": []}]})
        ids = [p.get("id") for p in s["providers"]]
        self.assertEqual(ids[0], "keep-me")
        self.assertTrue(ids[1].startswith("p_"))
        self.assertEqual(len(set(ids)), 2)

class KeyCheckTest(unittest.TestCase):
    def test_empty_key_fails_fast_without_network(self):
        def boom(*a, **k):
            raise AssertionError("no network call on an empty key")
        with mock.patch.object(providers.urllib.request, "urlopen", boom):
            r = providers.check_key("anthropic", "", "m", "")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_kind"], "auth")

    def _http(self, code):
        import io
        import urllib.error
        return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b"body"))

    def test_auth_rejection_is_the_only_hard_failure(self):
        with mock.patch.object(providers.urllib.request, "urlopen",
                               mock.Mock(side_effect=self._http(401))):
            r = providers.check_key("anthropic", "", "m", "sk-bad")
        self.assertEqual((r["ok"], r["error_kind"]), (False, "auth"))

        with mock.patch.object(providers.urllib.request, "urlopen",
                               mock.Mock(side_effect=self._http(404))):
            r = providers.check_key("openai", "", "gone-model", "sk-ok")
        self.assertTrue(r["ok"])
        self.assertIn("404", r.get("note", ""))

    def test_transport_failure_is_reported_not_raised(self):
        with mock.patch.object(providers.urllib.request, "urlopen",
                               mock.Mock(side_effect=TimeoutError("t"))):
            r = providers.check_key("anthropic", "", "m", "sk")
        self.assertEqual((r["ok"], r["error_kind"]), (False, "timeout"))

    def test_check_payload_is_minimal(self):
        seen = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{}"

        def fake(req, **k):
            seen.update(json.loads(req.data))
            return _Resp()
        with mock.patch.object(providers.urllib.request, "urlopen", fake):
            providers.check_key("anthropic", "", "m", "sk")
        self.assertEqual(seen.get("max_tokens"), providers.KEY_CHECK_MAX_TOKENS)
        self.assertLessEqual(providers.KEY_CHECK_MAX_TOKENS, 64)

class ListModelsTest(unittest.TestCase):
    def test_list_models_needs_a_key_unless_local(self):
        settings.update({"providers": [_wf("p_lm", "m", key_name="LM_KEY")]})
        secrets_store.delete_secret("LM_KEY", secrets_store.OWNER_APP)

        def boom(*a, **k):
            raise AssertionError("no network call without a key")
        with mock.patch.object(providers.urllib.request, "urlopen", boom):
            r = providers.list_models({"adapter": "openai", "key_name": "LM_KEY"})
        self.assertEqual((r["ok"], r["error_kind"]), (False, "auth"))

    def test_a_custom_gemini_card_speaks_gemini_and_lists_or_says_why_not(self):
        from storage import model_catalog
        row = {"id": "p_gai", "adapter": "gemini", "key_name": "LM_KEY3",
               "endpoint": "https://aiplatform.googleapis.com/v1/publishers/google"}
        self.assertEqual(model_catalog.catalog_key(row), "gemini")
        self.assertEqual(model_catalog.facts(row, "gemini-3.5-flash")["reads"], ["pdf", "image"])
        secrets_store.set_secret("LM_KEY3", "k", secrets_store.OWNER_APP)
        r = providers.list_models(row)
        self.assertFalse(r["ok"])
        self.assertIn("Add the models by name", r["message"])
        seen = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps({"models": [{"name": "models/gemini-3.5-flash"},
                                              {"name": "models/gemini-3.1-pro-preview"}]}).encode()

        def fake(req, **k):
            seen["url"] = req.full_url
            seen["key"] = req.headers.get("X-goog-api-key", "")
            return _Resp()
        with mock.patch.object(providers.urllib.request, "urlopen", fake):
            r = providers.list_models({**row, "endpoint": ""})
        self.assertEqual(r["models"], ["gemini-3.1-pro-preview", "gemini-3.5-flash"])
        self.assertIn("generativelanguage.googleapis.com/v1beta/models", seen["url"])
        self.assertEqual(seen["key"], "k")

    def test_list_models_parses_the_openai_shape(self):
        secrets_store.set_secret("LM_KEY2", "sk", secrets_store.OWNER_APP)
        seen = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                return json.dumps({"data": [{"id": "model-b"},
                                            {"id": "model-a"},
                                            {"id": "model-a"},

                                            {"id": "models/gemini-3.5-flash"}]}).encode()

        def fake(req, **k):
            seen["url"] = req.full_url
            seen["auth"] = req.headers.get("Authorization", "")
            return _Resp()
        with mock.patch.object(providers.urllib.request, "urlopen", fake):
            r = providers.list_models({"adapter": "openai", "key_name": "LM_KEY2",
                                       "endpoint": "https://api.mistral.ai/v1"})
        self.assertTrue(r["ok"])
        self.assertIn("gemini-3.5-flash", r["models"])
        self.assertNotIn("models/gemini-3.5-flash", r["models"])
        self.assertEqual(r["models"], ["gemini-3.5-flash", "model-a", "model-b"])
        self.assertEqual(seen["url"], "https://api.mistral.ai/v1/models")
        self.assertIn("sk", seen["auth"])

    def test_list_models_anthropic_path_and_failure(self):
        secrets_store.set_secret("LM_KEY3", "sk", secrets_store.OWNER_APP)
        seen = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({"data": [{"id": "claude-z"}]}).encode()

        def fake(req, **k):
            seen["url"] = req.full_url
            return _Resp()
        with mock.patch.object(providers.urllib.request, "urlopen", fake):
            r = providers.list_models({"adapter": "anthropic",
                                       "key_name": "LM_KEY3"})
        self.assertEqual(r["models"], ["claude-z"])
        self.assertEqual(seen["url"], "https://api.anthropic.com/v1/models")
        with mock.patch.object(providers.urllib.request, "urlopen",
                               mock.Mock(side_effect=TimeoutError("t"))):
            r = providers.list_models({"adapter": "anthropic",
                                       "key_name": "LM_KEY3"})
        self.assertEqual((r["ok"], r["error_kind"]), (False, "timeout"))

class TokenUsageTest(unittest.TestCase):
    def _resp(self, payload):
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(payload).encode()
        return _Resp()

    def test_anthropic_usage_rides_the_result(self):
        settings.update({"providers": [_wf("p_tok", "tok-model",
                                           key_name="TOK_KEY")]})
        secrets_store.set_secret("TOK_KEY", "k", secrets_store.OWNER_APP)
        payload = {"stop_reason": "tool_use",
                   "usage": {"input_tokens": 120, "output_tokens": 45},
                   "content": [{"type": "tool_use", "input": {"v": 1}}]}
        with mock.patch.object(providers.urllib.request, "urlopen",
                               lambda *a, **k: self._resp(payload)):
            res = providers.call({"model": "tok-model"}, "p", {},
                                 output_schema={"type": "object"})
        self.assertEqual(res["usage"], {"in": 120, "out": 45})

    def test_openai_usage_shape_and_sideband(self):
        from runtime import capability
        settings.update({"providers": [_wf("p_tok2", "tok-model-2",
                                           key_name="TOK_KEY2",
                                           adapter="openai")]})
        secrets_store.set_secret("TOK_KEY2", "k", secrets_store.OWNER_APP)
        payload = {"choices": [{"finish_reason": "stop",
                                "message": {"content": "{\"a\": 1}"}}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
        with mock.patch.object(providers.urllib.request, "urlopen",
                               lambda *a, **k: self._resp(payload)):
            capability.ai_call("p", {"model": "tok-model-2"}, {},
                               output_ports=[{"name": "a", "type": "number"}])
        self.assertEqual(capability.take_ai_usage(), {"in": 10, "out": 3})
        self.assertIsNone(capability.take_ai_usage())

    def test_ai_usage_ledger_add_and_aggregate(self):
        from storage import db
        db.ai_usage_add(provider_id="p_led", model="m", workflow_id="proj_led",
                        run_id="r1", node_id="n1", tokens_in=100,
                        tokens_out=20, source="run")
        db.ai_usage_add(provider_id="p_led", model="m", workflow_id="proj_led",
                        run_id="", node_id="step", tokens_in=50,
                        tokens_out=5, source="build")
        db.ai_usage_add(provider_id="builder", model="claude-x",
                        workflow_id="proj_led", run_id="", node_id="",
                        tokens_in=1000, tokens_out=200, source="builder")
        agg = db.ai_usage_by_provider(0)
        self.assertEqual(agg["p_led"], {"in": 150, "out": 25})
        proj = db.ai_usage_workflow("proj_led")
        self.assertEqual(proj["builder"], {"in": 1000, "out": 200, "calls": 1})
        self.assertEqual(proj["run"], {"in": 100, "out": 20, "calls": 1})
        self.assertEqual(proj["build"], {"in": 50, "out": 5, "calls": 1})

        import time as _t
        self.assertEqual(db.ai_usage_by_provider(_t.time() + 60), {})

if __name__ == "__main__":
    unittest.main()

class ProviderChoiceTest(unittest.TestCase):
    def setUp(self):
        settings.update({"providers": [
            _wf("p_one", "shared-model", key_name="ONE_KEY", name="Provider One"),
            _wf("p_two", "shared-model", key_name="TWO_KEY", name="Provider Two"),
            _wf("p_three", "only-here", key_name="THREE_KEY", name="Provider Three"),
            _wf("p_off", "shared-model", key_name="OFF_KEY", name="Switched Off",
                enabled=False)]})
        for k in ("ONE_KEY", "TWO_KEY", "THREE_KEY"):
            secrets_store.set_secret(k, "k", secrets_store.OWNER_APP)

    def test_a_provider_id_picks_that_provider(self):
        self.assertEqual(providers.find_model("shared-model", for_node=True)[0]["id"], "p_one")
        self.assertEqual(providers.find_model("shared-model", for_node=True,
                                             provider_id="p_two")[0]["id"], "p_two")

        self.assertEqual(providers.find_model("shared-model", for_node=True,
                                             provider_id="p_three"), ({}, {}))
        self.assertTrue(providers.is_ready({"model": "shared-model", "provider_id": "p_two"}))
        self.assertFalse(providers.is_ready({"model": "shared-model", "provider_id": "p_three"}))
        self.assertEqual(providers.resolve({"model": "shared-model", "provider_id": "p_two"})
                         ["key_name"], "TWO_KEY")

    def test_the_list_names_the_provider_and_marks_a_shared_name(self):
        rows = {(m["provider_id"], m["name"]): m for m in providers.node_models()}
        self.assertTrue(rows[("p_one", "shared-model")]["ambiguous"])
        self.assertTrue(rows[("p_two", "shared-model")]["ambiguous"])
        self.assertFalse(rows[("p_three", "only-here")]["ambiguous"])
        self.assertEqual(rows[("p_two", "shared-model")]["provider"], "Provider Two")
        self.assertNotIn(("p_off", "shared-model"), rows)

    def test_a_named_provider_resolves_by_id_or_display_name(self):
        self.assertEqual(providers.provider_id_for("p_two"), "p_two")
        self.assertEqual(providers.provider_id_for("provider two"), "p_two")
        self.assertEqual(providers.provider_id_for("Switched Off"), "")
        self.assertEqual(providers.provider_id_for("nobody"), "")
        self.assertEqual(providers.provider_id_for(""), "")

    def test_the_agent_context_lists_the_ready_models_with_providers(self):
        from agent import context
        line = context._models_line({"id": "p_ctx"})
        self.assertIn("[models]", line)
        self.assertIn("shared-model (Provider One, reads PDFs and images)", line)
        self.assertIn("shared-model (Provider Two, reads PDFs and images)", line)
        self.assertIn("only-here (Provider Three, reads PDFs and images)", line)
        self.assertNotIn("Switched Off", line)
        settings.update({"providers": []})
        self.assertEqual(context._models_line({"id": "p_ctx"}), "")

class RefusedTemperatureIsStoredTest(unittest.TestCase):
    def test_the_mark_is_written_listed_and_honoured(self):
        settings.update({"providers": [
            _wf("p_t1", "picky-model", key_name="T1_KEY", name="One"),
            _wf("p_t2", "picky-model", key_name="T2_KEY", name="Two"),
            _wf("p_t3", "easy-model", key_name="T3_KEY", name="Three")]})
        for k in ("T1_KEY", "T2_KEY", "T3_KEY"):
            secrets_store.set_secret(k, "k", secrets_store.OWNER_APP)
        providers._remember_no_temperature("picky-model")
        rows = {(m["provider_id"], m["name"]): m for m in providers.node_models()}
        self.assertTrue(rows[("p_t1", "picky-model")]["no_temperature"])
        self.assertTrue(rows[("p_t2", "picky-model")]["no_temperature"])
        self.assertFalse(rows[("p_t3", "easy-model")]["no_temperature"])
        self.assertIsNone(providers.resolve({"model": "picky-model", "temperature": 0.5})["temperature"])
        self.assertEqual(providers.resolve({"model": "easy-model", "temperature": 0.5})["temperature"], 0.5)
        from modelcall import adapters
        self.assertIs(adapters.remember_no_temperature, providers._remember_no_temperature)
