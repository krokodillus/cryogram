# Tests: standard criteria (port_checks), environment checks (env_checks) and the read_input contract - including the executor-level slice test for the original PDF-read-as-text failure, which must halt with a reasoned verdict, never a raw TypeError
from __future__ import annotations

import unittest
from pathlib import Path

from tests import _bootstrap

from storage import blobstore
from runtime import env_checks
from runtime import executor
from runtime import port_checks

def _port(name, type_, **kw):
    return {"name": name, "type": type_, "label": "", "options": [], **kw}

class PortChecksTest(unittest.TestCase):
    def _one(self, port, value, require_all=False):
        return port_checks.check_ports([port], {port["name"]: value}, require_all)

    def test_text(self):
        self.assertEqual(self._one(_port("t", "text"), "hello"), [])
        self.assertIn("should be text", self._one(_port("t", "text"), 42)[0]["problem"])

    def test_number_rejects_bool_and_text(self):
        self.assertEqual(self._one(_port("n", "number"), 3.5), [])
        self.assertTrue(self._one(_port("n", "number"), True))
        self.assertTrue(self._one(_port("n", "number"), "12"))

    def test_boolean(self):
        self.assertEqual(self._one(_port("b", "boolean"), False), [])
        self.assertTrue(self._one(_port("b", "boolean"), "yes"))

    def test_date(self):
        self.assertEqual(self._one(_port("d", "date"), "2026-07-06"), [])
        self.assertIn("2026-01-31", self._one(_port("d", "date"), "6 July")[0]["problem"])

    def test_enum(self):
        p = _port("e", "enum", options=["A", "B"])
        self.assertEqual(self._one(p, "A"), [])
        self.assertIn("the choices are", self._one(p, "C")[0]["problem"])

    def test_record(self):
        self.assertEqual(self._one(_port("r", "record"), {"k": 1}), [])
        self.assertTrue(self._one(_port("r", "record"), "not json"))

    def test_file_blob_ref_must_resolve_but_inline_text_ok(self):
        ref = blobstore.put(b"hello", "text/plain", {}, owner=blobstore.OWNER_APP)
        self.assertEqual(self._one(_port("f", "file"), ref), [])
        self.assertEqual(self._one(_port("f", "file"), "inline sample text"), [])
        self.assertIn("no longer stored",
                      self._one(_port("f", "file"), "blob:deadbeef")[0]["problem"])

    def test_secret_and_any_skipped(self):
        self.assertEqual(self._one(_port("s", "secret"), 123), [])
        self.assertEqual(self._one(_port("a", "any"), object()), [])

    def test_require_all_flags_missing_output(self):
        probs = port_checks.check_ports([_port("x", "number")], {}, require_all=True)
        self.assertIn("was not produced", probs[0]["problem"])

    def test_non_dict_values(self):
        probs = port_checks.check_ports([_port("x", "number")], "just a string",
                                        require_all=True)
        self.assertIn("keyed by port name", probs[0]["problem"])

class ItemFieldsCheckTest(unittest.TestCase):
    _PORT = _port("posts", "any", item_fields=[
        {"name": "title", "type": "text", "description": "headline"},
        {"name": "score", "type": "number"},
        {"name": "verdict", "type": "enum", "options": ["ENGAGE", "SKIP"],
         "required": True}])

    def _check(self, value):
        return port_checks.check_ports([self._PORT], {"posts": value})

    def test_conforming_items_pass_extra_fields_legal(self):
        self.assertEqual(self._check([
            {"title": "a", "score": 3, "verdict": "ENGAGE", "extra": 1},
            {"title": "b", "score": 0, "verdict": "SKIP"}]), [])

    def test_empty_list_passes(self):
        self.assertEqual(self._check([]), [])

    def test_missing_must_have_field_flagged_expected_tolerated(self):
        probs = self._check([{"title": "a", "score": 3, "verdict": "ENGAGE"},
                             {"title": "b", "score": 1}])
        self.assertIn("item 2 > verdict is missing",
                      probs[0]["problem"])

        self.assertEqual(self._check([{"verdict": "SKIP"}]), [])

    def test_wrong_field_type_flagged(self):
        probs = self._check([{"title": "a", "score": "high",
                              "verdict": "SKIP"}])
        self.assertIn("should be a number", probs[0]["problem"])

    def test_enum_membership_flagged(self):
        probs = self._check([{"title": "a", "score": 1, "verdict": "MAYBE"}])
        self.assertIn("allowed values", probs[0]["problem"])

    def test_non_record_item_flagged(self):
        probs = self._check(["just a string"])
        self.assertIn("should be a record", probs[0]["problem"])

    def test_non_list_value_flagged(self):
        probs = self._check({"title": "a"})
        self.assertIn("should be a list", probs[0]["problem"])

    def test_plain_any_port_still_skipped(self):
        self.assertEqual(port_checks.check_ports(
            [_port("blob", "any")], {"blob": object()}), [])

