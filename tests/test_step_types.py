# Tests: the registry of the five step types, and what each module must declare
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests import _bootstrap

import step_types

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "backend" / "step_types"

class EveryTypeDeclaresItsPartsTest(unittest.TestCase):
    def test_the_five_types_in_plan_order(self):
        self.assertEqual(step_types.TYPES,
                         ("user-input", "code", "connector", "browser", "ai"))

    def test_each_module_declares_what_it_is_and_what_proves_it(self):
        for t in step_types.TYPES:
            m = step_types.module(t)
            self.assertEqual(m.TYPE, t)
            for part in ("MAY", "RECEIPT", "EVIDENCE", "BAR"):
                self.assertTrue(hasattr(m, part), f"{t} has no {part}")

    def test_every_type_answers_every_capability(self):
        for t in step_types.TYPES:
            self.assertEqual(set(step_types.module(t).MAY), set(step_types.CAPABILITIES), t)

    def test_an_unknown_type_is_allowed_nothing(self):
        self.assertIsNone(step_types.module("nonsense"))
        self.assertEqual(step_types.contract("nonsense"), {})
        for cap in step_types.CAPABILITIES:
            self.assertFalse(step_types.may("nonsense", cap))

class TheSetsAreWorkedOutNotWrittenTest(unittest.TestCase):
    def test_the_types_that_carry_code(self):
        self.assertEqual(set(step_types.CODE_TYPES), {"code", "connector", "browser"})

    def test_the_types_that_reach_outside(self):
        self.assertEqual(set(step_types.OUTSIDE_TYPES), {"connector", "browser"})

    def test_the_predicates(self):
        self.assertTrue(step_types.reaches_outside({"type": "connector"}))
        self.assertFalse(step_types.reaches_outside({"type": "ai"}))
        self.assertTrue(step_types.writes_outside({"type": "browser"}))
        self.assertFalse(step_types.writes_outside({"type": "browser", "read_only": True}))
        self.assertFalse(step_types.carries_code({"type": "user-input"}))

    def test_models_keeps_no_list_of_types_of_its_own(self):
        import models
        for gone in ("CODE_TYPES", "OUTSIDE_TYPES", "reaches_outside",
                     "writes_outside", "carries_code"):
            self.assertFalse(hasattr(models, gone), f"models.{gone} came back")

class ThePackageStaysLowTest(unittest.TestCase):
    def test_it_imports_only_models_and_the_standard_library(self):
        allowed_ours = {"models", "step_types"}
        ours = {"agent", "runtime", "storage", "extensions", "config",
                "providers", "server", "turns"}
        for f in PACKAGE.glob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".")[0]
                elif isinstance(node, ast.Import):
                    top = node.names[0].name.split(".")[0]
                else:
                    continue
                self.assertFalse(top in ours and top not in allowed_ours,
                                 f"{f.name} imports {top}")

