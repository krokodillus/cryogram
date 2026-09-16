# Tests: one default provider kept by a rule, the model picked by code, the question raised by code, blank ranks, the file kinds as the step's own check, and no temperature unless set
from __future__ import annotations

import json
import unittest
from unittest import mock

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

import providers
from agent import context, node_tools, steps
from modelcall import adapters
from runtime import capability, env_checks, executor
from storage import blobstore, secrets_store, settings

PDF_BYTES = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n2 0 obj << /Type /Page >> endobj\n"

def _cards(default=""):
    settings.update({"providers": [
        {"id": "anthropic", "name": "Anthropic", "adapter": "anthropic", "auth": "api-key",
         "use": "workflow", "enabled": True, "key_name": "DP_A", "tags": [],
         "models": [{"name": "claude-sonnet-5", "cost": 6, "quality": 8},
                    {"name": "claude-haiku-4-5-20251001", "cost": 2, "quality": 6},
                    {"name": "claude-unranked", "cost": None, "quality": None}]},
        {"id": "gemini", "name": "Gemini", "adapter": "openai", "auth": "api-key",
         "use": "workflow", "enabled": True, "key_name": "DP_G", "tags": [],
         "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/",
         "models": [{"name": "gemini-3.5-flash", "cost": None, "quality": None}]},
        {"id": "mistral", "name": "Mistral", "adapter": "openai", "auth": "api-key",
         "use": "workflow", "enabled": True, "key_name": "DP_M", "tags": [],
         "endpoint": "https://api.mistral.ai/v1",
         "models": [{"name": "mistral-small-latest", "cost": 1, "quality": 3}]}],
        "default_provider": default, "master_ai": {"model": ""}})
    secrets_store.set_secret("DP_A", "k", secrets_store.OWNER_APP)
    secrets_store.set_secret("DP_G", "k", secrets_store.OWNER_APP)
    secrets_store.delete_secret("DP_M", secrets_store.OWNER_APP)
    settings.update({"default_provider": default})

def _forget():
    settings.update({"providers": [], "default_provider": "", "master_ai": {"model": ""}})

class DefaultTickTest(unittest.TestCase):
    def tearDown(self):
        _forget()

    def test_the_tick_sits_on_an_active_card_and_a_keyless_card_is_not_active(self):
        _cards()
        s = settings.get()
        self.assertEqual(s["default_provider"], "anthropic")
        view = settings.public_view()
        active = {p["id"]: p["active"] for p in view["providers"]}
        self.assertEqual(active, {"anthropic": True, "gemini": True, "mistral": False})
        self.assertEqual(next(p for p in view["providers"] if p["id"] == "anthropic")["reads"],
                         "reads PDFs and images")

    def test_the_tick_cannot_land_on_an_inactive_card(self):
        _cards()
        settings.update({"default_provider": "mistral"})
        self.assertEqual(settings.get()["default_provider"], "anthropic")

    def test_disabling_the_default_moves_the_tick_and_disabling_all_clears_it(self):
        _cards("gemini")
        self.assertEqual(settings.get()["default_provider"], "gemini")
        ps = settings.get()["providers"]
        next(p for p in ps if p["id"] == "gemini")["enabled"] = False
        settings.update({"providers": ps})
        self.assertEqual(settings.get()["default_provider"], "anthropic")
        ps = settings.get()["providers"]
        next(p for p in ps if p["id"] == "anthropic")["enabled"] = False
        settings.update({"providers": ps})
        self.assertEqual(settings.get()["default_provider"], "")
        ps = settings.get()["providers"]
        next(p for p in ps if p["id"] == "gemini")["enabled"] = True
        settings.update({"providers": ps})
        self.assertEqual(settings.get()["default_provider"], "gemini")

    def test_a_saved_key_makes_a_card_active_and_a_deleted_one_moves_the_tick(self):
        _cards()
        settings.update({"providers": [p for p in settings.get()["providers"] if p["id"] == "mistral"]})
        self.assertEqual(settings.get()["default_provider"], "")
        secrets_store.set_secret("DP_M", "k", secrets_store.OWNER_APP)
        settings.note_key_change("DP_M", present=True)
        self.assertEqual(settings.get()["default_provider"], "mistral")
        secrets_store.delete_secret("DP_M", secrets_store.OWNER_APP)
        settings.note_key_change("DP_M", present=False)
        self.assertEqual(settings.get()["default_provider"], "")