class EnvChecksTest(unittest.TestCase):
    def _ai_node(self, model):
        return {"id": "n_ai", "name": "judge", "type": "ai",
                "config": {"model": {"model": model}}}

    def test_ai_node_without_model_flagged(self):
        probs = env_checks.check_node(self._ai_node(""))
        self.assertIn("No AI provider is set up for the step", probs[0])

    def test_unknown_model_flagged(self):
        probs = env_checks.check_node(self._ai_node("no-such-model"))
        self.assertIn("isn't set up", probs[0])
        self.assertNotIn("no-such-model", probs[0])

    def test_check_variables_blocks_a_value_the_port_cannot_mean(self):
        workflow = {"id": "p", "nodes": [
            {"id": "n", "name": "Fetch", "type": "code",
             "config": {"code": "x=1"},
             "inputs": [{"name": "cap", "type": "number"}], "outputs": []}],
            "variables": [{"name": "cap", "label": "Post cap",
                           "value": "lots", "secret": False}]}
        probs = env_checks.check_variables(workflow)
        self.assertEqual(len(probs), 1, probs)
        self.assertIn('"Post cap"', probs[0])
        self.assertIn("Inputs tab", probs[0])

        workflow["variables"][0]["value"] = 25
        self.assertEqual(env_checks.check_variables(workflow), [])
        workflow["variables"][0]["value"] = ""
        self.assertEqual(env_checks.check_variables(workflow), [])

    def test_check_variables_ignores_secrets(self):
        workflow = {"id": "p", "nodes": [
            {"id": "n", "name": "Send", "type": "connector",
             "config": {"code": "x=1"},
             "inputs": [{"name": "api_key", "type": "secret"}], "outputs": []}],
            "variables": [{"name": "api_key", "value": True, "secret": True}]}
        self.assertEqual(env_checks.check_variables(workflow), [])

    def test_check_all_nodes_flags_unready_ai_only(self):
        workflow = {"id": "p", "nodes": [
            {"id": "n_ai", "name": "Judge it", "type": "ai",
             "config": {"model": {"model": "no-such-model"}}},
            {"id": "n_c", "name": "Post it", "type": "connector", "external_impact": "sends",
             "inputs": [{"name": "API_KEY", "type": "secret"}], "outputs": []},
            {"id": "n_code", "name": "Parse", "type": "code", "config": {"code": "x=1"}}]}
        probs = env_checks.check_all_nodes(workflow)
        self.assertTrue(any('"Judge it"' in p and "isn't set up" in p for p in probs), probs)
        self.assertFalse(any("Post it" in p for p in probs), probs)
        self.assertFalse(any("no-such-model" in p for p in probs))

    def test_check_all_nodes_clean_when_no_ai(self):
        workflow = {"id": "p", "nodes": [
            {"id": "n_c", "name": "Post it", "type": "connector", "external_impact": "s",
             "inputs": [{"name": "API_KEY", "type": "secret"}], "outputs": []},
            {"id": "n_code", "name": "Parse", "type": "code", "config": {"code": "x=1"}}]}
        self.assertEqual(env_checks.check_all_nodes(workflow), [])

    def test_non_ai_nodes_have_no_env_check(self):
        self.assertEqual(env_checks.check_node({"id": "n", "type": "code"}), [])

    def test_dead_entry_blob_flagged(self):
        probs = env_checks.check_run({"id": "p", "nodes": []}, {"doc": "blob:gone"})
        self.assertIn("no longer exists", probs[0])

    def test_clean_entry_passes(self):
        self.assertEqual(env_checks.check_run({"id": "p", "nodes": []}, {"x": 1}), [])

    def test_run_refuses_to_start_when_ai_not_ready(self):
        p = {"id": "p_envrun", "variables": [], "edges": [],
             "nodes": [{"id": "n_ai", "name": "Judge", "type": "ai",
                        "config": {"model": {"model": "no-such-model"}},
                        "inputs": [], "outputs": [{"name": "v", "type": "text"}],
                        "tests": []}]}
        res = executor.run_workflow(p, {})
        self.assertEqual((res["status"], res["reason"]),
                         ("halted", "environment-check-failed"))
        self.assertIsNone(res["halted_at"])
        self.assertNotIn("case_id", res)

    def test_untaken_ai_branch_never_blocks_a_run(self):
        node = {"id": "n_ok", "name": "ok", "type": "code",
                "config": {"code": "def f(x):\n    return x + 1\n", "criteria": []},
                "inputs": [_port("x", "number")],
                "outputs": [_port("y", "number")], "tests": []}
        res = executor.run_isolated(node, {"x": 1}, workflow_id="p_ports")
        self.assertEqual(res["status"], "completed")

class InputContractTest(unittest.TestCase):
    CODE = ("def classify(document):\n"
            "    text = read_input('document')\n"
            "    return 'A' if 'Invoice' in text else 'unknown'\n")

    def _node(self):
        return {"id": "n_cls", "name": "classify", "type": "code",
                "config": {"code": self.CODE, "criteria": []},
                "inputs": [_port("document", "file")],
                "outputs": [_port("doc_type", "text")], "tests": []}

    def test_binary_blob_halts_with_reasoned_verdict(self):
        ref = blobstore.put(b"%PDF-1.7 \xde\xad\xbe\xef", "application/pdf", {}, owner=blobstore.OWNER_APP)
        res = executor.run_isolated(self._node(), {"document": ref}, workflow_id="p_ports")
        self.assertEqual(res["status"], "halted")
        self.assertEqual(res["reason"], "input-contract-failed")
        msg = res["verdict"]["standard_input_check"][0]
        self.assertIn("application/pdf", msg)
        self.assertIn("binary=True", msg)

    def test_text_blob_still_reads_as_text(self):
        ref = blobstore.put(b"Invoice number 42", "text/plain", {}, owner=blobstore.OWNER_APP)
        res = executor.run_isolated(self._node(), {"document": ref}, workflow_id="p_ports")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["output"], {"doc_type": "A"})

    def test_standard_output_check_catches_wrong_shape(self):
        node = self._node()
        node["outputs"] = [_port("doc_type", "number")]
        ref = blobstore.put(b"Invoice", "text/plain", {}, owner=blobstore.OWNER_APP)
        res = executor.run_isolated(node, {"document": ref}, workflow_id="p_ports")
        self.assertEqual(res["status"], "halted")
        self.assertEqual(res["reason"], "output-check-failed (standard)")
        self.assertIn("should be a number",
                      res["verdict"]["standard_output_check"][0])

class UnexpectedInputTest(unittest.TestCase):
    CODE = ("import csv, io\n"
            "rows = list(csv.DictReader(io.StringIO(read_input('sheet'))))\n"
            "cols = list(rows[0].keys()) if rows else []\n"
            "need = ['date', 'merchant', 'amount']\n"
            "if any(c not in cols for c in need):\n"
            "    unexpected_input('This does not look like an expense sheet',\n"
            "                     found=cols, expected=need)\n"
            "write_output('n', len(rows))\n")

    def _node(self):
        return {"id": "n_rd", "name": "Read expenses", "type": "code",
                "config": {"code": self.CODE, "criteria": []},
                "inputs": [_port("sheet", "file")],
                "outputs": [_port("n", "number")], "tests": []}

    def test_a_wrong_file_halts_input_shape_with_found_and_expected(self):
        ref = blobstore.put(b"name,email\nAda,a@x\n", "text/csv", {}, owner=blobstore.OWNER_APP)
        res = executor.run_isolated(self._node(), {"sheet": ref}, workflow_id="p_ports")
        self.assertEqual(res["status"], "halted")
        self.assertEqual(res["reason"], "input-shape")
        line = res["verdict"]["input_shape"][0]
        self.assertIn("does not look like an expense sheet", line)
        self.assertIn("date, merchant and amount", line)
        self.assertIn("name and email", line)
        row = res["offending"][0]["rows"][0]["value"]
        self.assertEqual(row["found"], ["name", "email"])
        self.assertEqual(row["expected"], ["date", "merchant", "amount"])

    def test_the_right_file_runs(self):
        ref = blobstore.put(b"date,merchant,amount\n2026-01-01,x,1\n", "text/csv", {}, owner=blobstore.OWNER_APP)
        res = executor.run_isolated(self._node(), {"sheet": ref}, workflow_id="p_ports")
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(res["output"], {"n": 1})

    def test_the_fix_brief_asks_the_user_not_a_code_repair(self):
        from agent import orchestrator
        brief = orchestrator._input_shape_brief({
            "reason": "input-shape",
            "offending": [{"rows": [{"value": {"found": ["name", "email"],
                                                "expected": ["date", "amount"]}}]}]})
        self.assertIn("ASK THE USER", brief)
        self.assertIn("do NOT 'add validation'", brief)
        self.assertIn("ANOTHER FORMAT", brief)
        self.assertEqual(orchestrator._input_shape_brief({"reason": "node threw: KeyError"}), "")

