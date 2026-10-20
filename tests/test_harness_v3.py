import ast
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests import _bootstrap
from agent import actions, build_readiness, cell_gates, cells, context, input_refs
from agent import node_tools, operating_context, skills, turnstate
from runtime import capability, contracts
from storage import blobstore, store

class RuntimeContractTest(unittest.TestCase):
    def test_reference_is_generated_from_bound_functions(self):
        for name, function in contracts.surface().items():
            sig = inspect.signature(function)
            self.assertEqual(list(sig.parameters), list(contracts.signature(name).parameters))
            if name != "ai_call":
                self.assertIn(f"{name}{contracts.signature(name)}", contracts.reference())
        self.assertIn("dict {key: result}", contracts.reference())

    def test_bad_calls_are_rejected_before_execution(self):
        p = _bootstrap.workflow(pid="v3_contract")
        c = SimpleNamespace(workflow=p, is_browser=False, plan_step=None, inputs={},
                            code="read_input('x', default=0)\nmark_sent('id')\nwrite_file('x')")
        got = cell_gates.gate_code_check(c)
        self.assertIn("read_input", got["error"])
        self.assertIn("mark_sent", got["error"])
        self.assertIn("write_file", got["error"])
        self.assertEqual(contracts.check_calls(ast.parse("write_file('x.csv', 'a,b\\n1,2')")), [])
        self.assertEqual(contracts.check_calls(ast.parse("read_input(*args, **kwargs)")), [])

    def test_reversed_file_arguments_do_not_create_a_blob(self):
        with patch.object(blobstore, "put") as put:
            for name in (b"binary", "a,b\n1,2", "", "x" * 256, "a\x00b"):
                with self.assertRaisesRegex(TypeError, "filename first"):
                    capability.write_file(name, "output.csv")
            put.assert_not_called()

    def test_invoice_record_fields_reach_the_enforced_schema(self):
        schema = capability.schema_from_ports([{
            "name": "invoice", "type": "record", "item_fields": [
                {"name": "invoice_number", "type": "text", "description": "Invoice identifier", "required": True},
                {"name": "total", "type": "number", "description": "Final invoice total", "required": True}]}])
        record = schema["properties"]["invoice"]
        self.assertEqual(set(record["properties"]), {"invoice_number", "total"})
        self.assertEqual(set(record["required"]), {"invoice_number", "total"})
        self.assertEqual(record["properties"]["total"]["description"], "Final invoice total")

    def test_nested_ai_field_meanings_survive_records_and_lists(self):
        for kind in ("record", "list"):
            schema = capability.schema_from_ports([{"name": "invoice", "type": kind,
                "item_fields": [{"name": "amounts", "type": "record", "item_fields": [
                    {"name": "total", "type": "number", "description": "Including tax, not subtotal"}]}]}])
            frag = schema["properties"]["invoice"]
            obj = frag if kind == "record" else frag["items"]
            field = obj["properties"]["amounts"]["properties"]["total"]
            self.assertEqual(field["description"], "Including tax, not subtotal")