class PickTest(unittest.TestCase):
    def tearDown(self):
        _forget()

    def test_the_cheapest_ranked_model_on_the_default_provider_is_picked(self):
        _cards("anthropic")
        self.assertEqual(providers.pick_model([])["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(providers.pick_model(["pdf"])["model"], "claude-haiku-4-5-20251001")

    def test_an_unranked_card_picks_nothing_and_the_sentence_says_why(self):
        _cards("gemini")
        self.assertIsNone(providers.pick_model([]))
        self.assertIn("No model on Gemini has a Cost yet", providers.no_pick_sentence([], step="Read"))
        self.assertIn('the step "Read"', providers.no_pick_sentence([], step="Read"))

    def test_a_blank_cost_is_never_picked_over_a_ranked_one(self):
        _cards("anthropic")
        ps = settings.get()["providers"]
        for m in next(p for p in ps if p["id"] == "anthropic")["models"]:
            if m["name"] != "claude-unranked":
                m["cost"] = None
        settings.update({"providers": ps})
        self.assertIsNone(providers.pick_model([]))

    def test_the_options_put_the_default_provider_first(self):
        _cards("gemini")
        names = [o["name"] for o in providers.model_options([])]
        self.assertEqual(names[0], "gemini-3.5-flash")
        self.assertIn("claude-haiku-4-5-20251001", names)
        self.assertEqual(providers.node_models()[0]["provider_id"], "gemini")

    def test_the_models_line_names_the_default_and_the_rule(self):
        _cards("anthropic")
        line = context._models_line({})
        self.assertIn("The default provider is Anthropic", line)
        self.assertIn("leave `model` blank", line)

    def test_a_blank_model_at_run_time_resolves_to_the_pick(self):
        _cards("anthropic")
        seen = {}

        def fake(model_ref, prompt, inputs, **k):
            seen.update(model_ref)
            return {"structured": {"n": 1}}
        with mock.patch.object(providers, "call", fake):
            out = capability.ai_call("p", {"model": ""}, {}, output_ports=[{"name": "n", "type": "number"}])
        self.assertEqual(seen["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(out, {"n": 1})

    def test_a_blank_model_with_nothing_to_pick_stops_before_the_call(self):
        _cards("gemini")
        called = {"n": 0}
        with mock.patch.object(providers, "call", lambda *a, **k: called.__setitem__("n", 1)):
            out = capability.ai_call("p", {"model": ""}, {}, output_ports=[{"name": "n", "type": "number"}])
        self.assertEqual(called["n"], 0)
        self.assertEqual(out.get("error_kind"), "model-not-chosen")
        problems = env_checks.check_all_nodes({"nodes": [
            {"id": "n_a", "name": "Read", "type": "ai", "config": {"model": {"model": ""}}, "inputs": []}]})
        self.assertEqual(len(problems), 1)
        self.assertIn("No model on Gemini has a Cost yet", problems[0])
        _cards("anthropic")
        self.assertEqual(env_checks.check_all_nodes({"nodes": [
            {"id": "n_a", "name": "Read", "type": "ai", "config": {"model": {"model": ""}}, "inputs": []}]}), [])

class AskCardTest(unittest.TestCase):
    def tearDown(self):
        _forget()

    def test_the_try_asks_on_a_card_and_the_answer_lands_on_the_plan_step(self):
        _cards("gemini")
        p = _workflow([], pid="p_dp_ask")
        p["plan"] = {"nodes": [{"name": "Read", "type": "ai", "description": "d"}], "edges": []}
        raised = {}

        def fake_raise(workflow, request_kind, payload, ikind, ipayload, prefix):
            raised.update(payload)
            return "int_x", {}, {"text": "claude-haiku-4-5-20251001"}
        from agent import actions
        with mock.patch.object(actions, "_raise_card", fake_raise):
            picked = node_tools.pick_or_ask(p, "Read", {})
        self.assertEqual(picked["model"], "claude-haiku-4-5-20251001")
        self.assertTrue(picked["chosen_by_user"])
        self.assertIn('Which AI model should the step "Read" use?', raised["question"])
        self.assertEqual(raised["options"][0], "gemini-3.5-flash")
        self.assertIn("claude-haiku-4-5-20251001", raised["options"])

    def test_a_parked_card_ends_the_turn(self):
        _cards("gemini")
        p = _workflow([], pid="p_dp_park")
        from agent import actions, turnstate
        with mock.patch.object(actions, "_raise_card", lambda *a, **k: ("int_x", {}, None)):
            picked = node_tools.pick_or_ask(p, "Read", {})
        self.assertNotIn("model", picked)
        self.assertTrue(turnstate.of(p).ask_open)

class NamedModelTest(unittest.TestCase):
    def tearDown(self):
        _forget()

    def test_the_users_words_find_the_listed_model(self):
        _cards("gemini")
        self.assertEqual(providers.match_model("Sonnet 5")[0]["name"], "claude-sonnet-5")
        self.assertEqual(providers.match_model("haiku")[0]["name"], "claude-haiku-4-5-20251001")
        self.assertEqual(providers.match_model("claude-sonnet-5")[0]["provider_id"], "anthropic")
        row, why = providers.match_model("claude")
        self.assertIsNone(row)
        self.assertIn("fits more than one", why)
        row, why = providers.match_model("gpt-9")
        self.assertIsNone(row)
        self.assertIn("no set-up model matches 'gpt-9'", why)
        self.assertIn("claude-sonnet-5", why)

    def test_a_named_model_is_used_by_the_try_and_never_swapped(self):
        _cards("gemini")
        p = _workflow([], pid="p_dp_named")
        p["plan"] = {"nodes": [{"name": "Read", "type": "ai", "prompt": "p",
                                "outputs": [{"name": "n", "type": "number"}]}], "edges": []}
        seen = {}

        def fake(model_ref, prompt, inputs, **k):
            seen.update(model_ref)
            return {"structured": {"n": 1}}
        with mock.patch.object(node_tools, "skeleton_first", lambda w: ""), \
             mock.patch.object(node_tools, "_step_must_exist", lambda w, n: ""), \
             mock.patch.object(node_tools, "_proposal_first", lambda w, n, pr: ""), \
             mock.patch.object(providers, "call", fake):
            r = node_tools.tool_run_ai_step(p, "Read", "p", "sonnet 5", inputs={},
                                            outputs=[{"name": "n", "type": "number"}])
            self.assertTrue(r.get("ok"), r)
            self.assertEqual(seen["model"], "claude-sonnet-5")
            r = node_tools.tool_run_ai_step(p, "Read", "p", "gpt-9", inputs={},
                                            outputs=[{"name": "n", "type": "number"}])
            self.assertIn("no set-up model matches 'gpt-9'", r.get("error", ""))

    def test_a_set_temperature_reaches_the_call(self):
        _cards("anthropic")
        seen = {}

        def fake(model_ref, prompt, inputs, **k):
            seen.update(model_ref)
            return {"structured": {"n": 1}}
        with mock.patch.object(providers, "call", fake):
            capability.ai_call("p", {"model": "claude-sonnet-5", "temperature": 0.4}, {},
                               output_ports=[{"name": "n", "type": "number"}])
        self.assertEqual(seen["temperature"], 0.4)
        self.assertEqual(providers.resolve({"model": "claude-sonnet-5", "temperature": 0.4})["temperature"], 0.4)

class FileKindsTest(unittest.TestCase):
    def tearDown(self):
        _forget()

    def _pdf(self):
        return blobstore.put(PDF_BYTES, "application/pdf", {"name": "invoice.pdf"}, owner=blobstore.OWNER_APP)

    def test_the_first_try_fills_the_ports_kinds_from_the_sample(self):
        p = _workflow([{"id": "n_r", "name": "Read", "type": "ai",
                        "config": {"prompt": "p", "model": {"model": ""}},
                        "inputs": [{"name": "doc", "type": "file"}], "outputs": [], "tests": []}],
                      pid="p_dp_kinds")
        p["plan"] = {"nodes": [{"name": "Read", "type": "ai", "inputs": [{"name": "doc", "type": "file"}]}], "edges": []}
        self.assertTrue(steps.seed_file_kinds(p, "Read", {"doc": self._pdf(), "n": 3}))
        self.assertEqual(p["nodes"][0]["inputs"][0]["file_kinds"], ["pdf"])
        self.assertEqual(p["plan"]["nodes"][0]["inputs"][0]["file_kinds"], ["pdf"])
        self.assertEqual(p["nodes"][0]["config"]["sends_file"], {"kinds": ["pdf"]})

        self.assertFalse(steps.seed_file_kinds(p, "Read", {"doc": blobstore.put(b"\x89PNG\r\n", "image/png", {"name": "a.png"}, owner=blobstore.OWNER_APP)}))
        self.assertEqual(p["nodes"][0]["inputs"][0]["file_kinds"], ["pdf"])

    def test_the_old_one_word_kind_reads_as_a_list_and_a_chat_edit_widens_it(self):
        self.assertEqual(steps.port_file_kinds({"type": "file", "file_kind": "PDF"}), ["pdf"])
        self.assertEqual(steps.port_file_kinds({"type": "file", "file_kinds": ["png", "jpeg", "pdf"]}), ["image", "pdf"])
        p = _workflow([{"id": "n_r", "name": "Read", "type": "ai",
                        "config": {"prompt": "p", "model": {"model": ""}},
                        "inputs": [], "outputs": [], "tests": []}], pid="p_dp_widen")
        r = node_tools._set_declared_io(p, "n_r", [{"name": "doc", "type": "file", "file_kinds": ["pdf", "jpg"]}],
                                        [{"name": "total", "type": "number"}])
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(p["nodes"][0]["inputs"][0]["file_kinds"], ["pdf", "image"])

    def test_a_file_of_another_kind_stops_the_run_before_anything_is_sent(self):
        _cards("anthropic")
        p = _workflow([{"id": "n_c", "name": "Count pages", "type": "code",
                        "config": {"code": "write_output('n', 1)\n", "criteria": []},
                        "inputs": [{"name": "doc", "type": "file", "file_kinds": ["pdf"]}],
                        "outputs": [{"name": "n", "type": "number"}], "tests": []}], pid="p_dp_wrong")
        xlsx = blobstore.put(b"PK\x03\x04 sheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             {"name": "report.xlsx"}, owner=blobstore.OWNER_APP)
        res = executor.run_workflow(p, {"doc": xlsx})
        self.assertEqual((res["status"], res["reason"]), ("halted", "wrong-file-kind"))
        self.assertEqual(res["verdict"]["environment"][0],
                         "This workflow was built for a PDF, and the file given is a spreadsheet (report.xlsx). Give it a PDF, or ask in chat to change the workflow.")
        res2 = executor.run_workflow(p, {"doc": self._pdf()})
        self.assertEqual(res2["status"], "completed", res2)

class TemperatureTest(unittest.TestCase):
    def test_a_temperature_is_always_sent_and_a_refusal_drops_it(self):
        self.assertEqual(adapters.clamp_temperature(None), 0.0)
        self.assertEqual(adapters.clamp_temperature("0"), 0.0)
        self.assertEqual(adapters.clamp_temperature(3), 1.0)
        self.assertEqual(providers.resolve({"model": "nothing"})["temperature"], 0.0)
        self.assertEqual(providers.resolve({"model": "nothing", "temperature": 0.2})["temperature"], 0.2)

    def test_the_openai_shape_retries_without_a_refused_temperature(self):
        import io as _io
        import urllib.error
        import urllib.request
        bodies = []
        reply = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"total": 7})}}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

        class _Resp:
            def __init__(self, p): self._p = p
            def read(self): return json.dumps(self._p).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake(req, timeout=None, context=None):
            body = json.loads(req.data.decode())
            bodies.append(body)
            if "temperature" in body:
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                             _io.BytesIO(b'{"error":{"message":"Unsupported value: temperature does not support 0 with this model."}}'))
            return _Resp(reply)
        adapters._NO_TEMPERATURE.discard("gpt-new")
        with mock.patch.object(urllib.request, "urlopen", fake):
            res = adapters.call("openai", "gpt-new", "k", "read it", [], temperature=0,
                                schema={"type": "object", "properties": {"total": {"type": "number"}}})
            self.assertEqual(res["structured"], {"total": 7})
            self.assertEqual([("temperature" in b) for b in bodies], [True, False])
            adapters.call("openai", "gpt-new", "k", "read it", [], temperature=0,
                          schema={"type": "object", "properties": {"total": {"type": "number"}}})
            self.assertNotIn("temperature", bodies[-1])
        adapters._NO_TEMPERATURE.discard("gpt-new")