class TheUserInputStepTest(unittest.TestCase):
    def setUp(self):
        from step_types import user_input
        self.m = user_input
        self.node = {"id": "n1", "name": "Pick a network", "type": "user-input",
                     "outputs": [{"name": "network", "type": "text", "label": "Network"},
                                 {"name": "api_key", "type": "secret"}]}

    def test_every_rule_is_reachable_through_the_registry(self):
        for rule in ("run", "shape_output", "ask_mid_run", "ask_again_after_failed_check",
                     "asks_for_a_stored_value", "asks_every_run_without_reason",
                     "port_warnings", "readiness", "refuse_run_alone", "run_summary"):
            self.assertIsNotNone(step_types.function("user-input", rule), rule)
            self.assertIsNone(step_types.function("code", rule), rule)

    def test_its_outputs_are_what_was_typed_and_a_secret_by_name(self):
        out, fired = self.m.run(self.node, {}, {"network": "home", "api_key": "sk-x"})
        self.assertEqual(out, {"network": "home", "api_key": "api_key"})
        self.assertFalse(fired)
        self.assertEqual(self.m.shape_output(self.node, {"a": 1}), {"a": 1})

    def test_an_up_front_step_never_asks_mid_run(self):
        self.assertIsNone(self.m.ask_mid_run(self.node, "n1", {}, {}, False, lambda n: False))

    def test_a_later_step_asks_for_what_is_missing_only(self):
        req = self.m.ask_mid_run(self.node, "n1", {"network": ""}, {}, True, lambda n: True)
        self.assertEqual([f["name"] for f in req["input_request"]["fields"]], ["network"])
        self.assertEqual(req["lines"], ['step "Pick a network" needs your input to continue'])
        self.assertIsNone(self.m.ask_mid_run(self.node, "n1", {"network": "home"}, {},
                                             True, lambda n: True))

    def test_a_picker_over_records_submits_one_field(self):
        node = {"id": "n2", "name": "Pick", "outputs": [
            {"name": "ssid", "type": "text", "value_field": "ssid"}]}
        rows = [{"ssid": "home", "signal": "strong"}]
        req = self.m.ask_mid_run(node, "n2", {}, {"scanned": rows}, True, lambda n: False)
        self.assertEqual(req["input_request"]["fields"][0]["options"],
                         [{"label": "home - strong", "value": "home"}])

    def test_a_failed_check_asks_again_with_its_own_words(self):
        req = self.m.ask_again_after_failed_check(self.node, "n1", ["must be a people search"])
        self.assertEqual(req["lines"],
                         ['step "Pick a network" needs a different value: must be a people search'])

    def test_the_build_side_rules(self):
        self.assertEqual(len(self.m.asks_for_a_stored_value(self.node, {"network"})), 1)
        self.assertEqual(self.m.asks_for_a_stored_value(self.node, set()), [])
        self.assertEqual(len(self.m.asks_every_run_without_reason(self.node)), 1)
        self.assertEqual(self.m.asks_every_run_without_reason(
            {**self.node, "per_run_reason": "they choose each time"}), [])
        self.assertEqual(self.m.port_warnings(self.node),
                         ["ports ['api_key'] have no human-readable label"])
        self.assertEqual(self.m.readiness(self.node),
                         {"declared_outputs": True, "implementation": True})
        self.assertIn("MID-RUN", self.m.run_summary(self.node, True))
        self.assertIn("RUN FORM", self.m.run_summary(self.node, False))

class _FakeCapability:
    def __init__(self, answer, usage=None):
        self.answer, self.usage, self.calls = answer, usage, []

    def ai_call(self, prompt, model, inputs, **kw):
        self.calls.append((prompt, model, inputs, kw))
        return self.answer

    def step_max_tokens(self, node):
        return 4096

    def step_ai_timeout(self, node):
        return 120

    def take_ai_usage(self):
        u, self.usage = self.usage, None
        return u