class CodeStyleTest(unittest.TestCase):
    def _node(self, code):
        return {"id": "n_style", "name": "add", "type": "code",
                "config": {"code": code, "criteria": []},
                "inputs": [_port("a", "number"), _port("b", "number")],
                "outputs": [_port("sum", "number")], "tests": []}

    def test_top_level_script_produces_output(self):
        code = ("num1 = read_input('a')\n"
                "num2 = read_input('b')\n"
                "write_output('sum', num1 + num2)\n")
        res = executor.run_isolated(self._node(code), {"a": 2, "b": 3},
                                    workflow_id="p_ports")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["output"], {"sum": 5})

    def test_function_style_still_works(self):
        code = "def run(a, b):\n    return {'sum': a + b}\n"
        res = executor.run_isolated(self._node(code), {"a": 4, "b": 5},
                                    workflow_id="p_ports")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["output"], {"sum": 9})

    def test_script_with_helper_def_is_not_mistaken_for_entry(self):
        code = ("def double(x):\n"
                "    return x * 2\n"
                "write_output('sum', double(read_input('a')) + read_input('b'))\n")
        res = executor.run_isolated(self._node(code), {"a": 3, "b": 1},
                                    workflow_id="p_ports")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["output"], {"sum": 7})

class CorpusScrubTest(unittest.TestCase):
    def test_substring_and_nested_scrub(self):
        from runtime import executor
        from storage import secrets_store
        secrets_store.set_secret("SCRUB_KEY", "sekrit-value-9", secrets_store.OWNER_APP)
        v = executor._scrub({"header": "Bearer sekrit-value-9",
                             "nested": {"list": ["ok", "x sekrit-value-9 y"]},
                             "plain": 42})
        self.assertEqual(v["header"], "Bearer <secret>")
        self.assertEqual(v["nested"]["list"][1], "x <secret> y")
        self.assertEqual(v["plain"], 42)

if __name__ == "__main__":
    unittest.main()

class WriteFileCapabilityTest(unittest.TestCase):
    def test_roundtrip_and_mime(self):
        from storage import blobstore
        from runtime import capability
        ref = capability.write_file("good_contacts.csv", "name,email\nA,a@b.no")
        self.assertTrue(ref.startswith("blob:"))
        self.assertEqual(blobstore.get(ref).decode(), "name,email\nA,a@b.no")
        st = blobstore.stat(ref)
        self.assertEqual(st.get("mime"), "text/csv")
        self.assertEqual(st.get("name"), "good_contacts.csv")
        with self.assertRaises(TypeError):
            capability.write_file("x.bin", 123)

    def test_runs_inside_the_sandbox_surface(self):
        from runtime import sandbox
        out = sandbox.run(
            "ref = write_file('list.txt', 'a\\nb')\n"
            "write_output('the_file', ref)", None, {}, {})
        self.assertTrue(str(out.get("the_file", "")).startswith("blob:"))

    def test_heartbeat_notes_surface_and_scrub(self):
        from runtime import sandbox
        notes = []
        code = ("import time\n"
                "heartbeat('fetching hunter2secret now')\n"
                "time.sleep(0.6)\n"
                "heartbeat('post 2 of 2')\n"
                "time.sleep(0.6)\n"
                "write_output('y', 1)\n")
        out = sandbox.run(code, None, {}, {"THE_KEY": "hunter2secret"},
                          on_progress=notes.append)
        self.assertEqual(out["y"], 1)
        self.assertTrue(any("post 2 of 2" in n for n in notes), notes)
        self.assertFalse(any("hunter2secret" in n for n in notes), notes)
        self.assertTrue(any("****" in n for n in notes), notes)

class SchemaDescriptionTest(unittest.TestCase):
    def test_port_description_lands_in_the_schema(self):
        from runtime import capability
        schema = capability.schema_from_ports([
            {"name": "invoice_amount_ex_vat", "type": "number",
             "label": "Amount ex VAT",
             "description": "The total of all costs including discounts, excluding VAT."},
            {"name": "vendor", "type": "text", "label": "Vendor"}])
        props = schema["properties"]
        self.assertEqual(props["invoice_amount_ex_vat"]["description"],
                         "The total of all costs including discounts, excluding VAT.")
        self.assertEqual(props["vendor"]["description"], "Vendor")
        self.assertEqual(schema["required"],
                         ["invoice_amount_ex_vat", "vendor"])

    def test_item_fields_enforce_a_per_item_shape(self):
        from runtime import capability
        schema = capability.schema_from_ports([
            {"name": "triaged_posts", "type": "any",
             "description": "One verdict per post",
             "item_fields": [
                 {"name": "verdict", "type": "enum",
                  "options": ["ENGAGE", "SKIP"], "description": "the call"},
                 {"name": "reason", "type": "text"}]}])
        frag = schema["properties"]["triaged_posts"]
        self.assertEqual(frag["type"], "array")
        items = frag["items"]
        self.assertEqual(items["required"], ["verdict", "reason"])
        self.assertEqual(items["properties"]["verdict"]["enum"],
                         ["ENGAGE", "SKIP"])
        self.assertFalse(items["additionalProperties"])
        self.assertEqual(frag["description"], "One verdict per post")

class AiOutputScaffoldingTest(unittest.TestCase):
    def test_strips_tooluse_tokens_and_field_wrapper(self):
        from runtime import capability
        self.assertEqual(
            capability._strip_scaffolding("today was quiet.</digest>\n</invoke>\n",
                                          "digest"),
            "today was quiet.")
        self.assertEqual(
            capability._strip_scaffolding("<digest>hello</digest>", "digest"),
            "hello")

    def test_real_content_and_other_tags_untouched(self):
        from runtime import capability
        v = "keep <b>bold</b> and <p>para</p>"
        self.assertEqual(capability._strip_scaffolding(v, "digest"), v)

    def test_clean_output_only_touches_declared_string_ports(self):
        from runtime import capability
        out = capability._clean_output(
            {"digest": "x</invoke>", "count": 3},
            [{"name": "digest"}, {"name": "count"}])
        self.assertEqual(out, {"digest": "x", "count": 3})

    def test_clean_output_parses_a_stringified_structured_field(self):
        from runtime import capability
        ports = [{"name": "rows", "type": "record",
                  "item_fields": [{"name": "u", "type": "text"}]},
                 {"name": "note", "type": "text"}]
        out = capability._clean_output(
            {"rows": '[{"u": "a"}, {"u": "b"}]', "note": '{"kept": "text"}'},
            ports)
        self.assertEqual(out["rows"], [{"u": "a"}, {"u": "b"}])
        self.assertEqual(out["note"], '{"kept": "text"}')
        out = capability._clean_output({"rows": "[broken json"}, ports)
        self.assertEqual(out["rows"], "[broken json")