if __name__ == "__main__":
    unittest.main()

class BuilderEffortAndShippedModelsTest(unittest.TestCase):
    def tearDown(self):
        _forget()

    def test_the_effort_is_medium_unless_the_tab_set_a_level_the_family_knows(self):
        self.assertEqual(settings.builder_effort(), "medium")
        settings.update({"master_ai": {"model": "", "effort": "high"}})
        self.assertEqual(settings.builder_effort("anthropic"), "high")
        self.assertEqual(settings.builder_effort("codex"), "high")
        settings.update({"master_ai": {"model": "", "effort": "max"}})
        self.assertEqual(settings.builder_effort("codex"), "medium")
        view = settings.public_view()
        self.assertEqual(view["effort_default"], "medium")
        self.assertIn("minimal", view["effort_levels"]["codex"])
        self.assertIn("max", view["effort_levels"]["anthropic"])

    def test_the_shipped_list_comes_from_the_file_with_its_default_marked(self):
        from storage import model_catalog
        cat = model_catalog.builder_models()
        self.assertEqual([m["name"] for m in cat["anthropic"] if m.get("default")], ["claude-sonnet-5"])
        self.assertEqual([m["name"] for m in cat["codex"] if m.get("default")], ["gpt-5.6-terra"])
        self.assertEqual(len(cat["anthropic"]), 3)
        self.assertEqual(len(cat["codex"]), 3)
        self.assertEqual(model_catalog.facts({"id": "codex"}, "gpt-5.6-terra")["reads"], [])

    def test_no_model_ticked_runs_the_builder_provider_with_the_programs_default(self):
        from agent import transport
        settings.update({"providers": [
            {"id": "builder", "name": "Builder", "adapter": "anthropic",
             "auth": "claude-subscription", "use": "builder", "enabled": True,
             "key_name": "", "tags": [], "models": []}],
            "master_ai": {"model": ""}})
        tr = transport.for_settings()
        self.assertEqual(tr.model, "")
        self.assertEqual(tr.auth, "claude-subscription")
        settings.update({"providers": []})
        with self.assertRaises(transport.TransportError):
            transport.for_settings()