class InputReferenceTest(unittest.TestCase):
    def setUp(self):
        self.p = _bootstrap.workflow(pid=self.id().rsplit(".", 1)[-1])
        self.ref = blobstore.put(b"amount\n100\n", mime="text/csv", meta={"name": "sample.csv"},
                                 owner=blobstore.workflow_owner(self.p["id"]))
        self.p["samples"] = [{"name": "sample.csv", "ref": self.ref, "mime": "text/csv"}]
        store.save(self.p)

    def test_sample_content_also_supplies_usable_reference(self):
        got = node_tools.tool_read_sample(self.p, "sample.csv")
        self.assertEqual(got["ref"], self.ref)
        self.assertIn("run_ai_step", got["input_hint"])
        fixed, error = input_refs.resolve(self.p, {"document": "sample.csv"}, {})
        self.assertFalse(error)
        self.assertEqual(fixed["document"], self.ref)

    def test_ambiguous_names_fail_without_guessing(self):
        self.p["samples"].append({"name": "sample.csv", "ref": "blob:" + "a" * 64})
        store.save(self.p)
        self.assertIn("ambiguous", input_refs.resolve(self.p, {"doc": "sample.csv"}, {})[1])
        self.assertIn("ambiguous", node_tools.tool_read_sample(self.p, "sample.csv")["error"])
        self.assertEqual(input_refs.resolve(self.p, {"doc": self.ref}, {})[0]["doc"], self.ref)

    def test_ai_resolves_filename_before_model_selection(self):
        self.p["plan"] = {"nodes": [{"name": "Read document", "type": "ai",
                                      "inputs": [{"name": "doc", "type": "file"}]}]}
        store.save(self.p)
        with patch.object(node_tools, "_proposal_first", return_value=None), \
                patch.object(node_tools, "pick_or_ask", return_value={"error": "stop before any paid call"}) as pick:
            node_tools.tool_run_ai_step(self.p, "Read document", "Read the document", inputs={"doc": "sample.csv"})
        self.assertEqual(pick.call_args.args[2]["doc"], self.ref)

class OperatingContextTest(unittest.TestCase):
    def test_planning_refuses_execution_but_allows_preparation(self):
        p = _bootstrap.workflow(pid="v3_phase")
        state = operating_context.availability(p)
        self.assertEqual(state["phase"], "planning")
        self.assertIn("save_plan", state["available_tools"])
        with patch.object(actions, "_execute") as handler:
            got = actions.execute(p, {"name": "run_cell", "input": {"name": "Probe", "code": "pass"}}, None)
        handler.assert_not_called()
        self.assertEqual(got["failure_stage"], "lifecycle")
        p["plan"] = {"nodes": [{"name": "Transform", "type": "code"}]}
        store.save(p)
        self.assertIn("run_cell", operating_context.availability(p)["available_tools"])
        turnstate.of(p).ask_open = True
        self.assertEqual(operating_context.availability(p)["available_tools"], [])

    def test_fix_and_change_gates_and_terminal_states(self):
        p = _bootstrap.workflow(pid="v3_fix_phase")
        p["plan"] = {"nodes": [{"name": "Read", "type": "code"}], "ticket_id": "ticket"}
        store.save(p)
        self.assertEqual(operating_context.availability(p)["phase"], "fix-declaration")
        self.assertTrue(operating_context.refusal(p, "run_ai_step"))
        turnstate.of(p).pending_fix = {"ticket_id": "ticket"}
        self.assertEqual(operating_context.availability(p)["available_tools"], [])
        turnstate.of(p).pending_fix = None
        turnstate.of(p).built = "done"
        self.assertEqual(operating_context.availability(p)["phase"], "finished")

    def test_guidance_is_type_selected_supplied_once_and_restored_next_turn(self):
        p = _bootstrap.workflow(pid="v3_guidance")
        p["plan"] = {"nodes": [{"name": "Compute", "type": "code"}]}
        store.save(p)
        body = skills.read_static("63-code-practice")["content"]
        first, ids = operating_context.supply(p)
        self.assertEqual(ids, {"63-code-practice"})
        self.assertEqual(first.count(body), 1)
        self.assertEqual(operating_context.supply(p)[0], "")
        self.assertEqual(context.build(p, "Continue")[0]["content"].count(body), 1)
        p["plan"]["nodes"].append({"name": "Judge", "type": "ai",
                                     "inputs": [{"name": "doc", "type": "file"}]})
        fresh, _ = operating_context.supply(p)
        self.assertIn("62-ai-practice/files", fresh)
        self.assertNotIn(body, fresh)

    def test_explicit_skill_load_does_not_duplicate_its_body_in_result(self):
        p = _bootstrap.workflow(pid="v3_load_once")
        got = actions.execute(p, {"name": "load_skill", "input": {"id": "browser"}}, None)
        self.assertTrue(got["ok"])
        self.assertNotIn("operating_guidance", got)