class BrowserProfileTest(unittest.TestCase):
    def test_the_path_is_absolute_and_per_workflow(self):
        import config
        a = config.browser_profile_dir("proj_a")
        b = config.browser_profile_dir("proj_b")
        self.assertTrue(a.is_absolute())
        self.assertNotEqual(a, b)
        self.assertEqual(a.name, "proj_a")

    def test_a_cell_and_a_run_get_THE_SAME_folder(self):
        import config
        from agent import node_tools
        from runtime import executor
        self.assertEqual(str(config.browser_profile_dir("proj_x")),
                         str(config.browser_profile_dir("proj_x")))

    def test_the_capability_creates_and_returns_it(self):
        import config
        from runtime import capability
        d = config.browser_profile_dir("proj_cap")
        capability.bind({}, profile_dir=str(d))
        got = capability.browser_profile()
        self.assertEqual(got, str(d))
        self.assertTrue(Path(got).is_dir())

    def test_it_refuses_outside_a_step(self):
        from runtime import capability
        capability.bind({})
        with self.assertRaises(RuntimeError):
            capability.browser_profile()

    def test_step_code_is_not_handed_it(self):
        import config
        from runtime import sandbox
        with self.assertRaises(sandbox.NodeError):
            sandbox.run("write_output('where', browser_profile())", None,
                        {}, {},
                        profile_dir=str(config.browser_profile_dir("proj_sb")))

    def test_it_is_no_longer_a_step_name(self):
        from agent import gate
        for name in ("browser_profile", "browser_har_path", "browser_login",
                     "snapshot_page", "browser_snapshot"):
            self.assertNotIn(name, gate.RESERVED_NAMES)

class WriteOutputNamesTest(unittest.TestCase):
    def test_literal_names_are_read(self):
        from agent import gate
        names, dyn = gate.write_output_names(
            'write_output("a", 1)\nwrite_output(name="b", value=2)\n')
        self.assertEqual(names, {"a", "b"})
        self.assertFalse(dyn)

    def test_a_computed_name_is_reported_as_dynamic(self):
        from agent import gate
        names, dyn = gate.write_output_names(
            'write_output("a", 1)\nfor k in ks:\n    write_output(k, 2)\n')
        self.assertEqual(names, {"a"})
        self.assertTrue(dyn)

    def test_a_parse_error_reports_nothing(self):
        from agent import gate
        self.assertEqual(gate.write_output_names("def ("), (set(), False))

class TransientAndUnverifiedTest(unittest.TestCase):
    def test_a_read_that_times_out_is_resumable_not_a_failure(self):
        from runtime import run_state
        run_state.start("p_tr", "run_1", {})
        node = {"id": "n_r", "name": "Fetch the sheet", "type": "connector",
                "read_only": True}
        r = executor._service_halt("run_1", "n_r", node,
                                     TimeoutError("The read operation timed out"))
        self.assertEqual(r["reason"], "service-unavailable")
        self.assertIn("running again", " ".join(r["verdict"]["environment"]))

    def test_a_write_that_times_out_asks_whether_it_landed(self):
        from runtime import run_state
        run_state.start("p_tr", "run_2", {})
        node = {"id": "n_w", "name": "Save results to Google Sheet",
                "type": "connector", "read_only": False,
                "external_impact": "adds rows"}
        r = executor._service_halt("run_2", "n_w", node,
                                     TimeoutError("The read operation timed out"))
        self.assertEqual(r["reason"], "send-unverified")
        said = " ".join(r["verdict"]["environment"])
        self.assertIn("may or may not", said)
        self.assertIn("Check the outside system", said)

    def test_a_real_bug_is_still_a_real_failure(self):
        node = {"id": "n_w", "name": "Shape it", "type": "code"}
        self.assertIsNone(executor._service_halt(
            "run_3", "n_w", node, KeyError("missing-key")))

    def test_the_did_it_land_form_fills_what_the_asserts_pin_down(self):
        from runtime import run_state
        node = {"id": "n_w", "name": "Save rows", "type": "connector",
                "read_only": False,
                "outputs": [{"name": "status", "type": "number"},
                            {"name": "sheet_id", "type": "text"},
                            {"name": "updated_range", "type": "text"}],
                "tests": [{"name": "recorded run", "expect": "ok",
                           "asserts": ["status == 200",
                                       "sheet_id == sheet_id_in"]}]}
        run_state.start("p_uo", "run_4", {"sheet_id_in": "abc123"})
        fields = {f["name"]: f
                  for f in executor.unverified_output_fields(node, "run_4")}
        self.assertEqual((fields["status"]["value"],
                          fields["status"]["locked"]), (200, True))
        self.assertEqual((fields["sheet_id"]["value"],
                          fields["sheet_id"]["locked"]), ("abc123", True))

        self.assertFalse(fields["updated_range"]["locked"])
        self.assertEqual(fields["updated_range"]["value"], "")

    def test_unfire_lets_a_write_be_sent_again(self):
        from runtime import run_state
        run_state.start("p_uo2", "run_5", {})
        run_state.record_fired("run_5", "n_w")
        self.assertTrue(run_state.has_fired("run_5", "n_w"))
        run_state.clear_fired("run_5", "n_w")
        self.assertFalse(run_state.has_fired("run_5", "n_w"))