class TheAiStepTest(unittest.TestCase):
    def setUp(self):
        from step_types import ai
        self.m = ai
        self.node = {"id": "n1", "name": "Score posts", "type": "ai",
                     "config": {"prompt": "Score them", "model": {"model": "m"}},
                     "outputs": [{"name": "score", "type": "number", "description": "0-10"},
                                 {"name": "note", "type": "text"}]}

    def test_every_rule_is_reachable_through_the_registry(self):
        for rule in ("run", "record_usage", "read_call_failure", "blank_answer",
                     "output_check_failure_mode", "missing_prompt",
                     "answer_shape_unenforced", "every_output_is_a_list",
                     "undescribed_output_fields"):
            self.assertIsNotNone(step_types.function("ai", rule), rule)
            self.assertIsNone(step_types.function("code", rule), rule)

    def test_its_run_is_one_enforced_model_call(self):
        cap = _FakeCapability({"score": 7, "note": "fine"})
        out, fired = self.m.run(self.node, {"posts": [1]}, {}, cap)
        self.assertEqual(out, {"score": 7, "note": "fine"})
        self.assertFalse(fired)
        prompt, model, inputs, kw = cap.calls[0]
        self.assertEqual(kw["output_ports"], self.node["outputs"])
        self.assertEqual((kw["max_tokens"], kw["timeout"]), (4096, 120))

    def test_a_failed_call_keeps_how_long_it_ran(self):
        cap = _FakeCapability({"_unparsed": True, "error_kind": "timeout"})
        out, _ = self.m.run(self.node, {}, {}, cap)
        self.assertIn("_elapsed_s", out)

    def test_usage_is_handed_over_only_when_there_is_some(self):
        got = []
        self.m.record_usage(self.node, _FakeCapability({}, usage={"in": 3}), got.append)
        self.m.record_usage(self.node, _FakeCapability({}, usage=None), got.append)
        self.assertEqual(got, [{"in": 3}])

    def test_each_failure_kind_is_a_stop_or_a_pause(self):
        cap = _FakeCapability(None)
        read = lambda kind: self.m.read_call_failure(
            self.node, {"_unparsed": True, "error_kind": kind, "_text": "boom",
                        "_elapsed_s": 3.0}, cap)
        self.assertIsNone(self.m.read_call_failure(self.node, {"score": 1}, cap))
        self.assertEqual(read("auth")["pause"]["reason"], "environment-check-failed")
        self.assertEqual(read("http-503")["pause"]["reason"], "model-unavailable")
        self.assertEqual(read("transport")["pause"]["reason"], "model-unavailable")
        truncated = read("max-tokens")
        self.assertEqual(truncated["halt"]["reason"], "ai-answer-truncated")
        self.assertIn("4,096 tokens", truncated["halt"]["verdict"]["ai_call"][0])
        self.assertEqual((truncated["ai_failure"], truncated["elapsed"]), ("truncated", 3.0))
        self.assertIn("120 seconds", read("timeout")["halt"]["verdict"]["ai_call"][0])
        unsupported = read("file-unsupported")
        self.assertNotIn("output", unsupported["halt"])
        self.assertIsNone(unsupported["ai_failure"])
        other = read("weird")
        self.assertEqual(other["halt"]["verdict"]["ai_call"], ["the AI step didn't answer: boom"])
        self.assertEqual(other["ai_failure"], "call-failed")

    def test_an_all_blank_answer_stops_but_one_blank_field_does_not(self):
        self.assertEqual(self.m.blank_answer(self.node, {"score": None, "note": " "})["halt"]["reason"],
                         "ai-answered-blank")
        self.assertIsNone(self.m.blank_answer(self.node, {"score": 3, "note": ""}))
        lone = {**self.node, "outputs": [{"name": "items", "type": "list"}]}
        self.assertIsNone(self.m.blank_answer(lone, {"items": []}))

    def test_the_build_side_rules(self):
        self.assertEqual(self.m.missing_prompt("Score posts", {"prompt": "Score them"}), [])
        self.assertEqual(len(self.m.missing_prompt("Score posts", {"prompt": " "})), 1)
        anys = {"name": "x", "outputs": [{"name": "a", "type": ""}]}
        self.assertEqual(len(self.m.answer_shape_unenforced(anys)), 1)
        self.assertEqual(self.m.answer_shape_unenforced(self.node), [])
        lists = {"name": "x", "outputs": [{"name": "a", "item_fields": [{"name": "t"}]}]}
        self.assertEqual(len(self.m.every_output_is_a_list(lists)), 1)
        self.assertEqual(self.m.undescribed_output_fields(self.node),
                         ["\"Score posts\": output field(s) note have no description - each field should say what goes in it (the format is enforced from these, so the prompt never describes it)."])
        self.assertEqual(self.m.output_check_failure_mode(self.node), "wrong-shape")