class BuildReadinessTest(unittest.TestCase):
    def setUp(self):
        self.p = _bootstrap.workflow(pid=self.id().rsplit(".", 1)[-1])
        self.node = {"name": "Filter", "type": "code", "inputs": [{"name": "minimum", "type": "number"}],
                     "outputs": []}
        self.plan = {"nodes": [self.node]}
        self.p["variables"] = [{"name": "minimum", "value": "", "persistent": True}]
        self.rec = {"inputs": {"minimum": 100}, "output": {}, "packages": []}

    def check(self):
        with patch.object(cells, "latest_ok_for", return_value=self.rec):
            return build_readiness.check(self.p, self.plan)

    def test_known_setting_must_not_exist_only_in_the_test(self):
        self.assertIn("only in the test", " ".join(self.check()))
        self.p["variables"][0]["value"] = 100
        self.assertEqual(self.check(), [])
        self.p["variables"][0]["value"] = "date,amount\n2026,100"
        self.assertIn("number", " ".join(self.check()))

    def test_per_run_values_secrets_and_untried_steps_are_not_invented(self):
        self.plan["nodes"].insert(0, {"name": "Ask", "type": "user-input",
                                       "outputs": [{"name": "minimum", "type": "number"}]})
        self.assertEqual(self.check(), [])
        self.plan["nodes"].pop(0)
        self.node["inputs"][0]["type"] = "secret"
        self.assertEqual(self.check(), [])
        self.rec = None
        self.assertEqual(self.check(), [])

    def test_build_tool_cannot_arm_when_readiness_fails(self):
        self.p.update(plan={**self.plan, "status": "draft"})
        from agent import plan_save
        with patch.object(node_tools, "untested_steps", return_value=[]), \
                patch.object(plan_save, "gaps_now", return_value=[]), \
                patch.object(build_readiness, "check", return_value=["setting missing"]):
            got = node_tools.tool_build_workflow(self.p)
        self.assertIn("setting missing", got["error"])
        self.assertFalse(turnstate.of(self.p).build_now)

    def test_sheet_test_inputs_do_not_silently_become_settings(self):
        self.node["inputs"] = [{"name": "sheet_url", "type": "text"},
                               {"name": "tab", "type": "text"},
                               {"name": "maximum", "type": "number"}]
        self.p["variables"] = [{"name": p["name"], "value": "", "persistent": True}
                               for p in self.node["inputs"]]
        self.rec["inputs"] = {"sheet_url": "https://example.invalid/sheet", "tab": "Log", "maximum": 5}
        findings = self.check()
        for name in self.rec["inputs"]:
            self.assertTrue(any(f'"{name}"' in f for f in findings), findings)
        self.assertTrue(all(v["value"] == "" for v in self.p["variables"]))
        for v in self.p["variables"]:
            v["value"] = self.rec["inputs"][v["name"]]
        self.p["variables"][-1]["value"] = 100
        self.assertEqual(self.check(), [])
        self.assertEqual(self.p["variables"][-1]["value"], 100)

    def test_recorded_dependencies_cannot_be_silently_dropped(self):
        self.p["variables"][0]["value"] = 100
        self.rec["packages"] = ["pypdf", "openpyxl"]
        self.node["packages"] = ["pypdf"]
        self.assertIn("openpyxl", " ".join(self.check()))
        self.node.pop("packages")
        self.assertEqual(self.check(), [])

    def test_artifact_existence_and_recognised_format(self):
        owner = blobstore.workflow_owner(self.p["id"])
        def put(name, data):
            return blobstore.put(data, meta={"name": name}, owner=owner)
        self.assertTrue(build_readiness.artifact_problem("blob:" + "0" * 64, owner))
        self.assertTrue(build_readiness.artifact_problem(put("bad.csv", b"a,b\n1\n"), owner))
        self.assertTrue(build_readiness.artifact_problem(put("bad.json", b"{no"), owner))
        self.assertTrue(build_readiness.artifact_problem(put("bad.pdf", b"plain text"), owner))
        self.assertFalse(build_readiness.artifact_problem(put("good.csv", b'a,b\n1,"x,y"\n'), owner))

if __name__ == "__main__":
    unittest.main()