class OptionalValuesTest(unittest.TestCase):
    def test_an_optional_output_may_be_absent_or_empty(self):
        from runtime import port_checks
        ports = [{"name": "name", "type": "text"},
                 {"name": "photo_url", "type": "text", "optional": True}]
        self.assertEqual(port_checks.check_ports(
            ports, {"name": "Ada"}, require_all=True), [])
        self.assertEqual(port_checks.check_ports(
            ports, {"name": "Ada", "photo_url": None}, require_all=True), [])

        probs = port_checks.check_ports(ports, {"photo_url": "x"},
                                        require_all=True)
        self.assertEqual([p["port"] for p in probs], ["name"])

    def test_an_optional_item_field_may_be_missing_per_item(self):
        from runtime import port_checks
        ports = [{"name": "people", "type": "any", "item_fields": [
            {"name": "name", "type": "text", "required": True},
            {"name": "photo_url", "type": "text", "optional": True}]}]
        people = [{"name": "Ada", "photo_url": "https://x/a.jpg"},
                  {"name": "Bo"},
                  {"name": "Cy", "photo_url": ""}]
        self.assertEqual(port_checks.check_ports(ports, {"people": people}), [])

        probs = port_checks.check_ports(ports, {"people": [{"photo_url": "x"}]})
        self.assertIn("name is missing", probs[0]["problem"])

    def test_the_ai_schema_stops_requiring_optional_fields(self):
        from runtime import capability
        schema = capability.schema_from_ports([
            {"name": "verdict", "type": "text"},
            {"name": "note", "type": "text", "optional": True},
            {"name": "people", "type": "any", "item_fields": [
                {"name": "name", "type": "text"},
                {"name": "photo_url", "type": "text", "optional": True}]}])
        self.assertEqual(schema["required"], ["verdict", "people"])
        self.assertEqual(
            schema["properties"]["people"]["items"]["required"], ["name"])

    def test_a_missing_optional_input_runs_as_none_never_a_halt(self):
        from runtime import run_state
        run_state.start("p_opt", "run_opt", {})
        node = {"id": "n_o", "name": "Format card", "type": "code",
                "inputs": [{"name": "name", "type": "text"},
                           {"name": "photo_url", "type": "text",
                            "optional": True}]}
        inputs, missing = executor._gather_inputs(
            node, "run_opt", {"name": "Ada"}, None, {})
        self.assertEqual(missing, [])
        self.assertEqual(inputs, {"name": "Ada", "photo_url": None})

        inputs2, missing2 = executor._gather_inputs(
            node, "run_opt", {"name": "Ada"}, None, {"photo_url": ""})
        self.assertEqual(missing2, [])
        self.assertIsNone(inputs2["photo_url"])

    def test_the_run_form_never_asks_for_an_optional_value(self):
        p = {"id": "p_opt2", "name": "v", "edges": [], "variables": [
                 {"name": "photo_url", "label": "Photo", "value": ""}],
             "nodes": [{"id": "n_o", "name": "Format", "type": "code",
                        "config": {"code": "x = 1"},
                        "inputs": [{"name": "photo_url", "type": "text",
                                    "optional": True}], "outputs": []}]}
        self.assertEqual(executor.missing_at_start(p), [])

class SendingPreviewTest(unittest.TestCase):
    NODE = {"name": "Save rows", "type": "connector", "read_only": False,
            "inputs": [{"name": "sheet_link", "type": "text", "label": "Sheet"},
                       {"name": "rows", "type": "any", "label": "Rows"},
                       {"name": "api_key", "type": "secret", "label": "Key"}]}

    def test_lists_scalars_and_secrets_each_read_right(self):
        prev = {f["label"]: f["text"] for f in executor.sending_preview(
            self.NODE, {"sheet_link": "https://docs.google.com/x/edit",
                        "rows": [{"name": "Ada"}, {"name": "Bo"},
                                 {"name": "Cy"}],
                        "api_key": "api_key"})}
        self.assertEqual(prev["Sheet"], "https://docs.google.com/x/edit")

        self.assertEqual(prev["Rows"].split("\n"),
                         ["3 rows", '{"name": "Ada"}', '{"name": "Bo"}',
                          "...and 1 more"])
        self.assertNotIn("api", prev["Key"])
        self.assertIn("secret", prev["Key"])

    def test_a_long_value_carries_its_marker(self):
        prev = executor.sending_preview(
            {"name": "n", "type": "connector",
             "inputs": [{"name": "body", "type": "text"}]},
            {"body": "x" * 500})
        self.assertTrue(prev[0]["text"].endswith("..."))
        self.assertLessEqual(len(prev[0]["text"]),
                             executor.SENDING_VALUE_CLIP + 3)

    def test_the_halt_carries_it(self):
        from runtime import run_state
        run_state.start("p_sp", "run_sp", {})

        v = {"approval": "write connector awaiting approval",
             "sending": executor.sending_preview(self.NODE,
                                                 {"rows": [{"a": 1}]})}
        self.assertEqual(v["sending"][0]["label"], "Rows")

class WhatTheStepSawTest(unittest.TestCase):
    def test_the_traffic_log_keeps_the_last_exchanges(self):
        import http.client, threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from runtime import _sandbox_runner as runner

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(503); self.send_header("Content-Type", "text/plain")
                self.end_headers(); self.wfile.write(b"service unavailable, try later")
            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
        try:
            runner._TRAFFIC.clear()
            runner._install_traffic_log()
            c = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
            c.putrequest("GET", "/things?x=1")
            c.putheader("Authorization", "Bearer sk-secret-token")
            c.putheader("Accept", "text/plain")
            c.endheaders()
            r = c.getresponse(); body = r.read()
            self.assertIn(b"unavailable", body)
            saw = runner._saw({})
            t = saw["traffic"][-1]
            self.assertEqual((t["method"], t["path"], t["status"]), ("GET", "/things?x=1", 503))
            self.assertIn("service unavailable", t["response_head"])
            self.assertEqual(t["request_headers"]["Authorization"], "(sent)")
            self.assertNotIn("sk-secret-token", str(saw))
        finally:
            srv.shutdown()

    def test_the_page_at_failure_is_captured_from_a_page_object(self):
        from runtime import _sandbox_runner as runner, capability
        class FakePage:
            url = "https://example.test/login"
            def evaluate(self, js):
                return "<!DOCTYPE html><html><head><title>Sign in</title></head><body>Please sign in</body></html>"
            def screenshot(self, timeout=None):
                return b"\x89PNG fake"
        capability.bind({}, blob_owner="wfl:p_saw")
        saw = runner._saw({"page": FakePage(), "x": 1})
        pg = saw["page"]
        self.assertEqual(pg["url"], "https://example.test/login")
        self.assertEqual(pg["title"], "Sign in")
        self.assertTrue(str(pg["page"]).startswith("blob:"))
        self.assertTrue(str(pg["screenshot"]).startswith("blob:"))

    def test_the_browsers_own_recording_is_kept_and_the_window_closed(self):
        from runtime import _sandbox_runner as runner, capability
        from storage import blobstore
        closed = []
        class FakeContext:
            def close(self):
                closed.append(True)
                with open(capability.har_recording(), "w") as f:
                    f.write('{"log": {"entries": [{"request": {}}]}}')
        class FakePage:
            url = "https://example.test/x"
            context = FakeContext()
            def evaluate(self, js):
                return "<!DOCTYPE html><html><title>T</title></html>"
            def screenshot(self, timeout=None):
                return b"\x89PNG fake"
        capability.bind({}, blob_owner="wfl:p_har")
        capability.browser_har_path()
        saw = runner._saw({"page": FakePage()})
        self.assertTrue(closed)
        self.assertTrue(str(saw["har"]).startswith("blob:"))
        self.assertIn(b'"entries"', blobstore.get(saw["har"]))

    def test_the_halt_carries_the_stored_files_by_name(self):
        from runtime import executor, sandbox
        err = sandbox.NodeError("RuntimeError", "boom", detail={
            "saw": {"page": {"url": "https://x/y"}},
            "files": {"browser-traffic.har": "blob:aa"}})
        saw = executor._what_it_saw(err)
        self.assertEqual(saw["files"]["browser-traffic.har"], "blob:aa")
        self.assertEqual(saw["page"]["url"], "https://x/y")

    def test_every_file_the_step_stored_survives_the_failure(self):
        from runtime import _sandbox_runner as runner, capability
        capability.bind({}, blob_owner="wfl:p_files")
        capability.write_file("threads.json", b"[]")
        payload = runner._failure_payload("boom", "RuntimeError")
        self.assertIn("threads.json", payload["files"])
        self.assertTrue(payload["files"]["threads.json"].startswith("blob:"))