class TheBrowserStepTest(unittest.TestCase):
    def setUp(self):
        from step_types import browser
        self.m = browser
        self.node = {"id": "n1", "name": "Read the inbox", "type": "browser"}

    def test_every_rule_is_reachable_through_the_registry(self):
        for rule in ("read_closed_window", "read_unanswered_question",
                     "work_mismatch", "fix_brief_hint", "learning_search_words"):
            self.assertIsNotNone(step_types.function("browser", rule), rule)
            self.assertIsNone(step_types.function("ai", rule), rule)

    def test_a_type_given_as_the_enum_is_found(self):
        from models import NodeType
        self.assertIs(step_types.module(NodeType.BROWSER), self.m)

    def test_a_closed_window_is_a_pause_either_way_in(self):
        by_kind = self.m.read_closed_window(self.node, "BrowserClosedError", "")
        self.assertEqual(by_kind["pause"]["reason"], "browser-closed")
        self.assertIn('"Read the inbox"', by_kind["pause"]["lines"][0])
        by_words = self.m.read_closed_window(self.node, "Error", "TargetClosedError: gone")
        self.assertEqual(by_words["pause"]["reason"], "browser-closed")
        self.assertIsNone(self.m.read_closed_window(self.node, "ValueError", "bad input"))

    def test_an_unanswered_question_keeps_what_was_asked(self):
        got = self.m.read_unanswered_question(self.node, "NobodyAnsweredError", "Sign in, then confirm")
        self.assertEqual(got["pause"]["reason"], "not-confirmed")
        self.assertIn(": Sign in, then confirm.", got["pause"]["lines"][0])
        self.assertIsNone(self.m.read_unanswered_question(self.node, "ValueError", ""))

    def test_a_plain_http_call_beside_the_window_is_its_own_step(self):
        self.assertEqual(self.m.work_mismatch("Read", {"http_client": False}, []), [])
        found = self.m.work_mismatch("Read", {"http_client": True}, ["slack"])
        self.assertIn("also calls slack over a plain HTTP client", found[0])

    def test_the_fix_hint_only_when_no_page_state_is_named(self):
        self.assertIn("BROWSER FAILURE WITHOUT A NAMED PAGE STATE",
                      self.m.fix_brief_hint(self.node, '{"hard_failures": ["no rows"]}'))
        self.assertEqual(self.m.fix_brief_hint(self.node, '{"x": "hit a captcha"}'), "")

    def test_window_settings_are_an_object_without_the_apps_own(self):
        self.assertEqual(self.m.window_options_problems("Read", {"locale": "en-GB"}), [])
        self.assertIn("must be an object", self.m.window_options_problems("Read", "en-GB")[0])
        own = self.m.window_options_problems("Read", {"headless": True, "user_data_dir": "/x"})
        self.assertIn("headless, user_data_dir", own[0])
        self.assertIsNotNone(step_types.function("browser", "window_options_problems"))
        self.assertIsNone(step_types.function("connector", "window_options_problems"))

    def test_the_capability_reads_that_replaced_name_tests(self):
        self.assertTrue(step_types.may("browser", "browser"))

        self.assertFalse(step_types.may("browser", "network"))
        self.assertIn("browser", step_types.OUTSIDE_TYPES)
        for t in ("code", "connector", "ai", "user-input"):
            self.assertFalse(step_types.may(t, "browser"), t)