class DiskGuardTest(unittest.TestCase):
    def _run(self, code, roots=None):
        from runtime import sandbox
        return sandbox.run(code, None, {}, {}, path_roots=roots or [])

    def test_the_home_folder_is_refused(self):
        from runtime import sandbox
        code = ("import os\n"
                "write_output('n', len(os.listdir(os.path.expanduser('~'))))")
        with self.assertRaises(sandbox.NodeError) as cm:
            self._run(code)
        self.assertIn("path blocked", str(cm.exception))

    def test_the_apps_database_is_refused_even_when_declared(self):
        import config
        from runtime import sandbox
        code = ("import sqlite3\n"
                f"sqlite3.connect({str(config.DATA_DIR / 'app.db')!r}).close()\n"
                "write_output('ok', True)")
        with self.assertRaises(sandbox.NodeError) as cm:
            self._run(code, roots=[str(config.DATA_DIR)])
        self.assertIn("path blocked", str(cm.exception))

    def test_a_declared_folder_is_readable(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp(prefix="allowed_"))

        (d / "a.txt").write_text("hello")
        code = (f"write_output('t', open({str(d / 'a.txt')!r}).read())")
        self.assertEqual(self._run(code, roots=[str(d)])["t"], "hello")

    def test_the_capability_surface_still_reaches_the_app_files(self):
        from runtime import sandbox
        out = sandbox.run("ref = write_file('x.txt', 'abc')\n"
                          "write_output('ref', ref)", None, {}, {})
        ref = out["ref"]
        self.assertTrue(ref.startswith("blob:"))
        out2 = sandbox.run("write_output('back', read_input('doc'))", None,
                           {"doc": ref}, {})
        self.assertEqual(out2["back"], "abc")

    def test_scratch_and_temp_stay_writable(self):
        code = ("import tempfile, os\n"
                "p = os.path.join(tempfile.gettempdir(), 'cryo_guard_probe.txt')\n"
                "open(p, 'w').write('x')\n"
                "open('local.txt', 'w').write('y')\n"
                "write_output('ok', open('local.txt').read())")
        self.assertEqual(self._run(code)["ok"], "y")

class AskThePersonInTheWindowTest(unittest.TestCase):
    def setUp(self):
        import os
        self._was = os.environ.get("CRYOGRAM_CARD_PATIENCE")
        os.environ["CRYOGRAM_CARD_PATIENCE"] = "20"

    def tearDown(self):
        import os
        if self._was is None:
            os.environ.pop("CRYOGRAM_CARD_PATIENCE", None)
        else:
            os.environ["CRYOGRAM_CARD_PATIENCE"] = self._was

    def _run(self, code, **kw):
        from runtime import sandbox
        return sandbox.run(code, None, {}, {}, **kw)

    def test_the_step_waits_and_carries_on_when_confirmed(self):
        asked = []
        def handler(message):
            asked.append(message)
            return "answered"
        out = self._run("confirm_with_user('Log in, then confirm')\n"
                        "write_output('done', True)",
                        on_user_request=handler)
        self.assertEqual(out, {"done": True})
        self.assertEqual(asked, ["Log in, then confirm"])

    def test_a_step_that_is_not_a_browser_step_cannot_ask(self):
        from runtime import sandbox
        with self.assertRaises(sandbox.NodeError) as cm:
            self._run("confirm_with_user('do a thing')\nwrite_output('n', 1)")
        self.assertIn("only a browser step", str(cm.exception))

    def test_waiting_for_a_person_is_not_silence(self):
        import time
        def slow(message):
            time.sleep(1.5)
            return "answered"
        out = self._run("confirm_with_user('take your time')\n"
                        "write_output('done', True)",
                        timeout=10, on_user_request=slow)
        self.assertEqual(out, {"done": True})

    def test_nobody_answering_ends_the_step_with_its_own_kind(self):
        from runtime import sandbox
        with self.assertRaises(sandbox.NodeError) as cm:
            self._run("confirm_with_user('anyone there?')\n",
                      on_user_request=lambda m: "unanswered")
        self.assertEqual(cm.exception.kind, "NobodyAnsweredError")

class ClosedWindowIsAPauseTest(unittest.TestCase):
    def setUp(self):
        from runtime import run_state
        run_state.start("p_closed", "run_1")

    def _node(self):
        return {"id": "n_b", "name": "Read the page", "type": "browser"}

    def test_a_closed_window_pauses_rather_than_failing(self):
        from runtime import executor, sandbox
        err = sandbox.NodeError("BrowserClosedError", "the window was closed")
        halt = executor._type_reads_error("run_1", "n_b", self._node(), err)
        self.assertEqual(halt["reason"], "browser-closed")
        self.assertIn("Nothing is lost", halt["verdict"]["environment"][0])

    def test_chromes_own_words_count_too(self):
        from runtime import executor, sandbox
        err = sandbox.NodeError(
            "Error", "playwright: Target page, context or browser has been closed")
        self.assertEqual(
            executor._type_reads_error("run_1", "n_b", self._node(), err)["reason"],
            "browser-closed")

    def test_only_a_browser_step_reads_that_way(self):
        from runtime import executor, sandbox
        err = sandbox.NodeError("BrowserClosedError", "the window was closed")
        self.assertIsNone(executor._type_reads_error(
            "run_1", "n_c", {"id": "n_c", "name": "Write", "type": "connector"}, err))

    def test_the_banner_stands_over_both_of_them(self):
        from runtime import run_state
        self.assertTrue(run_state.waiting_on_you("browser-closed"))
        self.assertTrue(run_state.waiting_on_you("not-confirmed"))

class TheWindowIsTheAppsRuntimeTest(unittest.TestCase):
    class FakePage:
        def __init__(self):
            self.url = "about:blank"
            self.went = []
        def goto(self, url, **kw):
            self.went.append(url)
            self.url = url
        def wait_for_timeout(self, ms):
            pass
        def evaluate(self, js):
            return "<!DOCTYPE html><html><title>T</title></html>"

    def setUp(self):
        from runtime import capability
        capability.bind({}, blob_owner="wfl:p_win")
        self.page = self.FakePage()

    def test_navigating_keeps_the_page_and_remembers_it(self):
        from runtime import capability
        capability.browser_goto(self.page, "https://example.test/things")
        self.assertEqual(self.page.went, ["https://example.test/things"])
        self.assertEqual(capability.kept_snapshots()[-1]["url"],
                         "https://example.test/things")

    def test_a_login_redirect_is_undone(self):
        from runtime import capability
        capability.browser_goto(self.page, "https://example.test/things")
        self.page.url = "https://example.test/feed"
        capability._back_where_it_meant_to_be(self.page)
        self.assertEqual(self.page.went[-1], "https://example.test/things")

    def test_a_page_already_where_it_should_be_is_left_alone(self):
        from runtime import capability
        capability.browser_goto(self.page, "https://example.test/things")
        capability._back_where_it_meant_to_be(self.page)
        self.assertEqual(len(self.page.went), 1)

    def test_a_page_that_never_loads_stops_the_step_by_name(self):
        from runtime import capability
        class Dead(self.FakePage):
            def goto(self, url, **kw):
                raise RuntimeError("net::ERR_CONNECTION_REFUSED")
        with self.assertRaises(capability.UnexpectedInputError) as cm:
            capability.browser_goto(Dead(), "https://example.test/x", tries=2)
        self.assertIn("example.test", str(cm.exception))

    def test_only_the_newest_pages_are_kept(self):
        from runtime import capability
        for i in range(capability.SNAPSHOTS_KEPT + 4):
            capability.browser_goto(self.page, f"https://example.test/{i}")
        self.assertEqual(len(capability.kept_snapshots()),
                         capability.SNAPSHOTS_KEPT)

class WhatTheWindowSawTest(unittest.TestCase):
    class Shaped:
        def __init__(self, height=4200, scrolls_to=None):
            self.url = "https://example.test/list"
            self.height = height
            self.seen = 900
            self.scrolls_to = scrolls_to
            self.went = []

        def goto(self, url, **kw):
            self.url = url

        def wait_for_timeout(self, ms):
            pass

        def evaluate(self, js):
            if js.startswith("() => ({"):
                return {"seen": self.seen, "height": self.height,
                        "elements": 1240, "links": 38}
            if "scrollHeight" in js:
                return self.height
            return "<!DOCTYPE html><html><title>T</title></html>"

    def setUp(self):
        from runtime import capability
        capability.bind({}, blob_owner="wfl:p_saw2")
        capability._ctx["calls"] = []
        capability._ctx["calls_seen"] = 0

    def test_a_copy_says_how_much_of_the_page_it_holds(self):
        from runtime import capability
        page = self.Shaped()
        capability.snapshot_page(page)
        kept = capability.kept_snapshots()[-1]

        self.assertEqual((kept["seen"], kept["height"]), (900, 4200))
        self.assertEqual(kept["links"], 38)
        self.assertGreater(kept["bytes"], 0)

    def test_a_page_that_cannot_be_measured_is_still_kept(self):
        from runtime import capability
        class Mute(self.Shaped):
            def evaluate(self, js):
                if js.startswith("() => ({"):
                    raise RuntimeError("page is gone")
                return "<html></html>"
        capability.snapshot_page(Mute())
        self.assertNotIn("height", capability.kept_snapshots()[-1])

    class Watcher:
        def __init__(self):
            self.note = None

        def on(self, event, fn):
            self.note = fn

        def respond(self, url, kind="xhr", ctype="application/json",
                    length="900", status=200, method="GET"):
            headers = {"content-type": ctype}
            if length is not None:
                headers["content-length"] = length
            self.note(type("R", (), {
                "url": url, "status": status, "headers": headers,
                "request": type("Q", (), {"method": method,
                                          "resource_type": kind})()})())

    def test_every_kind_of_call_is_listed_images_included(self):
        from runtime import capability
        ctx = self.Watcher()
        capability._watch_calls(ctx)
        for i in range(3):
            ctx.respond(f"https://example.test/pics/shot-{i}.jpg",
                        kind="image", ctype="image/jpeg")
        seen = capability.browser_calls()
        self.assertEqual((seen["listed"], seen["total"]), (3, 3))
        self.assertEqual([c["kind"] for c in seen["calls"]], ["image"] * 3)

        self.assertEqual([c["name"] for c in seen["calls"]],
                         ["shot-0.jpg", "shot-1.jpg", "shot-2.jpg"])
        self.assertEqual(seen["calls"][0]["bytes"], 900)

    def test_a_call_carries_when_it_happened_so_a_heartbeat_reads_as_one(self):
        from runtime import capability
        ctx = self.Watcher()
        capability._watch_calls(ctx)

        ctx.respond("https://example.test/feed")
        capability._ctx["calls_t0"] -= 5.0
        ctx.respond("https://example.test/ping")
        capability._ctx["calls_t0"] -= 5.0
        ctx.respond("https://example.test/ping")
        ats = [c["at"] for c in capability.browser_calls()["calls"]]
        self.assertEqual(len(ats), 3)
        self.assertAlmostEqual(ats[2] - ats[1], 5.0, places=1)
        self.assertAlmostEqual(ats[1] - ats[0], 5.0, places=1)

    def test_an_answer_sent_in_pieces_carries_no_size_rather_than_a_zero(self):
        from runtime import capability
        ctx = self.Watcher()
        capability._watch_calls(ctx)
        ctx.respond("https://example.test/stream", length=None)
        ctx.respond("https://example.test/odd", length="not a number")
        self.assertNotIn("bytes", capability.browser_calls()["calls"][0])
        self.assertNotIn("bytes", capability.browser_calls()["calls"][1])

    def test_an_address_with_no_file_at_the_end_gets_no_name(self):
        from runtime import capability
        self.assertEqual(capability._file_name("https://example.test/things/"), "")
        self.assertEqual(capability._file_name("https://example.test/a%20b.pdf"),
                         "a b.pdf")
        self.assertEqual(capability._file_name("https://example.test/x.json?v=2"),
                         "x.json")

    def test_every_call_rides_folded_in_the_result_and_whole_in_a_file(self):
        from runtime import _sandbox_runner as runner
        from storage import blobstore
        import json as _json
        calls = [{"url": f"https://example.test/a-very-long-address/{i}" + "x" * 120,
                  "method": "GET", "status": 200, "kind": "xhr",
                  "type": "application/json", "bytes": i}
                 for i in range(900)]
        out = runner._listed_calls({"calls": calls, "listed": 900, "total": 900})
        self.assertEqual(len(out["calls"]), 1)
        self.assertEqual(out["calls"][0]["count"], 900)
        self.assertEqual(out["calls"][0]["ids"], list(range(900)))
        self.assertIn("unfold", out["calls"][0]["note"])
        self.assertEqual(out["calls_total"], 900)
        self.assertNotIn("calls_listed", out)
        whole = _json.loads(blobstore.get(out["calls_file"]).decode())
        self.assertEqual(len(whole), 900)
        self.assertEqual([c["id"] for c in whole][:3], [0, 1, 2])

    def test_an_error_quoting_an_input_names_the_input(self):
        from runtime import capability
        big = '{"log": {"entries": [' + ", ".join(["{}"] * 200) + "]}}"
        capability.bind({"har": big, "n": "7"})
        self.assertEqual(capability.read_input("har"), big)
        capability.read_input("n")
        err = f"[Errno 63] File name too long: {big!r}"
        named = capability.name_read_values(err)
        self.assertEqual(named, "[Errno 63] File name too long: <the contents of input 'har'>")
        self.assertEqual(capability.name_read_values("bad value 7"), "bad value 7")