class TheConnectorStepTest(unittest.TestCase):
    def setUp(self):
        from step_types import connector
        self.m = connector
        self.reader = {"id": "n1", "name": "Fetch the feed", "type": "connector",
                       "read_only": True}
        self.writer = {"id": "n2", "name": "Add the rows", "type": "connector",
                       "read_only": False}

    def test_every_rule_is_reachable_through_the_registry(self):
        for rule in ("read_slow_down", "read_no_answer", "work_mismatch",
                     "sends_without_saying_why", "lists_no_domains"):
            self.assertIsNotNone(step_types.function("connector", rule), rule)
            for other in ("code", "browser", "ai", "user-input"):
                if rule == "work_mismatch" and other in ("code", "browser"):
                    continue
                self.assertIsNone(step_types.function(other, rule), (other, rule))

    def test_slowing_down_is_a_pause_that_quotes_the_service(self):
        got = self.m.read_slow_down(self.reader, "HTTP 429 Too Many Requests",
                                    " (The service said: HTTP 429)")
        self.assertEqual(got["pause"]["reason"], "rate-limited")
        self.assertIn("\"Fetch the feed\" was asked to slow down by the service (The service said: HTTP 429).", got["pause"]["lines"][0])
        self.assertIsNone(self.m.read_slow_down(self.reader, "KeyError: 'title'", ""))

    def test_no_answer_runs_a_read_again_and_asks_about_a_send(self):
        text = "TimeoutError: The read operation timed out"
        self.assertEqual(self.m.read_no_answer(self.reader, text, "")["pause"]["reason"],
                         "service-unavailable")
        self.assertEqual(self.m.read_no_answer(self.writer, text, "")["pause"]["reason"],
                         "send-unverified")

        unmarked = {"name": "Post", "type": "connector"}
        self.assertEqual(self.m.read_no_answer(unmarked, text, "")["pause"]["reason"],
                         "send-unverified")
        self.assertIsNone(self.m.read_no_answer(self.reader, "404 Not Found", ""))

    def test_one_outside_system_and_no_window(self):
        self.assertEqual(self.m.work_mismatch("Fetch", {"browser": False}, ["slack"]), [])
        window = self.m.work_mismatch("Fetch", {"browser": True}, [])
        self.assertIn('set its type to "browser"', window[0])
        two = self.m.work_mismatch("Fetch", {"browser": False}, ["slack", "google"])
        self.assertIn("talks to 2 outside services (slack, google)", two[0])

    def test_a_sender_says_what_it_did(self):
        silent = dict(self.writer, outputs=[{"name": "count", "type": "number"}])
        self.assertIn("acts on items but its outputs carry no text",
                      self.m.sends_without_saying_why(silent)[0])
        talks = dict(self.writer, outputs=[{"name": "note", "type": "text"}])
        self.assertEqual(self.m.sends_without_saying_why(talks), [])
        self.assertEqual(self.m.sends_without_saying_why(dict(silent, read_only=True)), [])
        self.assertEqual(self.m.sends_without_saying_why(dict(self.writer, outputs=[])), [])

    def test_domains(self):
        self.assertTrue(self.m.lists_no_domains(self.reader))
        self.assertFalse(self.m.lists_no_domains(dict(self.reader, domains=["api.example.com"])))

    def test_the_capability_read_that_replaced_the_name_test(self):
        self.assertTrue(step_types.may("connector", "app"))
        for t in ("code", "browser", "ai", "user-input"):
            self.assertFalse(step_types.may(t, "app"), t)

class TheCodeStepTest(unittest.TestCase):
    def setUp(self):
        from step_types import code
        self.m = code

    def test_every_rule_is_reachable_through_the_registry(self):
        self.assertIsNotNone(step_types.function("code", "work_mismatch"))
        self.assertIsNotNone(step_types.function("code", "parses_a_file_without_saying_so"))
        for other in ("connector", "browser", "ai", "user-input"):
            self.assertIsNone(step_types.function(other, "parses_a_file_without_saying_so"), other)

    def test_a_window_or_an_outside_system_is_another_type(self):
        self.assertEqual(self.m.work_mismatch("Parse", {}, []), [])
        self.assertIn("make it a browser step",
                      self.m.work_mismatch("Parse", {"browser": True}, [])[0])
        self.assertIn("reaches an outside system but is typed as plain code",
                      self.m.work_mismatch("Parse", {"http_client": True}, [])[0])
        self.assertIn("reaches slack but is typed as plain code",
                      self.m.work_mismatch("Parse", {}, ["slack"])[0])

    def test_a_file_reader_says_when_the_file_is_wrong(self):
        node = {"name": "Read the sheet", "inputs": [{"name": "sheet", "type": "file"}],
                "code": "rows = read_input('sheet')"}
        self.assertIn("parses a file but never calls unexpected_input",
                      self.m.parses_a_file_without_saying_so(node)[0])
        says = dict(node, code="unexpected_input('no rows', found=[], expected=['a'])")
        self.assertEqual(self.m.parses_a_file_without_saying_so(says), [])
        self.assertEqual(self.m.parses_a_file_without_saying_so(dict(node, inputs=[])), [])

    def test_no_network_and_nothing_outside(self):
        for cap in ("network", "app", "browser", "model", "process"):
            self.assertFalse(step_types.may("code", cap), cap)
        self.assertTrue(step_types.may("code", "code"))

if __name__ == "__main__":
    unittest.main()