class SettingReadInThePortsTypeTest(unittest.TestCase):
    def test_a_text_ten_reaches_a_number_port_as_ten(self):
        from runtime import run_state
        run_state.start("p_typ", "run_typ", {})
        node = {"id": "n_t", "name": "Count", "type": "code",
                "inputs": [{"name": "limit", "type": "number"},
                           {"name": "on", "type": "boolean"}]}
        inputs, missing = executor._gather_inputs(
            node, "run_typ", {}, None, {"limit": "10", "on": "TRUE"})
        self.assertEqual(missing, [])
        self.assertEqual(inputs, {"limit": 10, "on": True})

    def test_a_value_that_cannot_mean_the_type_is_asked_for(self):
        from runtime import run_state
        run_state.start("p_typ2", "run_typ2", {})
        node = {"id": "n_t", "name": "Count", "type": "code",
                "inputs": [{"name": "limit", "type": "number"}]}
        inputs, missing = executor._gather_inputs(
            node, "run_typ2", {}, None, {"limit": "ten"})
        self.assertEqual(missing, ["limit"])
        self.assertNotIn("limit", inputs)

class FoldedListingTest(unittest.TestCase):
    CALLS = [
        {"at": 0.4, "name": "feed", "method": "GET", "url": "https://x.com/api/feed?page=1", "status": 200, "kind": "xhr", "type": "application/json", "bytes": 900},
        {"at": 0.6, "name": "a.png", "method": "GET", "url": "https://x.com/img/a.png", "status": 200, "kind": "image", "type": "image/png", "bytes": 10},
        {"at": 5.4, "name": "feed", "method": "GET", "url": "https://x.com/api/feed?page=2", "status": 200, "kind": "xhr", "type": "application/json", "bytes": 900},
        {"at": 0.7, "name": "b.png", "method": "GET", "url": "https://x.com/img/b.png", "status": 404, "kind": "image", "type": "image/png", "bytes": 20},
        {"at": 10.4, "name": "feed", "method": "GET", "url": "https://x.com/api/feed?page=3", "status": 200, "kind": "xhr", "type": "application/json", "bytes": 900},
        {"at": 1.0, "name": "me", "method": "POST", "url": "https://x.com/api/me", "status": 200, "kind": "fetch", "type": "application/json"},
    ]

    def test_repeats_and_kinds_fold_with_their_ids(self):
        from runtime import capability
        rows = capability.fold_calls(self.CALLS)
        feed = next(r for r in rows if r.get("count") and r.get("kind") == "xhr")
        self.assertEqual(feed["ids"], [0, 2, 4])
        self.assertEqual(feed["count"], 3)
        self.assertEqual(feed["interval"], 5.0)
        self.assertIn("unfold", feed["note"])
        me = next(r for r in rows if r.get("url") == "https://x.com/api/me")
        self.assertEqual(me["id"], 5)
        images = next(r for r in rows if r.get("kind") == "image")
        self.assertEqual((images["count"], images["ids"], images["bytes"]), (2, [1, 3], 30))
        self.assertIn('kind="image"', images["note"])
        self.assertEqual(sum(r.get("count", 1) for r in rows), len(self.CALLS))

    def test_the_calls_file_and_the_recording_read_with_filters(self):
        from runtime import capability
        whole = [{"id": i, **c} for i, c in enumerate(self.CALLS)]
        capability._ctx["inputs"] = {"calls": whole, "har": {"log": {"entries": [
            {"_resourceType": "image", "request": {"method": "GET", "url": "https://x.com/img/a.png"},
             "response": {"status": 200, "content": {"mimeType": "image/png"}}},
            {"_resourceType": "xhr", "request": {"method": "POST", "url": "https://x.com/api/me"},
             "response": {"status": 200, "content": {"mimeType": "application/json"}}}]}},
            "page": "<html></html>"}
        try:
            self.assertEqual([c["id"] for c in capability.read_input("calls", kind="image")], [1, 3])
            self.assertEqual([c["id"] for c in capability.read_input("calls", ids=[0, 4])], [0, 4])
            self.assertEqual([c["id"] for c in capability.read_input("calls", url="/api/", status=200, method="POST")], [5])
            har = capability.read_input("har", kind="image")
            self.assertEqual(len(har["log"]["entries"]), 1)
            self.assertEqual(har["log"]["entries"][0]["request"]["url"], "https://x.com/img/a.png")
            with self.assertRaises(capability.InputContractError):
                capability.read_input("page", kind="image")
            with self.assertRaises(capability.InputContractError):
                capability.read_input("calls", kind="image", path=True)
        finally:
            capability._ctx["inputs"] = {}
