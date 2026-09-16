# Tests: node-tool seams: run_node's connector refusal, deopt-guard plumbing on set_criteria, and the agent/freeze tool-surface separation
from __future__ import annotations

import copy
import json
import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

def _pdf_with_text(text):
    objs = [b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]"
            + (b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>" if text else b"") + b">>"]
    if text:
        stream = f"BT /F1 18 Tf 20 40 Td ({text}) Tj ET".encode()
        objs.append(b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream")
        objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offsets:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out

from agent import node_tools
from agent import turnstate

def _user_approved(p):
    import time as _t
    node_tools.append_plan_entry(p, p["plan"])
    card = node_tools._transcript.last_request(p, "blueprint")
    if card:
        node_tools._transcript.append_answer(
            p, card["iid"], node_tools_actions_ok(), shown=node_tools_actions_ok())
    p["plan_approved_ts"] = _t.time()

def node_tools_actions_ok():
    from agent import actions
    return actions.PLAN_OK_LABEL

class RunNodeTest(unittest.TestCase):
    def test_never_connectors(self):
        p = _workflow([{"id": "n1", "name": "send", "type": "connector",
                       "config": {}, "inputs": [], "outputs": [], "tests": []}])
        r = node_tools.tool_run_node(p, "n1", {})
        self.assertIn("never runs a connector", r["error"])

class ConnectDataTest(unittest.TestCase):
    def _two(self, out_name, in_name):
        return _workflow([
            {"id": "a", "name": "a", "type": "code", "config": {"code": "x=1"},
             "inputs": [], "outputs": [{"name": out_name, "type": "text"}], "tests": []},
            {"id": "b", "name": "b", "type": "code", "config": {"code": "x=1"},
             "inputs": [{"name": in_name, "type": "text"}], "outputs": [], "tests": []}])

    def test_an_edge_touches_no_port(self):
        p = self._two("val", "val")
        r = node_tools.connect_nodes(p, "a", "b")
        self.assertTrue(r["ok"])
        self.assertEqual(r["order"], "a -> b")
        self.assertNotIn("source", p["nodes"][1]["inputs"][0])
        self.assertEqual(p["edges"], [{"src": "a", "dst": "b", "when": ""}])

    def test_read_node_never_hands_back_a_dead_port_field(self):
        p = self._two("val", "val")
        p["nodes"][1]["inputs"][0]["source"] = "a"
        out = node_tools.tool_read_node(p, "b")
        self.assertNotIn("source", out["inputs"][0])
        self.assertEqual(out["inputs"][0]["name"], "val")
        self.assertEqual(p["nodes"][1]["inputs"][0]["source"], "a")

    def test_dataless_no_when_is_a_legal_order_edge(self):
        p = self._two("val", "other")
        r = node_tools.connect_nodes(p, "a", "b")
        self.assertTrue(r["ok"])
        self.assertEqual(r["order"], "a -> b")
        self.assertEqual(len(p["edges"]), 1)

    def test_dataless_with_when_allowed(self):
        p = self._two("val", "other")
        r = node_tools.connect_nodes(p, "a", "b", when="val == 'x'")
        self.assertTrue(r["ok"])
        self.assertEqual(len(p["edges"]), 1)

    def test_reconnecting_identical_edge_is_a_quiet_ok(self):
        p = self._two("val", "val")
        self.assertTrue(node_tools.connect_nodes(p, "a", "b")["ok"])
        r = node_tools.connect_nodes(p, "a", "b")
        self.assertTrue(r.get("ok"), r)
        self.assertIn("already wired", r["note"])
        self.assertEqual(len(p["edges"]), 1)
        r2 = node_tools.connect_nodes(p, "a", "b", when="val == 'x'")
        self.assertIn("different condition", r2.get("error", ""))

class StateEchoTest(unittest.TestCase):
    def test_write_tools_echo_state(self):
        p = _workflow([])
        r = node_tools._create_node(p, "make it", "code", intention="x")
        self.assertEqual(r["state"]["name"], "make it")
        nid = r["node_id"]
        r = node_tools._set_declared_io(
            p, nid, [{"name": "a", "type": "text"}], [{"name": "b", "type": "text"}])
        self.assertEqual(r["state"]["inputs"], ["a:text"])
        r = node_tools._set_code(p, nid, "b = read_input('a').upper()\nwrite_output('b', b)")
        self.assertTrue(r["state"]["has_code"])
        r = node_tools._set_tests(p, nid, [{"name": "t", "inputs": {"a": "x"},
                                                "expect": "ok", "asserts": []}])
        self.assertEqual(r["state"]["tests"], 1)

    def test_validate_lists_not_ready_steps(self):
        p = _workflow([
            {"id": "n1", "name": "done step", "type": "code",
             "config": {"code": "write_output('y', read_input('x'))"},
             "inputs": [{"name": "x", "type": "text"}],
             "outputs": [{"name": "y", "type": "text"}],
             "tests": [{"name": "t", "inputs": {"x": "v"}, "expect": "ok", "asserts": []}]},
            {"id": "n2", "name": "empty step", "type": "code", "config": {},
             "inputs": [], "outputs": [], "tests": []}])
        r = node_tools.validate_node(p, "n1")
        self.assertEqual(r["not_ready"], ["empty step"])

class SetModelConfigTest(unittest.TestCase):
    def _ai(self):
        return _workflow([{"id": "n_ai", "name": "judge", "type": "ai", "config": {},
                          "inputs": [], "outputs": [{"name": "v", "type": "text"}],
                          "tests": []}])

    def test_blank_model_allowed(self):
        p = self._ai()
        r = node_tools.set_prompt_model(p, "n_ai", "do it", "")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(p["nodes"][0]["config"]["model"]["model"], "")

    def test_unconfigured_model_refused(self):
        p = self._ai()
        r = node_tools.set_prompt_model(p, "n_ai", "do it", "no-such-model")
        self.assertIn("error", r)
        self.assertIn("not set up", r["error"])
        self.assertNotIn("model", p["nodes"][0].get("config", {}))

    def test_the_provider_is_stored_with_the_model_and_checked_against_it(self):
        from storage import settings, secrets_store
        settings.update({"providers": [
            {"id": "p_a", "name": "A", "adapter": "anthropic", "auth": "api-key",
             "use": "workflow", "key_name": "A_KEY", "tags": [],
             "models": [{"name": "twin-model"}]},
            {"id": "p_b", "name": "B", "adapter": "anthropic", "auth": "api-key",
             "use": "workflow", "key_name": "B_KEY", "tags": [],
             "models": [{"name": "twin-model"}]}]})
        for k in ("A_KEY", "B_KEY"):
            secrets_store.set_secret(k, "k", secrets_store.OWNER_APP)
        p = self._ai()
        r = node_tools.set_prompt_model(p, "n_ai", "do it", "twin-model", provider_id="p_b")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(p["nodes"][0]["config"]["model"]["provider_id"], "p_b")
        r = node_tools.set_prompt_model(p, "n_ai", "do it", "twin-model", provider_id="p_nope")
        self.assertIn("error", r)
        self.assertIn("under provider", r["error"])

        p2 = _workflow([], pid="p_prov_plan")
        p2["plan"] = {"nodes": [
            {"id": "s1", "name": "Judge it", "type": "ai", "prompt": "Judge {x}.",
             "model": "twin-model", "provider": "b",
             "inputs": [{"name": "x", "type": "text"}],
             "outputs": [{"name": "verdict", "type": "text"}],
             "tests": [{"name": "authored", "inputs": {"x": "a"}, "expect": "ok",
                        "asserts": []}]}], "edges": []}
        r = node_tools.build_step(p2, "Judge it")
        self.assertTrue(r["ok"], r)
        self.assertEqual(node_tools.tool_read_node(p2, "Judge it")["config"]["model"]
                         ["provider_id"], "p_b")
        p2["plan"]["nodes"][0]["provider"] = "nobody"
        r = node_tools.build_step(p2, "Judge it")
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("not a workflow provider" in n for n in r.get("notes", [])), r)
        self.assertEqual(node_tools.tool_read_node(p2, "Judge it")["config"]["model"]
                         ["provider_id"], "")

class TicketShapeTest(unittest.TestCase):
    def test_agent_toolset_has_no_write_surface(self):
        overlap = set(node_tools.AGENT_TOOL_NAMES) & set(node_tools.NODE_WRITE_TOOL_NAMES)
        self.assertEqual(overlap, set())
        for name in ("save_plan", "save_learning", "save_intent", "read_cases",
                     "diagnose_case",
                     "query_corpus", "corpus_summary", "run_node"):
            self.assertIn(f"mcp__cryogram__{name}", node_tools.AGENT_TOOL_NAMES)

class ScrappyValidateTest(unittest.TestCase):
    def _doubler(self, pid):
        p = _workflow([], pid=pid)
        node_tools._create_node(p, "dbl", "code", "doubles a")
        nid = [n for n in p["nodes"] if n["name"] == "dbl"][0]["id"]
        node_tools._set_declared_io(
            p, nid, inputs=[{"name": "a", "type": "number", "label": "A"}],
            outputs=[{"name": "out", "type": "number", "label": "Out"}])
        node_tools._set_code(p, nid, "write_output('out', read_input('a') * 2)\n")
        return p, nid

    def test_green_without_criteria(self):
        p, nid = self._doubler("p_scrappy")
        node_tools._set_tests(p, nid, [
            {"name": "happy", "inputs": {"a": 2}, "expect": "ok", "asserts": ["out == 4"]}])
        v = node_tools.validate_node(p, nid)
        self.assertTrue(v["ok"], v)
        self.assertNotIn("criteria", v["missing"])
        self.assertIn("criteria", v.get("advisory", []))

    def test_missing_evidence_no_longer_blocks(self):
        p, nid = self._doubler("p_scrappy2")
        v = node_tools.validate_node(p, nid)
        self.assertTrue(v["ok"], v)
        self.assertFalse(v["checklist"]["tests_authored"])

class NodeHygieneTest(unittest.TestCase):
    def _confirm(self, pid):
        p = _workflow([], pid=pid)
        node_tools._create_node(p, "confirm", "code", "check switch",
                                    intention="verify the mac switched network")
        nid = [n for n in p["nodes"] if n["name"] == "confirm"][0]["id"]
        node_tools._set_declared_io(
            p, nid, inputs=[{"name": "chosen", "type": "text", "label": "Chosen"}],
            outputs=[{"name": "matched", "type": "boolean", "label": "Matched"}])
        return p, nid

    def test_set_code_rejects_hardcoded_output(self):
        p, nid = self._confirm("p_fake1")
        r = node_tools._set_code(p, nid, "write_output('matched', True)\n")
        self.assertFalse(r.get("ok"))
        self.assertTrue(r.get("violations"))

        r2 = node_tools._set_code(
            p, nid, "write_output('matched', read_input('chosen') == 'x')\n")
        self.assertTrue(r2.get("ok"), r2)

    def test_validate_not_green_when_fake(self):
        p, nid = self._confirm("p_fake2")

        node = node_tools.find_node(p, nid)
        node["config"]["code"] = "write_output('matched', True)\n"
        node["tests"] = [{"name": "t", "inputs": {"chosen": "x"}, "expect": "ok",
                          "asserts": ["matched == True"]}]
        v = node_tools.validate_node(p, nid)
        self.assertFalse(v["ok"])
        self.assertIn("real_implementation", v["missing"])

    def test_intention_stored_and_delete_prunes_it(self):
        p, nid = self._confirm("p_int")
        self.assertEqual(node_tools.tool_list_nodes(p)["nodes"][0]["intention"],
                         "verify the mac switched network")
        self.assertEqual(node_tools.tool_read_node(p, nid)["intention"],
                         "verify the mac switched network")
        node_tools.delete_node(p, nid)
        self.assertEqual(p["nodes"], [])

    def test_delete_node_clears_its_edges_and_leaves_others_alone(self):
        p = _workflow([
            {"id": "n_a", "name": "a", "type": "code", "config": {}, "inputs": [],
             "outputs": [{"name": "x", "type": "text"}], "tests": []},
            {"id": "n_b", "name": "b", "type": "code", "config": {},
             "inputs": [{"name": "x", "type": "text"}],
             "outputs": [], "tests": []}], pid="p_del")
        p["edges"] = [{"src": "n_a", "dst": "n_b"}]
        before = copy.deepcopy(p["nodes"][1])
        node_tools.delete_node(p, "n_a")
        self.assertEqual([n["id"] for n in p["nodes"]], ["n_b"])
        self.assertEqual(p["edges"], [])
        self.assertEqual(p["nodes"][0], before)

class AiTryBelongsToAStepTest(unittest.TestCase):
    def _planned(self, pid, *steps):
        p = _workflow([], pid=pid)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")

        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": s, "type": "code", "code_sketch": f"does {s}",
             "outputs": []} for s in steps]})
        return p

    def test_an_unplanned_ai_try_is_refused_and_names_the_real_steps(self):
        p = self._planned("p_aistep1", "Triage posts")
        r = node_tools.tool_run_ai_step(p, "Triage batch 7", "judge these",
                                        "claude-haiku-4-5-20251001")
        self.assertIn("error", r)
        self.assertIn("no step called", r["error"])
        self.assertIn("Triage posts", r["error"])

    def test_a_probe_CELL_keeps_its_freedom(self):
        p = self._planned("p_aistep2", "Triage posts")
        r = node_tools.tool_run_cell(p, "check the sheet's column names",
                                     "write_output('cols', ['a','b'])")
        self.assertTrue(r.get("ok"), r)

    def test_a_planned_step_name_passes_the_gate(self):
        p = self._planned("p_aistep3", "Triage posts")
        self.assertIsNone(node_tools._step_must_exist(p, "Triage posts"))

    def test_a_built_step_counts_too(self):
        p = _workflow([{"id": "n1", "name": "Judge it", "type": "ai",
                       "config": {}, "inputs": [], "outputs": [], "tests": []}],
                     pid="p_aistep4")
        self.assertIsNone(node_tools._step_must_exist(p, "Judge it"))

class RunCellTest(unittest.TestCase):
    def _skeleton(self, p, *steps):
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")

        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": s, "type": "code", "code_sketch": f"does {s}",
             "outputs": []} for s in steps]})

    def test_stop_reaches_the_sandbox_and_records_nothing(self):
        from unittest import mock

        from runtime import sandbox as _sandbox
        p = _workflow([], pid="p_cellstop")
        self._skeleton(p, "Fetch things")
        seen = {}

        def fake_run(*a, **kw):
            seen.update(kw)
            raise _sandbox.NodeStopped("stopped by the user mid-step")
        with mock.patch.object(node_tools.sandbox, "run", fake_run):
            r = node_tools.tool_run_cell(p, "Fetch things",
                                         "write_output('y', 'x')", {})
        self.assertTrue(callable(seen.get("should_stop")))
        self.assertFalse(seen["should_stop"]())
        self.assertEqual(r, {"ok": False, "error": "stopped by the user",
                             "cell": "Fetch things",
                             "seconds": r["seconds"]})
        from agent import cells as _cells
        self.assertNotIn("Fetch things",
                         {c["name"] for c in _cells.inventory(p["id"])})

    def test_cell_runs_records_and_fills_the_plan(self):
        p = _workflow([], pid="p_cells")
        self._skeleton(p, "Extract text")
        r = node_tools.tool_run_cell(
            p, "Extract text",
            "write_output('y', read_input('x').upper())", {"x": "abc"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["output"], {"y": "ABC"})
        self.assertEqual(r["cell"], "Extract text")
        sp = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Extract text", "type": "code",
             "inputs": [{"name": "x", "type": "text"}],
             "outputs": [{"name": "y", "type": "text"}]}]})
        self.assertTrue(sp["ok"], sp)
        self.assertEqual(sp.get("assembled_from_cells"), ["Extract text"])
        pn = p["plan"]["nodes"][0]
        self.assertIn("write_output('y'", pn["code"])
        self.assertEqual(pn["tests"][0]["inputs"], {"x": "abc"})

    def test_cells_recorded_after_the_plan_still_fill_at_build(self):
        p = _workflow([], pid="p_cells3")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Extract", "type": "code", "code_sketch": "reads the pdf",
             "outputs": [{"name": "y", "type": "text"}]}]})
        self.assertNotIn("code", p["plan"]["nodes"][0])
        node_tools.tool_run_cell(p, "Extract",
                                 "write_output('y', 'ok')", {})
        filled = node_tools.fill_from_cells(p["id"], p["plan"]["nodes"])
        self.assertEqual(filled, ["Extract"])
        self.assertIn("write_output('y'", p["plan"]["nodes"][0]["code"])

    def test_fixture_fidelity_rails(self):
        p = _workflow([], pid="p_cells4")
        p["samples"] = [{"name": "cv.pdf", "ref": "blob:" + "b" * 64,
                         "mime": "application/pdf"}]
        from storage import store as _st
        _st.save(p)
        self._skeleton(p, "Read", "Judge")

        r = node_tools.tool_run_cell(p, "Read", "write_output('y', 1)",
                                     {"pdf": "/x/data/blobs/abc"})
        self.assertIn("error", r)
        r2 = node_tools.tool_run_cell(
            p, "Read", "write_output('got', read_input('pdf'))", {"pdf": "cv.pdf"})

        from agent import cells as _c
        rec = [x for x in _c._load("p_cells4") if x["name"] == "Read"][-1]
        self.assertTrue(str(rec["inputs"]["pdf"]).startswith("blob:"))

        r3 = node_tools.tool_run_cell(p, "Judge", "ai_call('x', {}, {})", {})
        self.assertIn("run_ai_step", r3.get("error", ""))

    def test_recorded_chaining_between_cells(self):
        p = _workflow([], pid="p_cells5")
        self._skeleton(p, "Extract", "Shout", "Nope")
        node_tools.tool_run_cell(p, "Extract", "write_output('text', 'hello')", {})
        r = node_tools.tool_run_cell(
            p, "Shout", "write_output('loud', read_input('text').upper())",
            {"text": "$recorded"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["output"], {"loud": "HELLO"})

        r2 = node_tools.tool_run_cell(p, "Nope", "write_output('z', 1)",
                                      {"missing": "$recorded"})
        self.assertIn("error", r2)
        self.assertIn("'text'", r2["error"])
        self.assertIn("$recorded:<name>", r2["error"])

        r3 = node_tools.tool_run_cell(
            p, "Nope", "write_output('z', read_input('src') + '!')",
            {"src": "$recorded:text"})
        self.assertTrue(r3["ok"], r3)
        self.assertEqual(r3["output"], {"z": "hello!"})

    def test_run_ai_step_refuses_without_ready_provider(self):
        p = _workflow([], pid="p_cells6")
        self._skeleton(p, "Judge")
        for m in ("no-such-model", ""):
            r = node_tools.tool_run_ai_step(p, "Judge", "judge it", m,
                                            {}, ["verdict"])
            self.assertIn("error", r)
            self.assertIn("Admin", r["error"])

    def test_step_work_refused_until_the_skeleton_exists(self):
        p = _workflow([], pid="p_cells7")
        r = node_tools.tool_run_cell(p, "Extract", "write_output('y', 1)", {})
        self.assertIn("save_plan", r.get("error", ""))
        r2 = node_tools.tool_run_ai_step(p, "Judge", "judge", "", {}, ["v"])
        self.assertIn("save_plan", r2.get("error", ""))
        self._skeleton(p, "Extract")
        self.assertTrue(node_tools.tool_run_cell(
            p, "Extract", "write_output('y', 1)", {}).get("ok"))

    def test_tools_resolve_names(self):
        p = _workflow([{"id": "n_x", "name": "Read the PDF", "type": "code",
                       "config": {}, "inputs": [], "outputs": [], "tests": []}],
                     pid="p_names")
        self.assertEqual(node_tools.tool_read_node(p, "Read the PDF")["id"], "n_x")

    def test_failed_cell_records_but_never_fills(self):
        p = _workflow([], pid="p_cells2")
        self._skeleton(p, "Broken")
        r = node_tools.tool_run_cell(p, "Broken", "raise ValueError('no')", {})
        self.assertFalse(r["ok"])
        sp = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Broken", "type": "code", "code_sketch": "does x",
             "outputs": [{"name": "y", "type": "text"}]}]})
        self.assertTrue(sp["ok"], sp)
        self.assertNotIn("assembled_from_cells", sp)
        self.assertNotIn("code", p["plan"]["nodes"][0])

class ValidateBarFitsTypeTest(unittest.TestCase):
    def test_user_input_green_without_tests(self):
        p = _workflow([{"id": "n_u", "name": "Get PDF", "type": "user-input",
                       "config": {}, "inputs": [],
                       "outputs": [{"name": "pdf", "type": "file"}], "tests": []}],
                     pid="p_bar1")
        self.assertTrue(node_tools.validate_node(p, "n_u")["ok"])

    def test_ai_green_with_authored_example_no_replay(self):
        p = _workflow([{"id": "n_a", "name": "Judge", "type": "ai",
                       "config": {"prompt": "judge it",
                                  "model": {"model": "m1"}},
                       "inputs": [{"name": "text", "type": "text"}],
                       "outputs": [{"name": "verdict", "type": "text"}],
                       "tests": [{"name": "ex", "inputs": {"text": "x"},
                                  "expect": "ok"}]}],
                     pid="p_bar2")
        v = node_tools.validate_node(p, "n_a")
        self.assertTrue(v["ok"], v)
        p["nodes"][0]["tests"] = []
        self.assertFalse(node_tools.validate_node(p, "n_a")["ok"])

class FakeCheckBypassTest(unittest.TestCase):
    def test_helper_function_params_do_not_suppress(self):
        from agent import gate
        code = ("def helper(x):\n"
                "    return x\n"
                "write_output('matched', helper(1))\n")
        v = gate.check_fake(code, ["chosen"])
        self.assertTrue(any("never reads them" in x for x in v), v)

    def test_entry_function_with_declared_param_is_fine(self):
        from agent import gate
        code = "def main(chosen):\n    return {'matched': chosen == 'x'}\n"
        self.assertEqual(gate.check_fake(code, ["chosen"]), [])

    def test_container_literal_output_is_fake(self):
        from agent import gate
        code = ("v = read_input('chosen')\n"
                "write_output('matched', {'always': True, 'items': [1, 2]})\n")
        v = gate.check_fake(code, ["chosen"])
        self.assertTrue(any("hardcoded" in x for x in v), v)

    def test_computed_output_passes(self):
        from agent import gate
        code = ("v = read_input('chosen')\n"
                "write_output('matched', {'hit': v == 'x'})\n")
        self.assertEqual(gate.check_fake(code, ["chosen"]), [])

class RegressionGateShapeTest(unittest.TestCase):
    def test_zero_cases_pass_vacuously(self):
        from agent import receipts
        node = {"id": "n_zero", "type": "code",
                "config": {"code": "write_output('y', read_input('x'))"},
                "inputs": [{"name": "x", "type": "text"}],
                "outputs": [{"name": "y", "type": "text"}]}
        r = receipts.regression_check(node, extra_cases=[], workflow_id="p_zero")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["total"], 0)

    def test_ai_replay_unverifiable_without_provider_passes_with_note(self):
        from agent import receipts
        node = {"id": "n_ai", "type": "ai",
                "config": {"prompt": "judge", "model": {"model": "no-such-model"}},
                "outputs": [{"name": "verdict", "type": "text"}]}
        r = receipts.regression_check(
            node, extra_cases=[{"inputs": {"text": "x"}, "output": {"verdict": "y"}}],
            workflow_id="p_zero")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r.get("unverifiable"), 1)
        self.assertEqual(r["regressions"], [])

    def test_connector_refusal_keeps_shape(self):
        from agent import receipts
        r = receipts.regression_check({"id": "n_c", "type": "connector"},
                                        workflow_id="p_zero")
        for k in ("ok", "total", "regressions", "changed", "error"):
            self.assertIn(k, r)
        self.assertFalse(r["ok"])

class BatchAndReadOnlyTest(unittest.TestCase):
    def _three(self, pid):
        return _workflow([
            {"id": "a", "name": "a", "type": "code", "config": {"code": "x=1"},
             "inputs": [], "outputs": [{"name": "val", "type": "text"}], "tests": []},
            {"id": "b", "name": "b", "type": "code", "config": {"code": "x=1"},
             "inputs": [{"name": "val", "type": "text"}],
             "outputs": [{"name": "out", "type": "text"}], "tests": []},
            {"id": "c", "name": "c", "type": "code", "config": {"code": "x=1"},
             "inputs": [{"name": "out", "type": "text"}], "outputs": [], "tests": []}],
            pid=pid)

    def test_connect_by_name_stores_ids(self):
        p = self._three("p_ids1")
        p["nodes"][0]["name"] = "First step"
        p["nodes"][1]["name"] = "Second step"
        r = node_tools.connect_nodes(p, "First step", "Second step")
        self.assertTrue(r["ok"], r)
        self.assertEqual(p["edges"][0]["src"], "a")
        self.assertEqual(p["edges"][0]["dst"], "b")

        r2 = node_tools.connect_nodes(p, "a", "b")
        self.assertTrue(r2.get("ok"), r2)
        self.assertIn("already wired", r2["note"])
        self.assertEqual(len(p["edges"]), 1)

    def test_canonicalise_heals_name_stored_refs(self):
        p = self._three("p_ids2")
        p["nodes"][1]["name"] = "Second step"
        p["nodes"][2]["name"] = "Third step"
        p["edges"] = [{"src": "a", "dst": "b", "when": ""},
                      {"src": "Second step",
                       "dst": "Third step", "when": ""}]
        fixed = node_tools.canonicalise_refs(p)
        self.assertEqual(fixed, 2)
        self.assertEqual(p["edges"][1]["src"], "b")
        self.assertEqual(p["edges"][1]["dst"], "c")

        self.assertNotIn("source", p["nodes"][2]["inputs"][0])

    def test_connect_batches_edges(self):
        p = self._three("p_batch1")
        r = node_tools.connect_nodes(p, edges=[{"src": "a", "dst": "b"},
                                              {"src": "b", "dst": "c"}])
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(p["edges"]), 2)

        r2 = node_tools.connect_nodes(p, edges=[{"src": "a", "dst": "a"}])
        self.assertFalse(r2["ok"])
        self.assertIn("error", r2["edges"]["a -> a"])

    def test_validate_batches_nodes(self):
        p = self._three("p_batch2")
        v = node_tools.validate_node(p, node_ids=["a", "b"])
        self.assertIn("a", v["nodes"])
        self.assertIn("b", v["nodes"])
        self.assertFalse(v["ok"])

        v1 = node_tools.validate_node(p, "a")
        self.assertIn("checklist", v1)

    def test_read_only_connector_declared_at_create(self):
        p = _workflow([], pid="p_ro")
        node_tools._create_node(p, "lookup", "connector",
                                    external_impact="reads the public registry",
                                    read_only=True)
        node_tools._create_node(p, "send", "connector",
                                    external_impact="posts the record")
        from step_types import writes_outside
        lookup = next(n for n in p["nodes"] if n["name"] == "lookup")
        send = next(n for n in p["nodes"] if n["name"] == "send")
        self.assertTrue(lookup["read_only"])
        self.assertFalse(send["read_only"])
        self.assertFalse(writes_outside(lookup))
        self.assertTrue(writes_outside(send))

class AskCardShapeTest(unittest.TestCase):
    def test_object_options_become_labels(self):
        p = _workflow([], pid="p_opts")
        r = node_tools.tool_ask_user(p, "Which?", options=[
            {"label": "A PowerPoint deck", "description": "x"}, "plain", 7])
        self.assertEqual(r["options"], ["A PowerPoint deck", "plain", "7"])

    def test_blank_node_name_refused(self):
        p = _workflow([], pid="p_blank")
        self.assertIn("error", node_tools._create_node(p, "  ", "code"))
        self.assertEqual(p["nodes"], [])

    def test_share_file_stays_in_scratch(self):
        import config
        p = _workflow([], pid="p_share")
        work = config.workflow_dir("p_share") / "work"
        work.mkdir(parents=True, exist_ok=True)
        (work / "deck.txt").write_text("draft")
        r = node_tools.tool_share_file(p, "deck.txt", label="Draft deck")
        self.assertTrue(r["ok"], r)
        from agent import transcript
        last = transcript.items(p)[-1]
        self.assertEqual(last["kind"], "message")
        self.assertEqual(last["files"][0]["name"], "Draft deck")

        self.assertIn("error", node_tools.tool_share_file(p, "../workflow.db"))
        self.assertIn("error", node_tools.tool_share_file(p, "/etc/hosts"))

class MaskedSecretAskTest(unittest.TestCase):
    def test_secret_ask_sentinel_carries_flag_and_name(self):
        p = _workflow([], pid="p_ask_sec")
        r = node_tools.tool_ask_user(p, "What's the wifi password?",
                                     secret=True, secret_name="wifi_password")
        self.assertTrue(r["secret"])
        self.assertEqual(r["secret_name"], "wifi_password")

    def test_plain_ask_unchanged(self):
        p = _workflow([], pid="p_ask_plain")
        r = node_tools.tool_ask_user(p, "Which network?", options=["a", "b"])
        self.assertEqual(r["options"], ["a", "b"])
        self.assertNotIn("secret", r)

class ProvenBuildGateTest(unittest.TestCase):
    def _plan(self, p):
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        return node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Extract", "type": "code", "code_sketch": "reads the pdf",
             "outputs": [{"name": "y", "type": "text"}]}]})

    def test_refuses_unproven_then_unlocks_after_cell(self):
        p = _workflow([], pid="p_gate1")
        r0 = self._plan(p)
        _user_approved(p)

        self.assertNotIn("warnings", p["plan"])
        if isinstance(r0, dict):
            self.assertTrue(any("not yet tried" in n for n in r0.get("notes") or []), r0)
        r = node_tools.tool_build_workflow(p)
        self.assertIn("Extract", r.get("error", ""))
        self.assertNotIn("_pending_build", p)
        node_tools.tool_run_cell(p, "Extract", "write_output('y', 'ok')", {})
        r2 = node_tools.tool_build_workflow(p)
        self.assertTrue(r2.get("ok"), r2)

    def test_a_declined_approval_is_the_other_way_a_step_is_done(self):
        p = _workflow([], pid="p_gate_declined")
        self._plan(p)
        _user_approved(p)
        self.assertIn("Extract",
                      node_tools.tool_build_workflow(p).get("error", ""))
        card = node_tools._transcript.append_request(
            p, "approval", {"title": 'Open a browser window for "Extract"?',
                            "step": "Extract"})
        node_tools._transcript.append_answer(p, card["iid"], "No.",
                                             shown="deny")
        self.assertEqual(node_tools.declined_steps(p), {"Extract"})
        self.assertEqual(node_tools.untested_steps(p, p["plan"]["nodes"]), [])
        self.assertTrue(node_tools.tool_build_workflow(p).get("ok"))

    def test_a_live_send_is_a_card_yes_runs_no_settles(self):
        from unittest import mock
        from agent import actions
        p = _workflow([], pid="p_send_gate")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Post it", "type": "connector", "read_only": False,
             "external_impact": "posts a note", "code_sketch": "post",
             "outputs": [{"name": "posted", "type": "boolean"}]}]})
        _user_approved(p)

        r = node_tools.tool_run_cell(p, "Post it", "write_output('posted', True)", {})
        self.assertEqual(r.get("needs_send_ok"), "Post it")
        self.assertFalse(r.get("send_repeat"))

        with mock.patch.object(actions, "_approve", return_value="deny") as ap:
            env = actions.execute(p, {"name": "run_cell", "input": {
                "name": "Post it", "code": "write_output('posted', True)",
                "inputs": {}, "reason": "one real post to prove it"}}, lambda e: None)
        self.assertFalse(env["ok"])
        self.assertIn("said no", env["error"])
        self.assertEqual(ap.call_args.kwargs.get("step"), "Post it")

        card = node_tools._transcript.append_request(
            p, "approval", {"title": 'Send "Post it" for real?', "step": "Post it"})
        node_tools._transcript.append_answer(p, card["iid"], "No.", shown="deny")
        self.assertEqual(node_tools.untested_steps(p, p["plan"]["nodes"]), [])

        p2 = _workflow([], pid="p_send_gate2")
        node_tools.tool_save_intent(p2, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p2, {"summary": "Do.", "nodes": [
            {"name": "Post it", "type": "connector", "read_only": False,
             "external_impact": "posts a note", "code_sketch": "post",
             "outputs": [{"name": "posted", "type": "boolean"}]}]})
        _user_approved(p2)
        with mock.patch.object(actions, "_approve", return_value="allow"):
            env = actions.execute(p2, {"name": "run_cell", "input": {
                "name": "Post it", "code": "write_output('posted', True)",
                "inputs": {}, "reason": "one real post"}}, lambda e: None)
        self.assertTrue(env["ok"], env)

        r3 = node_tools.tool_run_cell(p2, "Post it", "write_output('posted', True)", {})
        self.assertTrue(r3.get("ok"), r3)
        self.assertNotIn("needs_send_ok", r3)

    def test_an_allowed_approval_still_requires_the_step_to_be_tried(self):
        p = _workflow([], pid="p_gate_allowed")
        self._plan(p)
        _user_approved(p)
        card = node_tools._transcript.append_request(
            p, "approval", {"title": "Open a browser?", "step": "Extract"})
        node_tools._transcript.append_answer(p, card["iid"], "Yes - go ahead.",
                                             shown="allow")
        self.assertEqual(node_tools.declined_steps(p), set())
        self.assertIn("Extract",
                      node_tools.tool_build_workflow(p).get("error", ""))

    def test_a_denial_naming_another_step_clears_nothing(self):
        p = _workflow([], pid="p_gate_other")
        self._plan(p)
        _user_approved(p)
        card = node_tools._transcript.append_request(
            p, "approval", {"title": "Open a browser?", "step": "Something else"})
        node_tools._transcript.append_answer(p, card["iid"], "No.", shown="deny")
        self.assertIn("Extract",
                      node_tools.tool_build_workflow(p).get("error", ""))

    def test_force_is_dead_on_the_model_surface(self):
        p = _workflow([], pid="p_gate2")
        self._plan(p)
        _user_approved(p)
        with self.assertRaises(TypeError):
            node_tools.tool_build_workflow(p, force=True)
        r = node_tools.tool_build_workflow(p)
        self.assertIn("Extract", r.get("error", ""))
        self.assertNotIn("_pending_build", p)
        spec = next(s for s in node_tools._AGENT_SPECS
                    if s[0] == "build_workflow")
        self.assertNotIn("force", spec[3])

    def test_the_opening_card_is_shown_once_and_approves_every_later_save(self):
        p = _workflow([], pid="p_gate4")
        p.pop("plan_approved_ts", None)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        plan = {"summary": "Do.", "nodes": [
            {"name": "Extract", "type": "code", "code": "write_output('y', 1)",
             "outputs": [{"name": "y", "type": "text"}]}]}
        node_tools.tool_save_plan(p, dict(plan))
        self.assertTrue(turnstate.of(p).opening_card_show_now)
        turnstate.of(p).take("opening_card_show_now")

        r = node_tools.tool_run_cell(p, "Extract", "write_output('y', 1)", {})
        self.assertIn("hasn't approved the plan", r.get("error", ""))
        p["plan_approved_ts"] = 1.0
        node_tools.tool_run_cell(p, "Extract", "write_output('y', 1)", {})
        node_tools.save(p)
        p.pop("plan_approved_ts", None)

        r = node_tools.tool_build_workflow(p)
        self.assertIn("hasn't agreed", r.get("error", ""))
        self.assertNotIn("_pending_build", p)
        _user_approved(p)

        node_tools.tool_save_plan(p, dict(plan))
        self.assertFalse(turnstate.of(p).opening_card_show_now)
        self.assertTrue(node_tools.tool_build_workflow(p).get("ok"))
        grown = dict(plan, summary="Do it, and also file the result.")
        grown["nodes"] = plan["nodes"] + [
            {"name": "File it", "type": "code", "code": "write_output('z', read_input('y'))",
             "inputs": [{"name": "y", "type": "text"}],
             "outputs": [{"name": "z", "type": "text"}]}]

        grown["edges"] = [{"src": "Extract", "dst": "File it"}]
        node_tools.tool_save_plan(p, grown)
        node_tools.tool_run_cell(p, "File it", "write_output('z', 1)", {})
        self.assertTrue(node_tools.tool_build_workflow(p).get("ok"))

        self.assertEqual(len(_bootstrap.shown_requests(p, "blueprint")), 1)

    def test_design_gaps_mark_the_plan_wip_and_block_build(self):
        from unittest.mock import patch
        p = _workflow([], pid="p_gate3")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        plan = {"summary": "Do.", "nodes": [
            {"name": "Extract", "type": "code", "code": "write_output('y', 1)",
             "outputs": [{"name": "y", "type": "text"}]}]}
        with patch.object(node_tools.plan_logic, "check",
                          return_value={"ok": False, "findings": ["port mismatch"]}):
            r = node_tools.tool_save_plan(p, dict(plan))
        self.assertEqual(p["plan"]["design_gaps"], ["port mismatch"])
        b = node_tools.tool_build_workflow(p)
        self.assertIn("design gaps", b.get("error", ""))
        self.assertNotIn("_pending_build", p)
        _user_approved(p)
        node_tools.tool_save_plan(p, dict(plan))
        self.assertNotIn("design_gaps", p["plan"])
        self.assertTrue(node_tools.tool_build_workflow(p).get("ok"))

class PlanApprovalGateTest(unittest.TestCase):
    def _proven(self, pid):
        p = _workflow([], pid=pid)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Extract", "type": "code",
             "code": "write_output('y', 1)",
             "outputs": [{"name": "y", "type": "text"}]}]})
        assert r.get("ok"), r
        node_tools.tool_run_cell(p, "Extract", "write_output('y', 1)", {})
        p.pop("plan_approved_ts", None)
        node_tools.save(p)
        return p

    def test_refuses_until_the_opening_card_is_answered(self):
        p = self._proven("p_apr1")
        self.assertFalse(node_tools.plan_approved(p))
        r = node_tools.tool_build_workflow(p)
        self.assertIn("hasn't agreed", r.get("error", ""))
        self.assertNotIn("_pending_build", p)
        _user_approved(p)
        self.assertTrue(node_tools.plan_approved(p))
        self.assertTrue(node_tools.tool_build_workflow(p).get("ok"))

    def test_the_deadlock_cannot_recur(self):
        p = self._proven("p_apr_deadlock")
        _user_approved(p)
        for said in ("Build", "do it", "build it"):
            node_tools.append_plan_entry(p, p["plan"])
            _bootstrap.said(p, "user", said)
            node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
                {"name": "Extract", "type": "code",
                 "code": "write_output('y', 1)",
                 "outputs": [{"name": "y", "type": "text"}]}]})
            self.assertTrue(node_tools.tool_build_workflow(p).get("ok"), said)

        self.assertEqual(len(_bootstrap.shown_requests(p, "blueprint")), 1)

    def test_approval_survives_every_later_save(self):
        p = self._proven("p_apr5")
        _user_approved(p)
        node_tools.tool_save_plan(p, {"summary": "Do it rather differently.",
                                      "nodes": [
            {"name": "Extract", "type": "code", "code": "write_output('y', 1)",
             "outputs": [{"name": "y", "type": "text"}]}]})
        self.assertTrue(node_tools.plan_approved(p))
        self.assertTrue(node_tools.tool_build_workflow(p).get("ok"))

    def test_nothing_is_explored_before_the_user_has_seen_the_plan(self):
        p = _workflow([], pid="p_apr_order")
        p.pop("plan_approved_ts", None)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_run_cell(p, "Probe it", "write_output('x', 1)", {})
        self.assertFalse(r.get("ok"))
        self.assertIn("must see the planned workflow", r.get("error", ""))

        gapped = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Extract", "type": "code", "code_sketch": "reads it",
             "inputs": [{"name": "raw", "type": "text"}], "outputs": []}]})
        self.assertTrue(gapped.get("design_gaps"))
        self.assertTrue(turnstate.of(p).opening_card_show_now)

    def test_approval_is_never_model_writable(self):
        p = _workflow([], pid="p_apr6")
        p.pop("plan_approved_ts", None)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {
            "summary": "Do.", "approved_ts": 1.0, "approved_summary": "Do.",
            "nodes": [{"name": "Extract", "type": "code",
                       "code": "write_output('y', 1)",
                       "outputs": [{"name": "y", "type": "text"}]}]})
        self.assertNotIn("approved_summary", p["plan"])
        self.assertNotIn("approved_ts", p["plan"])
        self.assertFalse(node_tools.plan_approved(p))

class FeedbackBatchTest(unittest.TestCase):
    def _step(self, name="Make it", **kw):
        return {"name": name, "type": "code",
                "code": "write_output('y', 1)",
                "outputs": [{"name": "y", "type": "text"}], **kw}

    def test_instructions_gap_only_on_build_ready_saves(self):
        p = _workflow([], pid="p_fb1")
        node_tools.tool_save_intent(p, "Purpose.")

        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Make it", "type": "code", "code_sketch": "x",
             "outputs": [{"name": "y", "type": "text"}]}]})
        self.assertTrue(r.get("ok"), r)
        self.assertNotIn("design_gaps", p["plan"])

        r2 = node_tools.tool_save_plan(p, {"summary": "Do.",
                                           "nodes": [self._step()]})
        self.assertTrue(any("instructions" in g
                            for g in r2.get("design_gaps") or []), r2)

        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r3 = node_tools.tool_save_plan(p, {"summary": "Do.",
                                           "nodes": [self._step()]})
        self.assertTrue(r3.get("ok"), r3)
        self.assertNotIn("design_gaps", p["plan"])

    def test_connector_without_domains_is_a_model_note_never_a_warning(self):
        p = _workflow([], pid="p_fb2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Send it", "type": "connector", "code_sketch": "posts",
             "external_impact": "sends", "read_only": False,
             "outputs": [{"name": "sent", "type": "boolean"}]}]})
        self.assertTrue(any("domains" in n for n in r.get("notes") or []),
                        r.get("notes"))
        self.assertFalse(any("addresses" in w
                             for w in p["plan"].get("warnings") or []))

        p2 = _workflow([], pid="p_fb2b")
        node_tools.tool_save_intent(p2, "Purpose.", instructions="Run it.")
        r2 = node_tools.tool_save_plan(p2, {"summary": "Do.", "nodes": [
            {"name": "Read the page", "type": "browser",
             "code_sketch": "reads", "read_only": True,
             "external_impact": "opens the page in the browser",
             "outputs": [{"name": "html", "type": "longtext"}]}]})
        self.assertFalse(any("domains" in n for n in r2.get("notes") or []))

    def test_bare_ai_output_descriptions_are_a_model_note(self):
        p = _workflow([], pid="p_fb2c")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Judge", "type": "ai", "prompt": "judge",
             "outputs": [{"name": "verdict", "type": "text"}]}]})
        self.assertTrue(any("no description" in n for n in r.get("notes") or []),
                        r.get("notes"))
        self.assertFalse(any("no description" in w
                             for w in p["plan"].get("warnings") or []))

    def test_a_partial_save_merges_into_the_saved_plan_by_default(self):
        p = _workflow([], pid="p_fb3")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        plan = {"summary": "Do.", "nodes": [self._step("One"),
                                            self._step("Two"),
                                            self._step("Three")]}
        node_tools.tool_save_plan(p, copy.deepcopy(plan))
        r2 = node_tools.tool_save_plan(p, {"summary": "Do.",
                                           "nodes": [self._step("Two")]})
        self.assertTrue(r2.get("ok"), r2)
        self.assertEqual([n["name"] for n in p["plan"]["nodes"]],
                         ["One", "Two", "Three"])
        r3 = node_tools.tool_save_plan(p, {"summary": "Do.", "replace": True,
                                           "nodes": [self._step("Two")]})
        self.assertFalse(r3.get("ok"), r3)
        self.assertIn("vanished", str(r3.get("errors")))

    def test_a_loaded_guide_rides_the_working_trail_into_the_next_turn(self):
        from agent import actions as _actions, context as _ctx, trail
        p = _workflow([], pid="p_fb5")
        trail.begin(p["id"], "t_fb5a", "wire the sheet", "chat")
        env = _actions.execute(p, {"name": "load_skill",
                                   "input": {"id": "google-sheets"}},
                               lambda ev: None)
        self.assertTrue(env["ok"], env)
        trail.end(p["id"])
        body = _ctx.build(p, "hello")[0]["content"]
        self.assertIn("[working memory]", body)
        self.assertIn("you called load_skill", body)

        self.assertIn("Three proven routes. Present the viable ones", body)

    def test_describe_save_plan_is_stage_honest(self):
        from agent import orchestrator
        p = _workflow([], pid="p_fb6")
        line = lambda: orchestrator._describe_tool(
            p, "mcp__cryogram__save_plan", {})
        self.assertEqual(line(), "writing up the plan")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Make it", "type": "code", "code_sketch": "x",
             "outputs": [{"name": "y", "type": "number"}]}]})
        self.assertEqual(line(), "updating the plan")
        node_tools.tool_run_cell(p, "Make it", "write_output('y', 1)", {})
        self.assertEqual(line(), "writing down the final blueprint")

class BuildBarFixesTest(unittest.TestCase):
    def test_fill_attaches_test_to_a_step_that_already_has_code(self):
        from agent import cells
        p = _workflow([], pid="p_bar1")
        cells.record("p_bar1", "Scan", "write_output('networks', ['a'])",
                     {}, ["a"], True, 0.1, [])
        nodes = [{"name": "Scan", "type": "code",
                  "code": "write_output('networks', scan())",
                  "outputs": [{"name": "networks", "type": "list"}]}]
        filled = node_tools.fill_from_cells("p_bar1", nodes)
        self.assertIn("Scan", filled)
        self.assertEqual(nodes[0]["tests"][0]["name"], "recorded run")
        self.assertIn("scan()", nodes[0]["code"])

    def test_ai_step_adopts_the_recorded_trys_model(self):
        from agent import cells
        cells.record("p_aimodel", "Summarise it", "the prompt", {"t": "x"},
                     {"message": "ok"}, True, 0.2, [], kind="ai",
                     model="cheap-model-1")
        nodes = [{"name": "Summarise it", "type": "ai", "prompt": "the prompt",
                  "outputs": [{"name": "message", "type": "text"}]}]
        filled = node_tools.fill_from_cells("p_aimodel", nodes)
        self.assertIn("Summarise it", filled)
        self.assertEqual(nodes[0]["model"], "cheap-model-1")
        self.assertEqual(nodes[0]["tests"][0]["name"], "recorded example")

        nodes2 = [{"name": "Summarise it", "type": "ai", "prompt": "p",
                   "model": "", "outputs": [{"name": "message", "type": "text"}]}]
        node_tools.fill_from_cells("p_aimodel", nodes2)
        self.assertEqual(nodes2[0]["model"], "cheap-model-1")

    def test_read_cases_outcome_never_errors_on_a_node_type(self):
        p = _workflow([{"id": "n_rc", "name": "step", "type": "code",
                       "config": {"code": "x=1"}, "inputs": [], "outputs": [],
                       "tests": []}], pid="p_rcoerce")
        for stray in ("code", "ai", "connector", "nonsense"):
            r = node_tools.tool_read_cases(p, "n_rc", outcome=stray)
            self.assertNotIn("error", r, stray)
        self.assertNotIn("error", node_tools.tool_read_cases(p, "n_rc", "success"))

    def test_zero_input_source_step_can_validate_green(self):
        p = _workflow([{"id": "n_src", "name": "Scan", "type": "code",
                       "config": {"code": "import os\nwrite_output('networks', os.listdir('.'))"},
                       "inputs": [], "outputs": [{"name": "networks", "type": "list"}],
                       "tests": [{"name": "t", "inputs": {}, "expect": "ok",
                                  "asserts": []}]}])
        v = node_tools.validate_node(p, "n_src")
        self.assertTrue(v["ok"], v)
        self.assertFalse(v["checklist"]["declared_inputs"])

    def test_secret_port_bypass_rejected_at_set_code(self):
        p = _workflow([{"id": "n_c", "name": "Connect", "type": "connector",
                       "config": {}, "external_impact": "joins wifi",
                       "inputs": [{"name": "ssid", "type": "text"},
                                  {"name": "password", "type": "secret"}],
                       "outputs": [{"name": "ok", "type": "boolean"}], "tests": []}])
        bad = ("ssid = read_input('ssid')\n"
               "pw = get_secret('wifi_password')\n"
               "write_output('ok', bool(ssid and pw))")
        r = node_tools._set_code(p, "n_c", bad)
        self.assertFalse(r.get("ok"))
        self.assertIn("password", str(r.get("violations")))

        leaky = ("ssid = read_input('ssid')\n"
                 "pw = read_input('password')\n"
                 "write_output('ok', bool(ssid and pw))")
        r2 = node_tools._set_code(p, "n_c", leaky)
        self.assertFalse(r2.get("ok"))
        self.assertIn("NAME", str(r2.get("violations")))
        good = ("ssid = read_input('ssid')\n"
                "pw = get_secret(read_input('password'))\n"
                "write_output('ok', bool(ssid and pw))")
        self.assertTrue(node_tools._set_code(p, "n_c", good).get("ok"))

class SettingsBecomeVariablesTest(unittest.TestCase):
    def _plan(self, extra_inputs, **step):
        return {"summary": "Do the thing.", "nodes": [
            {"name": "Fetch posts", "type": "code", "code_sketch": "fetches",
             "inputs": extra_inputs,
             "outputs": [{"name": "posts", "type": "list"}], **step}]}

    def test_a_steps_try_gets_only_its_declared_inputs(self):
        p = _workflow([], pid="p_declared_only")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_declare_variables(p, [{"name": "sheet_url", "value": "https://example.test/sheet"}])
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Read sheet", "type": "code", "code_sketch": "reads",
             "outputs": [{"name": "url", "type": "text"}]}]})
        code = "write_output('url', read_input('sheet_url'))"
        step = node_tools.tool_run_cell(p, "Read sheet", code)
        self.assertFalse(step.get("ok"), step)
        self.assertIn("sheet_url", step.get("error", ""))
        probe = node_tools.tool_run_cell(p, "peek at the sheet setting", code)
        self.assertTrue(probe.get("ok"), probe)

    def test_the_agent_can_declare_variables(self):
        from agent import actions
        self.assertIn("declare_variables",
                      [t["name"] for t in actions.schemas()])

    def test_unwired_input_becomes_a_settings_variable(self):
        p = _workflow([], pid="p_set1")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, self._plan(
            [{"name": "reddit_feed_url", "type": "text"},
             {"name": "posts_per_run", "type": "number"}]))
        self.assertTrue(r.get("ok"), r)
        self.assertFalse(r.get("design_gaps"))
        names = [v["name"] for v in p.get("variables") or []]
        self.assertIn("reddit_feed_url", names)
        self.assertIn("posts_per_run", names)
        v = next(v for v in p["variables"] if v["name"] == "reddit_feed_url")
        self.assertEqual(v["value"], "")
        self.assertTrue(v["persistent"])
        self.assertFalse(v["secret"])

        warn = " ".join(p["plan"].get("warnings") or [])
        self.assertIn("become settings", warn)
        self.assertIn("Reddit feed url", warn)

    def test_the_build_is_not_refused(self):
        p = _workflow([], pid="p_set2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, self._plan(
            [{"name": "sheet_link", "type": "text"}]))
        self.assertIsNone((p.get("plan") or {}).get("design_gaps"))

    def test_a_secret_port_never_becomes_a_settings_variable(self):
        p = _workflow([], pid="p_set3")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, self._plan(
            [{"name": "api_key", "type": "secret"}]))
        self.assertNotIn("api_key",
                         [v["name"] for v in p.get("variables") or []])

    def test_a_value_the_user_gave_is_stored_and_kept(self):
        p = _workflow([], pid="p_set4")
        r = node_tools.tool_declare_variables(
            p, [{"name": "sheet_link", "value": "https://sheets/abc"}])
        self.assertTrue(r.get("ok"), r)
        v = next(v for v in p["variables"] if v["name"] == "sheet_link")
        self.assertEqual(v["value"], "https://sheets/abc")

        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, self._plan(
            [{"name": "sheet_link", "type": "text"}]))
        v = next(v for v in p["variables"] if v["name"] == "sheet_link")
        self.assertEqual(v["value"], "https://sheets/abc")

class ToolSurfaceTruthTest(unittest.TestCase):
    def test_image_sample_returns_visible_image_content(self):
        from storage import blobstore
        png = (b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        ref = blobstore.put(png, mime="image/png", meta={"name": "shot.png"}, owner=blobstore.OWNER_APP)
        p = _workflow([], pid="p_img1")
        p["samples"] = [{"name": "shot.png", "ref": ref, "mime": "image/png",
                         "size": len(png)}]
        from storage import store
        store.save(p)
        r = node_tools.tool_read_sample(p, "shot.png")
        self.assertIn("image", r)
        self.assertEqual(r["image"]["media_type"], "image/png")
        import base64
        self.assertEqual(base64.b64decode(r["image"]["data"]), png)
        self.assertNotIn("path", r)

    def test_binary_sample_names_the_cell_route(self):
        from storage import blobstore
        ref = blobstore.put(b"PK\x03\x04 fake zip", mime="application/zip",
                            meta={"name": "bundle.zip"}, owner=blobstore.OWNER_APP)
        p = _workflow([], pid="p_img2")
        p["samples"] = [{"name": "bundle.zip", "ref": ref,
                         "mime": "application/zip", "size": 13}]
        from storage import store
        store.save(p)
        r = node_tools.tool_read_sample(p, "bundle.zip")
        self.assertIn("cell", r.get("note", ""))
        self.assertNotIn("file tools", r.get("note", ""))

    def _pdf_sample(self, pid, data):
        from storage import blobstore, store
        ref = blobstore.put(data, mime="application/pdf", meta={"name": "doc.pdf"},
                            owner=blobstore.OWNER_APP)
        p = _workflow([], pid=pid)
        p["samples"] = [{"name": "doc.pdf", "ref": ref, "mime": "application/pdf", "size": len(data)}]
        store.save(p)
        return p

    def test_a_pdf_sample_is_read_as_its_text(self):
        p = self._pdf_sample("p_pdf_text", _pdf_with_text("Invoice total 42"))
        r = node_tools.tool_read_sample(p, "doc.pdf")
        self.assertEqual(r.get("pages"), 1)
        self.assertIn("[page 1]", r["text"])
        self.assertIn("Invoice total 42", r["text"])
        self.assertNotIn("note", r)

    def test_a_pdf_with_no_text_says_it_is_a_scan(self):
        p = self._pdf_sample("p_pdf_scan", _pdf_with_text(None))
        r = node_tools.tool_read_sample(p, "doc.pdf")
        self.assertNotIn("text", r)
        self.assertIn("no text that can be extracted", r["note"])
        self.assertIn("without OCR", r["note"])
        self.assertIn("model that reads PDFs", r["note"])

    def test_a_broken_pdf_is_said_not_raised(self):
        p = self._pdf_sample("p_pdf_bad", b"%PDF-1.4 not really")
        r = node_tools.tool_read_sample(p, "doc.pdf")
        self.assertIn("could not be read as text", r["note"])

    def test_share_file_accepts_a_blob_ref(self):
        from storage import blobstore
        ref = blobstore.put(b"report bytes", mime="text/plain",
                            meta={"name": "report.txt"}, owner=blobstore.OWNER_APP)
        p = _workflow([], pid="p_shr1")
        r = node_tools.tool_share_file(p, ref, label="The report")
        self.assertTrue(r.get("ok"), r)
        from agent import transcript
        last = transcript.items(p)[-1]
        self.assertEqual(last["kind"], "message")
        self.assertEqual(last["files"][0]["name"], "The report")

    def test_humanise_handles_camel_case(self):
        self.assertEqual(node_tools.humanise_name("fetchRate"), "Fetch rate")
        self.assertEqual(node_tools.humanise_name("fetchEURRate"),
                         "Fetch eur rate")
        self.assertEqual(node_tools.humanise_name("Fetch the rate"),
                         "Fetch the rate")

class ProofMarksTest(unittest.TestCase):
    def test_marks_follow_evidence(self):
        from agent import cells
        p = _workflow([], pid="p_pm1")
        cells.record("p_pm1", "Fetch", "write_output('y', 1)", {}, {"y": 1},
                     True, 0.1, [])
        cells.record("p_pm1", "Judge", "prompt", {}, {"v": "x"}, True, 0.1,
                     [], kind="ai", model="m")
        p["plan"] = {"status": "draft", "nodes": [
            {"name": "Fetch", "type": "code", "code": "write_output('y', 1)"},
            {"name": "Judge", "type": "ai"},
            {"name": "Ask", "type": "user-input"},
            {"name": "Send", "type": "connector"},
            {"name": "Stale", "type": "code", "code": "other()"}]}

        self.assertEqual(node_tools.step_states(p),
                         {"Fetch": "coded", "Judge": "coded", "Ask": "coded",
                          "Send": "validated", "Stale": "coded"})

class SetupCardFieldsTest(unittest.TestCase):
    def test_fields_normalise(self):
        clean, err = node_tools.clean_ask_fields([
            {"name": "Sheet URL", "type": "text"},
            {"name": "mode", "label": "How to read Reddit",
             "options": ["Log in once", "Skip comments"]},
            {"label": "Posts per run", "optional": True}])
        self.assertEqual(err, "")
        self.assertEqual(clean[0]["name"], "sheet_url")
        self.assertEqual(clean[0]["label"], "Sheet URL")
        self.assertEqual(clean[1]["type"], "choice")
        self.assertEqual(clean[2]["name"], "posts_per_run")
        self.assertTrue(clean[2]["optional"])

    def test_field_validation(self):
        cases = [
            ([{"name": "pick", "type": "choice"}], "needs options"),
            ([{"name": "a"}, {"name": "a"}], "share the name"),
            ([{}], "needs a name"),
            ("not-a-list", "list"),
        ]
        for fields, frag in cases:
            _, err = node_tools.clean_ask_fields(fields)
            self.assertIn(frag, err, fields)
        _, err = node_tools.clean_ask_fields([{"name": "x"}], secret=True)
        self.assertIn("own card", err)

    def test_ask_user_body_carries_clean_fields(self):
        p = _workflow([], pid="p_fields1")
        r = node_tools.tool_ask_user(p, "Let's set this up.",
                                     fields=[{"name": "Sheet URL"}])
        self.assertEqual(r["fields"][0]["name"], "sheet_url")
        r2 = node_tools.tool_ask_user(p, "Bad.", secret=True,
                                      secret_name="k",
                                      fields=[{"name": "x"}])
        self.assertIn("error", r2)

class CellAndPlanEdgeCasesTest(unittest.TestCase):
    def test_egress_domain_matches_both_error_shapes(self):
        urlopen = ("<urlopen error egress blocked: ('sheets.googleapis.com', 443) is not on the allowlist>")
        urllib3 = ("HTTPSConnectionPool(host='oauth2.googleapis.com', port=443): Max retries exceeded with url: /token (Caused by NewConnectionError(\"HTTPSConnection(host='oauth2.googleapis.com', port=443): Failed to establish a new connection: egress blocked: 'oauth2.googleapis.com' is not on the allowlist\"")
        self.assertEqual(node_tools.egress_block_domain(urlopen),
                         "sheets.googleapis.com")
        self.assertEqual(node_tools.egress_block_domain(urllib3),
                         "oauth2.googleapis.com")
        self.assertIsNone(node_tools.egress_block_domain("plain error"))

    def test_workflow_tools_refused_as_cell_code(self):
        p = _workflow([], pid="p_s7a")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Step", "type": "code", "code_sketch": "does",
             "outputs": [{"name": "y", "type": "text"}]}]})
        r = node_tools.tool_run_cell(
            p, "Step", "res = declare_variables({'post_cap': 20})\n"
                       "write_output('y', res)")
        self.assertIn("workflow TOOL", r.get("error", ""))

        r2 = node_tools.tool_run_cell(
            p, "Step", "note = 'about save_plan'\nwrite_output('y', note)")
        self.assertTrue(r2.get("ok"), r2)

    def test_bind_variable_accepts_project(self):
        p = _workflow([], pid="p_s7b")
        p["environment_ids"] = ["env_x"]
        from unittest import mock
        from storage import environments as _envs
        with mock.patch.object(_envs, "attached",
                               lambda proj: [{"id": "env_x", "name": "Gd"}]):
            r = node_tools.tool_bind_variable(p, "feed_url", "workflow")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(p["env_bindings"]["feed_url"], "workflow")

    def _full_plan(self, p):
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {"summary": "Do the thing.", "nodes": [
            {"name": "Fetch", "type": "code", "code": "write_output('a', 1)",
             "outputs": [{"name": "a", "type": "number"}]},
            {"name": "Judge", "type": "ai", "prompt": "judge",
             "inputs": [{"name": "a", "type": "number"}],
             "outputs": [{"name": "v", "type": "text"}]},
            {"name": "Save", "type": "connector", "read_only": True,
             "external_impact": "reads a sheet",
             "code": "write_output('done', bool(read_input('v')))",
             "inputs": [{"name": "v", "type": "text"}],
             "outputs": [{"name": "done", "type": "boolean"}]}],
            "edges": [{"src": "Fetch", "dst": "Judge"},
                      {"src": "Judge", "dst": "Save"}]})
        assert r.get("ok"), r

    def test_amend_carries_everything_forward(self):
        p = _workflow([], pid="p_s7c")
        self._full_plan(p)

        r = node_tools.tool_save_plan(p, {"amend": True, "nodes": [
            {"name": "Fetch", "type": "code",
             "code": "write_output('a', 2)",
             "outputs": [{"name": "a", "type": "number"}]}]})
        self.assertTrue(r.get("ok"), r)
        plan = p["plan"]
        self.assertEqual([n["name"] for n in plan["nodes"]],
                         ["Fetch", "Judge", "Save"])
        self.assertEqual(len(plan["edges"]), 2)
        self.assertEqual(plan["summary"], "Do the thing.")
        self.assertIn("write_output('a', 2)",
                      plan["nodes"][0]["code"])

    def test_amend_fills_a_steps_omitted_fields(self):
        p = _workflow([], pid="p_s7f")
        self._full_plan(p)
        r = node_tools.tool_save_plan(p, {"amend": True, "nodes": [
            {"name": "Judge", "type": "ai", "prompt": "judge harder"}]})
        self.assertTrue(r.get("ok"), r)
        judge = next(n for n in p["plan"]["nodes"] if n["name"] == "Judge")
        self.assertEqual(judge["prompt"], "judge harder")
        self.assertEqual([o["name"] for o in judge["outputs"]], ["v"])
        self.assertEqual([i["name"] for i in judge["inputs"]], ["a"])

        r = node_tools.tool_save_plan(p, {"amend": True, "nodes": [
            {"name": "Judge", "type": "ai",
             "outputs": [{"name": "verdict2", "type": "text"}]}]})
        self.assertTrue(r.get("ok"), r)
        judge = next(n for n in p["plan"]["nodes"] if n["name"] == "Judge")
        self.assertEqual([o["name"] for o in judge["outputs"]], ["verdict2"])

    def test_amend_removed_still_drops(self):
        p = _workflow([], pid="p_s7d")
        self._full_plan(p)
        r = node_tools.tool_save_plan(p, {
            "amend": True, "nodes": [],
            "changes": {"removed": ["Save"]},
            "edges": [{"src": "Fetch", "dst": "Judge"}]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual([n["name"] for n in p["plan"]["nodes"]],
                         ["Fetch", "Judge"])

    def test_amend_appends_new_steps(self):
        p = _workflow([], pid="p_s7e")
        self._full_plan(p)
        r = node_tools.tool_save_plan(p, {"amend": True, "nodes": [
            {"name": "Notify", "type": "code", "code_sketch": "notifies",
             "inputs": [{"name": "done", "type": "boolean"}],
             "outputs": [{"name": "sent", "type": "boolean"}]}],
            "edges": [{"src": "Fetch", "dst": "Judge"},
                      {"src": "Judge", "dst": "Save"},
                      {"src": "Save", "dst": "Notify"}]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual([n["name"] for n in p["plan"]["nodes"]],
                         ["Fetch", "Judge", "Save", "Notify"])

    def _browser_proj(self, pid, browser_flag=False):
        from agent import cells
        p = _workflow([], pid=pid)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        step = {"name": "Fetch posts", "type": "browser",
                "read_only": True, "external_impact": "reads a site",
                "url": "https://example.test/posts",
                "code_sketch": "fetches",
                "outputs": [{"name": "posts", "type": "list"}]}
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [step]})
        cells.record(pid, "Fetch posts", "old code", {},
                     {"posts": [{"t": "a"}]}, True, 1.0, [])
        return p

    def test_a_browser_step_carries_the_address_it_opens(self):
        p = _workflow([], pid="p_url_sync")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Read the inbox", "type": "browser", "read_only": True,
             "external_impact": "reads a site", "code_sketch": "reads",
             "url": "https://mail.example.test/inbox",
             "outputs": [{"name": "rows", "type": "list"}]}]})
        step = (p.get("plan") or {})["nodes"][0]
        self.assertEqual(step["url"], "https://mail.example.test/inbox")

        p2 = _workflow([], pid="p_url_code")
        node_tools.tool_save_intent(p2, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p2, {"summary": "Do.", "nodes": [
            {"name": "Tidy the rows", "type": "code", "code_sketch": "tidies",
             "url": "https://nope.test",
             "outputs": [{"name": "rows", "type": "list"}]}]})
        import step_types
        self.assertFalse(step_types.may("code", "browser"))

    def test_the_cell_says_what_the_window_brought_back(self):
        from unittest import mock
        from runtime import sandbox
        from storage import blobstore
        p = self._browser_proj("p_s8cap")
        ref = blobstore.put(b"{}", "application/json", {},
                            owner=blobstore.workflow_owner("p_s8cap"))
        out = {"posts": [1], "$capture": {"har": ref, "snapshot": ref}}
        with mock.patch.object(sandbox, "run", return_value=out):
            r = node_tools.tool_run_cell(
                p, "Fetch posts", "write_output('posts', [1])",
                reason="first look", browser_ok=True, fresh=True)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("captured"), ["har", "page"])

        self.assertEqual(r.get("window"), 1)
        self.assertIn("window 1 (first look)", r["captured_note"])
        self.assertIn("$recorded:<name>#1", r["captured_note"])
        from agent import cells
        wins = cells.windows("p_s8cap")
        self.assertEqual([(w["n"], w["reason"]) for w in wins], [(1, "first look")])
        self.assertEqual(cells.latest_outputs("p_s8cap").get("har#1"), ref)
        entry = next(e for e in cells.inventory("p_s8cap") if e.get("windows"))
        self.assertEqual(entry["windows"][0]["reason"], "first look")

    def test_a_repeat_window_asks_like_the_first(self):
        p = self._browser_proj("p_s8a")
        p["plan"]["nodes"][0]["url"] = "https://www.example.test/feed"
        r = node_tools.tool_run_cell(
            p, "Fetch posts",
            "from playwright.sync_api import sync_playwright\n"
            "write_output('posts', [])",
            reason="checking whether scrolling loads more")
        self.assertEqual(r.get("needs_browser_ok"), "Fetch posts")
        self.assertEqual(r.get("domain"), "example.test")
        self.assertNotIn("sends", r)
        self.assertNotIn("missing", r)

    def test_browser_relaunch_without_reason_asks_for_one(self):
        p = self._browser_proj("p_s8a2")
        r = node_tools.tool_run_cell(
            p, "Fetch posts",
            "from playwright.sync_api import sync_playwright\n"
            "write_output('raw_ref', write_file('page.html', 'x'))\n"
            "write_output('posts', [])",
            missing="the recording lacks the comment bodies")
        self.assertIsNone(r.get("needs_browser_ok"))
        self.assertIn("reason", r["error"])

    def test_a_declared_browser_step_gates_plain_code_too(self):
        p = self._browser_proj("p_s8b")
        r = node_tools.tool_run_cell(
            p, "Fetch posts",
            "write_output('raw_ref', write_file('p.html', 'x'))\n"
            "write_output('posts', [1])",
            reason="need one more live read",
            missing="the recording predates the new fields")
        self.assertEqual(r.get("needs_browser_ok"), "Fetch posts")

    def test_fresh_does_not_grant_browser_permission(self):
        p = self._browser_proj("p_s8c2", browser_flag=True)
        r = node_tools.tool_run_cell(
            p, "Fetch posts", "write_output('posts', [1])", fresh=True,
            reason="the user asked for fresh data")
        self.assertEqual(r.get("needs_browser_ok"), "Fetch posts")

    def test_a_hand_written_launch_is_refused_before_any_card(self):
        p = self._browser_proj("p_s8flag", browser_flag=True)
        bad = ("ctx = pw.chromium.launch_persistent_context(profile)\n"
               "write_output('posts', [1])")
        r = node_tools.tool_run_cell(p, "Fetch posts", bad,
                                     reason="quick session probe")
        self.assertIn("already open", r.get("error", ""))
        good = ("with browser_page() as page:\n"
                "    browser_goto(page, 'https://example.test/posts')\n"
                "    write_output('posts', [1])")
        r2 = node_tools.tool_run_cell(p, "Fetch posts", good,
                                      reason="one live read",
                                      missing="no session state recorded yet")
        self.assertEqual(r2.get("needs_browser_ok"), "Fetch posts")

    def test_every_window_asks_no_build_wide_grant(self):
        p = self._browser_proj("p_s8c3", browser_flag=True)
        p["browser_ok_build"] = True
        r = node_tools.tool_run_cell(
            p, "Fetch posts",
            "write_output('raw_ref', write_file('p.html', 'x'))\n"
            "write_output('posts', [1])",
            reason="need one more live read",
            missing="the recording predates the new column")
        self.assertEqual(r.get("needs_browser_ok"), "Fetch posts")

    def test_browser_ok_unlocks_the_relaunch(self):
        p = self._browser_proj("p_s8c", browser_flag=True)

        p["plan"]["nodes"][0].pop("url", None)
        r = node_tools.tool_run_cell(
            p, "Fetch posts", "write_output('posts', [1])",
            browser_ok=True)
        self.assertTrue(r.get("ok"), r)
        self.assertFalse(r.get("duplicate"))

    def test_first_browser_run_gates_too(self):
        p = _workflow([], pid="p_s8d")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Fetch posts", "type": "browser", "read_only": True,
             "external_impact": "reads a site", "url": "https://example.test/posts",
             "code_sketch": "fetches",
             "outputs": [{"name": "posts", "type": "list"}]}]})
        r = node_tools.tool_run_cell(
            p, "Fetch posts", "write_output('posts', [1])  # playwright",
            reason="first live read of the site")
        self.assertEqual(r.get("needs_browser_ok"), "Fetch posts")
        self.assertFalse(r.get("browser_repeat"))

        r2 = node_tools.tool_run_cell(
            p, "Fetch posts", "write_output('posts', [1])  # playwright")
        self.assertIsNone(r2.get("needs_browser_ok"))
        self.assertIn("reason", r2["error"])

        p["browser_ok_build"] = True
        r3 = node_tools.tool_run_cell(
            p, "Fetch posts", "write_output('posts', [1])  # playwright",
            reason="first live read of the site")
        self.assertEqual(r3.get("needs_browser_ok"), "Fetch posts")

    def test_a_window_outside_a_browser_step_is_refused(self):
        p = _workflow([], pid="p_s8d2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Fetch posts", "type": "connector", "read_only": True,
             "external_impact": "reads a site", "code_sketch": "fetches",
             "outputs": [{"name": "posts", "type": "list"}]}]})

        r = node_tools.tool_run_cell(
            p, "Fetch posts",
            "from playwright.sync_api import sync_playwright\n"
            "write_output('posts', [])",
            reason="checking the button layout")
        self.assertIsNone(r.get("needs_browser_ok"))
        self.assertIn("BROWSER step", r["error"])
        self.assertIn("address it opens", r["error"])

        r2 = node_tools.tool_run_cell(
            p, "peek at the page layout",
            "from playwright.sync_api import sync_playwright\n"
            "write_output('seen', True)",
            reason="checking the button layout")
        self.assertIsNone(r2.get("needs_browser_ok"))
        self.assertIn("browser=true", r2["error"])
        r3 = node_tools.tool_run_cell(
            p, "peek at the page layout",
            "from playwright.sync_api import sync_playwright\n"
            "write_output('seen', True)",
            reason="checking the button layout", browser=True,
            url="https://example.test/")
        self.assertEqual(r3.get("needs_browser_ok"), "peek at the page layout")

    def test_a_probe_names_its_site_and_a_sending_step_says_so(self):
        p = self._browser_proj("p_s8pay")
        r = node_tools.tool_run_cell(
            p, "peek at the list", "write_output('seen', True)",
            reason="how the list loads", browser=True,
            url="https://www.example.test/list")
        self.assertEqual(r.get("needs_browser_ok"), "peek at the list")
        self.assertEqual(r.get("domain"), "example.test")
        p["plan"]["nodes"][0]["read_only"] = False
        r2 = node_tools.tool_run_cell(
            p, "Fetch posts", "write_output('posts', [])", reason="reply in the thread")
        self.assertTrue(r2.get("sends"))

    def test_a_read_connectors_changed_code_answers_from_the_recording(self):
        from agent import cells
        p = _workflow([], pid="p_s8e")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Fetch posts", "type": "connector", "read_only": True,
             "external_impact": "reads a site", "code_sketch": "fetches",
             "outputs": [{"name": "posts", "type": "list"}]}]})
        cells.record("p_s8e", "Fetch posts", "old code", {},
                     {"posts": [{"t": "a"}]}, True, 1.0, [])
        r = node_tools.tool_run_cell(
            p, "Fetch posts", "write_output('posts', [2])")
        self.assertIn("$recorded", r.get("error", ""))
        self.assertIn("missing=", r["error"])
        r2 = node_tools.tool_run_cell(
            p, "Fetch posts", "write_output('posts', [2])",
            missing="the recordings only cover page one")
        self.assertTrue(r2.get("ok"), r2)
        r3 = node_tools.tool_run_cell(
            p, "Fetch posts", "write_output('posts', [3])", fresh=True)
        self.assertTrue(r3.get("ok"), r3)

    def _refused_skeleton(self, p):
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {"summary": "Do the thing.", "nodes": [
            {"name": "Fetch", "type": "code", "code": "write_output('a', 1)",
             "outputs": [{"name": "a", "type": "number"}]},
            {"name": "Save", "type": "connector",
             "external_impact": "writes rows to the sheet",
             "code": "write_output('done', True)",
             "inputs": [{"name": "a", "type": "number"}],
             "outputs": [{"type": "boolean"}]}],
            "edges": [{"src": "Fetch", "dst": "Save"}]})
        assert not r.get("ok"), r
        assert "port needs a name" in str(r.get("errors")), r
        return r

    def test_amend_after_refused_save_merges_into_the_attempt(self):
        p = _workflow([], pid="p_s7f")
        self._refused_skeleton(p)
        r = node_tools.tool_save_plan(p, {"amend": True, "nodes": [
            {"name": "Save", "type": "connector",
             "external_impact": "writes rows to the sheet",
             "code": "write_output('done', True)",
             "inputs": [{"name": "a", "type": "number"}],
             "outputs": [{"name": "done", "type": "boolean"}]}]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual([n["name"] for n in p["plan"]["nodes"]],
                         ["Fetch", "Save"])
        self.assertEqual(len(p["plan"]["edges"]), 1)
        self.assertIsNone(turnstate.of(p).last_plan_attempt)

    def test_partial_retry_merges_into_the_attempt_without_a_flag(self):
        p = _workflow([], pid="p_s7g")
        self._refused_skeleton(p)
        r = node_tools.tool_save_plan(p, {"summary": "Do the thing.", "nodes": [
            {"name": "Save", "type": "connector",
             "external_impact": "writes rows to the sheet",
             "code": "write_output('done', True)",
             "inputs": [{"name": "a", "type": "number"}],
             "outputs": [{"name": "done", "type": "boolean"}]}]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual([n["name"] for n in p["plan"]["nodes"]],
                         ["Fetch", "Save"])

class UndefinedNameLintTest(unittest.TestCase):
    def setUp(self):
        from agent import gate
        self.gate = gate

    def test_an_invented_helper_is_a_finding(self):
        v = self.gate.check_undefined_names("result = frobnicate(read_input('x'))")
        self.assertEqual(len(v), 1)
        self.assertIn("frobnicate", v[0])
        self.assertIn("NameError", v[0])

    def test_the_one_message_names_the_boundary(self):
        v = self.gate.check_undefined_names(
            "ok = ask_user_to_confirm_login()\nwrite_output('ok', ok)")
        self.assertEqual(len(v), 1)
        self.assertIn("cannot talk to the user", v[0])
        self.assertIn("approval popup", v[0])
        self.assertFalse(hasattr(self.gate, "_INTERACTION_PREFIXES"))

    def test_bound_names_imports_and_the_surface_pass(self):
        code = ("import json\n"
                "from urllib.parse import urlparse as up\n"
                "def helper(a, *rest, **kw):\n"
                "    return len(a) + len(rest)\n"
                "items = [x * 2 for x in read_input('rows')]\n"
                "try:\n"
                "    total = helper(items)\n"
                "except ValueError as e:\n"
                "    total = str(e)\n"
                "with open('/tmp/f') as fh:\n"
                "    fh.read()\n"
                "write_output('out', json.dumps({'t': total,\n"
                "                                'u': up('http://x').netloc}))\n")
        self.assertEqual(self.gate.check_undefined_names(code), [])

    def test_a_wildcard_import_disables_the_check(self):
        self.assertEqual(self.gate.check_undefined_names(
            "from os.path import *\nwrite_output('y', join('a', 'b'))"), [])

    def test_proven_code_is_skipped_at_save(self):
        from agent import plan_logic, cells
        p = _workflow([], pid="p_lint1")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        code = "write_output('y', mystery_helper())"
        cells.record("p_lint1", "Odd step", code, {}, {"y": 1}, True, 0.1, [])
        plan = {"summary": "Do.", "nodes": [
            {"name": "Odd step", "type": "code", "code": code,
             "outputs": [{"name": "y", "type": "list"}]}]}
        r = plan_logic.check(p, plan)
        self.assertFalse(any("mystery_helper" in f for f in r["findings"]),
                         r["findings"])

        plan2 = {"summary": "Do.", "nodes": [
            {"name": "Other step", "type": "code", "code": code,
             "outputs": [{"name": "y", "type": "list"}]}]}
        r2 = plan_logic.check(p, plan2)
        self.assertTrue(any("mystery_helper" in f for f in r2["findings"]),
                        r2["findings"])

class DetachedBrowserGateTest(unittest.TestCase):
    def setUp(self):
        from agent import gate
        self.gate = gate

    def test_a_detached_chrome_launch_is_flagged(self):
        code = ("import subprocess\n"
                "subprocess.Popen(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--user-data-dir=x'])\n"
                "write_output('opened', True)\n")
        v = self.gate.check_detached_browser(code)
        self.assertEqual(len(v), 1)
        self.assertIn("ONE step", v[0])
        self.assertIn("outlive its step", v[0])

    def test_the_real_capture_shape_is_flagged_too(self):
        code = ("import subprocess, sys\n"
                "def chrome_path():\n"
                "    if sys.platform == 'darwin':\n"
                "        return ('/Applications/Google Chrome.app'\n"
                "                '/Contents/MacOS/Google Chrome')\n"
                "    return '/usr/bin/google-chrome'\n"
                "proc = subprocess.Popen([chrome_path(), 'about:blank'])\n"
                "write_output('opened', True)\n")
        self.assertEqual(len(self.gate.check_detached_browser(code)), 1)

    def test_a_user_agent_string_is_not_a_binary(self):
        code = ("import subprocess\n"
                "ua = 'Mozilla/5.0 (X11) Chrome/120.0.0.0 Safari/537.36'\n"
                "subprocess.Popen(['/usr/bin/curl', '-A', ua, 'https://x'])\n"
                "write_output('done', True)\n")
        self.assertEqual(self.gate.check_detached_browser(code), [])

    def test_persistent_context_and_other_popens_pass(self):
        ok = ("from playwright.sync_api import sync_playwright\n"
              "with sync_playwright() as pw:\n"
              "    ctx = pw.chromium.launch_persistent_context('/p')\n"
              "    ctx.close()\n")
        self.assertEqual(self.gate.check_detached_browser(ok), [])
        other = ("import subprocess\n"
                 "subprocess.Popen(['/usr/bin/tar', '-czf', 'x.tgz'])\n")
        self.assertEqual(self.gate.check_detached_browser(other), [])

    def test_flagged_even_when_proven(self):
        from agent import plan_logic, cells
        p = _workflow([], pid="p_det1")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        code = ("import subprocess\n"
                "subprocess.Popen(['/usr/bin/google-chrome', 'about:blank'])\n"
                "write_output('opened', True)\n")
        cells.record("p_det1", "Open login window", code, {},
                     {"opened": True}, True, 0.1, [])
        plan = {"summary": "Do.", "nodes": [
            {"name": "Open login window", "type": "browser",
             "external_impact": "opens a window", "code": code,
             "outputs": [{"name": "opened", "type": "list"}]}]}
        r = plan_logic.check(p, plan)
        self.assertTrue(any("outlive its step" in f for f in r["findings"]),
                        r["findings"])

class ChangesDedupeTest(unittest.TestCase):
    def test_removed_lists_dedupe_but_renamed_dicts_survive(self):
        p = _workflow([], pid="p_chdup")
        plan = {"nodes": [], "changes": {
            "removed": ["Log in to LinkedIn", "Log in to LinkedIn"],
            "renamed": [{"from": "A", "to": "B"}]}}
        node_tools.normalise_changes(p, plan)
        self.assertEqual(plan["changes"]["removed"], ["Log in to LinkedIn"])
        self.assertEqual(plan["changes"]["renamed"], [{"from": "A", "to": "B"}])

class EvidenceKeyGatesTest(unittest.TestCase):
    def _proj(self, pid):
        p = _workflow([], pid=pid)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Parse the page", "type": "code",
             "code_sketch": "parses",
             "outputs": [{"name": "rows", "type": "list"}]}]})
        _user_approved(p)
        return p

    def test_a_declined_steps_cell_is_never_rerun(self):
        p = self._proj("p_evk1")
        card = node_tools._transcript.append_request(
            p, "approval", {"title": "Try it?", "step": "Parse the page"})
        node_tools._transcript.append_answer(p, card["iid"], "No.",
                                             shown="deny")
        r = node_tools.tool_run_cell(p, "Parse the page",
                                     "write_output('rows', [1])")
        self.assertIn("declined", r.get("error", ""))
        self.assertIn("ask_again", r.get("error", ""))
        r2 = node_tools.tool_run_cell(p, "Parse the page",
                                      "write_output('rows', [1])",
                                      fresh=True)
        self.assertIn("declined", r2.get("error", ""))

        r3 = node_tools.tool_run_cell(p, "Parse the page",
                                      "write_output('rows', [1])",
                                      ask_again="the page changed and this is the only way to see it")
        self.assertTrue(r3.get("ok"), r3)
        again = node_tools._transcript.append_request(
            p, "approval", {"title": "Try it?", "step": "Parse the page"})
        node_tools._transcript.append_answer(p, again["iid"], "Yes.",
                                             shown="allow")
        self.assertFalse(node_tools.is_declined(p, "Parse the page"))

    def test_a_probe_cannot_shadow_a_steps_recording(self):
        from agent import cells
        p = self._proj("p_evk2")
        cells.record("p_evk2", "Parse the page", "the real proven code", {},
                     {"rows": [1, 2]}, True, 1.0, [])
        r = node_tools.tool_run_cell(
            p, "Parse the page",
            "write_output('debug_marker', 'checking attribution')")
        self.assertIn("OWN plain name", r.get("error", ""))

        r2 = node_tools.tool_run_cell(
            p, "Parse the page", "write_output('rows', [9])")
        self.assertTrue(r2.get("ok"), r2)

    def test_connector_revision_notes_no_resend_and_instructions_early(self):
        from agent import cells
        p = _workflow([], pid="p_evk4")
        node_tools.tool_save_intent(p, "Purpose.")
        cells.record("p_evk4", "Post it",
                     "import json\nwrite_output('posted', True)",
                     {}, {"posted": True}, True, 0.1, [])
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Fetch", "type": "code", "code_sketch": "fetches",
             "outputs": [{"name": "rows", "type": "list"}]},
            {"name": "Post it", "type": "connector",
             "external_impact": "posts a message", "read_only": False,
             "code": "import json\nwrite_output('posted', bool(json))",
             "inputs": [{"name": "rows", "type": "list"}],
             "outputs": [{"name": "posted", "type": "list"}]}]})
        notes = " ".join(r.get("notes") or [])

        self.assertIn("code changed since the last recorded run", notes)
        self.assertIn("No settles the step", notes)
        self.assertIn("no user instructions yet", notes)

    def test_the_built_card_says_tested_per_step(self):
        from agent import cells
        p = self._proj("p_evk6")
        cells.record("p_evk6", "Parse the page", "the proven code", {},
                     {"rows": [1]}, True, 0.1, [])
        plan = {"summary": "Do.", "nodes": [
            {"name": "Parse the page", "type": "code",
             "code": "the proven code",
             "outputs": [{"name": "rows", "type": "list"}]},
            {"name": "Send it", "type": "connector",
             "external_impact": "posts",
             "code": "send_v2()", "minor_revision": "flags only",
             "outputs": [{"name": "ok", "type": "list"}]}]}
        cells.record("p_evk6", "Send it", "send_v1()", {}, {"ok": True},
                     True, 0.1, [])
        payload = node_tools.opening_card_payload(p, plan, "built")
        by = {s["name"]: s.get("tested") for s in payload["steps"]}
        self.assertEqual(by["Parse the page"], "Tested")
        self.assertEqual(by["Send it"],
                         "Not tested - a small change since its test")

        opening = node_tools.opening_card_payload(p, plan, "plan")
        self.assertNotIn("tested", opening["steps"][0])

    def test_step_code_under_a_new_name_is_refused(self):
        from agent import cells
        p = self._proj("p_evk3")
        code = ("data = read_input('page')\n"
                "rows = [x for x in str(data).split() if x]\n"
                "rows = [r.strip().lower() for r in rows if len(r) > 1]\n"
                "write_output('rows', rows)\n")
        cells.record("p_evk3", "Parse the page", code, {}, {"rows": ["a"]},
                     True, 1.0, [])
        r = node_tools.tool_run_cell(p, "Peek at parsing", code, {})
        self.assertIn("never re-run a step's code under a new name",
                      r.get("error", ""))

class DeclareVariablesNoOpTest(unittest.TestCase):
    def test_empty_entries_is_an_error(self):
        p = _workflow([], pid="p_dv1")
        for bad in ([], None, "feed_url", [{"no": "name"} and None]):
            r = node_tools.tool_declare_variables(p, bad)
            self.assertIn("error", r, bad)
        self.assertEqual(p.get("variables") or [], [])

    def test_nameless_entry_still_named_error(self):
        p = _workflow([], pid="p_dv2")
        r = node_tools.tool_declare_variables(p, [{"value": "x"}])
        self.assertIn("name", r.get("error", ""))

    def test_happy_path_unchanged(self):
        p = _workflow([], pid="p_dv3")
        r = node_tools.tool_declare_variables(
            p, [{"name": "feed_url", "value": "https://a.example/feed"}])
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(p["variables"][0]["name"], "feed_url")

class AiStepRecordAnswerTest(unittest.TestCase):
    def _ready(self):
        import providers
        from unittest import mock
        return (mock.patch.object(providers, "is_ready", lambda m: True),
                mock.patch.object(providers, "node_models",
                                  lambda: [{"name": "m1"}]))

    def _skeleton(self, p):
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Judge it", "type": "ai", "prompt": "judge {x}",
             "outputs": [{"name": "verdict", "type": "text"}]}]})

    def test_unchanged_try_answers_from_the_record(self):
        from agent import cells
        from runtime import capability
        from unittest import mock
        p = _workflow([], pid="p_air1")
        self._skeleton(p)
        cells.record("p_air1", "Judge it", "judge {x}", {"x": "a"},
                     {"verdict": "yes"}, True, 0.2, [], kind="ai", model="m1")
        pa, pb = self._ready()
        with pa, pb, mock.patch.object(
                capability, "ai_call",
                side_effect=AssertionError("must not run live")):
            r = node_tools.tool_run_ai_step(p, "Judge it", "judge {x}", "m1",
                                            {"x": "a"}, ["verdict"])
        self.assertTrue(r["ok"], r)
        self.assertTrue(r.get("duplicate"))
        self.assertEqual(r["output"], {"verdict": "yes"})
        self.assertIn("RECORDED", r["note"])

    def test_any_change_runs_live(self):
        from agent import cells
        from runtime import capability
        from unittest import mock
        p = _workflow([], pid="p_air2")
        self._skeleton(p)
        cells.record("p_air2", "Judge it", "judge {x}", {"x": "a"},
                     {"verdict": "yes"}, True, 0.2, [], kind="ai", model="m1")
        pa, pb = self._ready()
        with pa, pb, mock.patch.object(capability, "ai_call",
                                       return_value={"verdict": "no"}):
            r = node_tools.tool_run_ai_step(p, "Judge it", "judge {x} HARDER",
                                            "m1", {"x": "a"}, ["verdict"])
        self.assertTrue(r["ok"], r)
        self.assertFalse(r.get("duplicate"))
        self.assertEqual(r["output"], {"verdict": "no"})

    def test_schema_change_runs_live_never_serves_the_stale_record(self):
        from unittest import mock

        from agent import cells
        from runtime import capability
        p = _workflow([], pid="p_air4")
        self._skeleton(p)
        cells.record("p_air4", "Judge it", "judge {x}", {"x": "a"},
                     {"verdict": "yes"}, True, 0.2, [], kind="ai", model="m1")
        pa, pb = self._ready()
        with pa, pb, mock.patch.object(
                capability, "ai_call",
                return_value={"verdict": "yes", "note": "2 judged"}):
            r = node_tools.tool_run_ai_step(p, "Judge it", "judge {x}", "m1",
                                            {"x": "a"}, ["verdict", "note"])
        self.assertTrue(r["ok"], r)
        self.assertFalse(r.get("duplicate"))
        self.assertEqual(r["output"], {"verdict": "yes", "note": "2 judged"})

    def test_fourth_run_carries_the_information_nudge(self):
        from agent import cells
        from runtime import capability
        from unittest import mock
        p = _workflow([], pid="p_air3")
        self._skeleton(p)
        for i in range(3):
            cells.record("p_air3", "Judge it", f"judge v{i}", {"x": "a"},
                         {"verdict": "yes"}, True, 0.2, [], kind="ai",
                         model="m1")
        pa, pb = self._ready()
        with pa, pb, mock.patch.object(capability, "ai_call",
                                       return_value={"verdict": "no"}):
            r = node_tools.tool_run_ai_step(p, "Judge it", "judge v9", "m1",
                                            {"x": "a"}, ["verdict"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["runs"], 4)
        self.assertIn("information", r["note"])

class FreezeStepTest(unittest.TestCase):
    def _plan(self, p, **node):
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        p["plan"] = {"status": "draft", "summary": "Do.",
                     "nodes": [node], "edges": []}

    def test_freezes_a_proven_code_step_complete(self):
        p = _workflow([], pid="p_frz1")
        self._plan(p, name="Shape it", type="code",
                   description="shapes the value", intention="shape",
                   inputs=[{"name": "x", "type": "text"}],
                   outputs=[{"name": "y", "type": "text"}],
                   code="write_output('y', read_input('x').upper())",
                   tests=[{"name": "recorded run", "inputs": {"x": "ab"},
                           "expect": "ok", "asserts": []}])
        r = node_tools.build_step(p, "Shape it")
        self.assertTrue(r["ok"], r)
        n = node_tools.tool_read_node(p, "Shape it")
        self.assertIn("upper()", n["config"]["code"])
        self.assertEqual(len(n["tests"]), 1)
        self.assertTrue(node_tools.validate_node(p, r["node_id"])["ok"])

    def test_refuses_a_step_with_no_proven_code(self):
        p = _workflow([], pid="p_frz2")
        self._plan(p, name="Mystery", type="code", code_sketch="does things",
                   outputs=[{"name": "y", "type": "text"}])
        r = node_tools.build_step(p, "Mystery")
        self.assertFalse(r.get("ok"))
        self.assertIn("no proven code", str(r.get("errors")))

    def test_plan_criteria_materialise_hard_by_default_with_label(self):
        p = _workflow([], pid="p_frz_cr1")
        self._plan(p, name="Shape it", type="code",
                   description="shapes", intention="shape",
                   inputs=[{"name": "x", "type": "text"}],
                   outputs=[{"name": "y", "type": "text"}],
                   code="write_output('y', read_input('x').upper())",
                   tests=[{"name": "recorded run", "inputs": {"x": "ab"},
                           "expect": "ok", "asserts": []}],
                   criteria=[{"expr": "len(y) > 0",
                              "label": "The result is never empty"}])
        r = node_tools.build_step(p, "Shape it")
        self.assertTrue(r["ok"], r)
        crit = node_tools.tool_read_node(p, "Shape it")["config"]["criteria"]
        self.assertEqual(len(crit), 1)
        self.assertEqual(crit[0]["hardness"], "hard")
        self.assertEqual(crit[0]["label"], "The result is never empty")

    def test_bad_plan_criterion_expr_refused_at_save(self):
        p = _workflow([], pid="p_frz_cr2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Shape it", "type": "code",
             "code": "write_output('y', 1)",
             "outputs": [{"name": "y", "type": "number"}],
             "criteria": [{"expr": "len(y) >", "label": "broken"}]}]})
        self.assertTrue(r.get("errors"))
        self.assertIn("not a valid python expression", str(r["errors"]))

    def test_timeout_override_syncs_from_the_plan(self):
        step = dict(name="Slow pull", type="code",
                    description="pulls", intention="pull",
                    inputs=[{"name": "x", "type": "text"}],
                    outputs=[{"name": "y", "type": "text"}],
                    code="write_output('y', read_input('x').upper())",
                    tests=[{"name": "recorded run", "inputs": {"x": "ab"},
                            "expect": "ok", "asserts": []}])
        p = _workflow([], pid="p_frz_t1")
        self._plan(p, **step, timeout_seconds=999999)
        r = node_tools.build_step(p, "Slow pull")
        self.assertTrue(r["ok"], r)
        from runtime import sandbox
        n = node_tools.tool_read_node(p, "Slow pull")
        self.assertEqual(n["config"]["timeout_seconds"], 999999)
        self._plan(p, **step)
        node_tools.build_step(p, "Slow pull")
        n = node_tools.tool_read_node(p, "Slow pull")
        self.assertNotIn("timeout_seconds", n["config"])

    def test_ai_step_syncs_max_tokens_from_the_plan(self):
        p = _workflow([], pid="p_frz_mt")
        self._plan(p, name="Judge it", type="ai", prompt="Judge {x}.",
                   model="", max_tokens=64_000,
                   inputs=[{"name": "x", "type": "text"}],
                   outputs=[{"name": "verdict", "type": "text"}],
                   tests=[{"name": "authored", "inputs": {"x": "a"},
                           "expect": "ok", "asserts": []}])
        r = node_tools.build_step(p, "Judge it")
        self.assertTrue(r["ok"], r)
        n = node_tools.tool_read_node(p, "Judge it")
        self.assertEqual(n["config"]["max_tokens"], 64_000)
        from runtime import capability
        self.assertEqual(capability.step_max_tokens(n), 64_000)

        self._plan(p, name="Judge it", type="ai", prompt="Judge {x}.",
                   model="",
                   inputs=[{"name": "x", "type": "text"}],
                   outputs=[{"name": "verdict", "type": "text"}],
                   tests=[{"name": "authored", "inputs": {"x": "a"},
                           "expect": "ok", "asserts": []}])
        r = node_tools.build_step(p, "Judge it")
        self.assertTrue(r["ok"], r)
        n = node_tools.tool_read_node(p, "Judge it")
        self.assertNotIn("max_tokens", n["config"])

    def test_ai_step_with_unconfigured_model_freezes_blank(self):
        p = _workflow([], pid="p_frz3")
        self._plan(p, name="Judge it", type="ai", prompt="Judge {x}.",
                   model="not-a-real-model",
                   inputs=[{"name": "x", "type": "text"}],
                   outputs=[{"name": "verdict", "type": "text"}],
                   tests=[{"name": "authored", "inputs": {"x": "a"},
                           "expect": "ok", "asserts": []}])
        r = node_tools.build_step(p, "Judge it")

        self.assertTrue(r["ok"], r)
        self.assertIn("not set up in Admin", str(r.get("notes")))
        n = node_tools.tool_read_node(p, "Judge it")
        self.assertEqual(n["config"]["model"]["model"], "")
        self.assertTrue(n["config"]["prompt"])
        v = node_tools.validate_node(p, r["node_id"])
        self.assertTrue(v["ok"], v)
        self.assertIn("model", v["advisory"])

    def test_stale_wrong_type_node_is_replaced_not_reused(self):
        p = _workflow([{"id": "n_stale", "name": "Connect", "type": "code",
                       "config": {"code": "write_output('ok', 1)"},
                       "inputs": [], "outputs": [], "tests": []}])
        self._plan(p, name="Connect", type="connector",
                   external_impact="joins the chosen wifi network",
                   read_only=False,
                   inputs=[{"name": "ssid", "type": "text"}],
                   outputs=[{"name": "ok", "type": "boolean"}],
                   code="write_output('ok', bool(read_input('ssid')))",
                   tests=[{"name": "recorded run", "inputs": {"ssid": "x"},
                           "expect": "ok", "asserts": []}])
        r = node_tools.build_step(p, "Connect")
        self.assertTrue(r["ok"], r)
        n = node_tools.tool_read_node(p, "Connect")
        self.assertNotEqual(n["id"], "n_stale")
        self.assertEqual(node_tools.node_type_of(n), "connector")
        self.assertFalse(n["read_only"])
        self.assertIn("wifi", n["external_impact"])
        self.assertIn("re-wire", str(r.get("notes")))

    def test_reused_connector_syncs_approval_identity(self):
        p = _workflow([{"id": "n_c", "name": "Send", "type": "connector",
                       "config": {"code": "write_output('sent', bool(read_input('to')))"},
                       "read_only": True, "external_impact": "old text",
                       "inputs": [{"name": "to", "type": "text"}],
                       "outputs": [{"name": "sent", "type": "boolean"}],
                       "tests": []}])
        self._plan(p, name="Send", type="connector", read_only=False,
                   external_impact="sends the confirmation email",
                   inputs=[{"name": "to", "type": "text"}],
                   outputs=[{"name": "sent", "type": "boolean"}],
                   code="write_output('sent', bool(read_input('to')))",
                   tests=[{"name": "recorded run", "inputs": {"to": "a@b"},
                           "expect": "ok", "asserts": []}])
        r = node_tools.build_step(p, "Send")
        n = node_tools.tool_read_node(p, "Send")
        self.assertEqual(n["id"], "n_c")
        self.assertFalse(n["read_only"])
        self.assertEqual(n["external_impact"], "sends the confirmation email")

    def test_failed_freeze_rolls_back_its_own_create(self):
        p = _workflow([], pid="p_frz_rb")
        self._plan(p, name="Mystery", type="code", code_sketch="does things",
                   outputs=[{"name": "y", "type": "text"}])
        r = node_tools.build_step(p, "Mystery")
        self.assertFalse(r.get("ok"))
        self.assertIsNone(node_tools.find_node(p, "Mystery"))

    def test_fill_derives_output_types_from_recorded_evidence(self):
        from agent import cells
        cells.record("p_frz_dt", "Scan", "code", {},
                     {"nets": ["a", "b"], "count": 3}, True, 0.1, [])
        nodes = [{"name": "Scan", "type": "code",
                  "outputs": [{"name": "nets", "type": "record"},
                              {"name": "count", "type": "number"}]}]
        node_tools.fill_from_cells("p_frz_dt", nodes)
        self.assertEqual(nodes[0]["outputs"][0]["type"], "list")
        self.assertEqual(nodes[0]["outputs"][1]["type"], "number")

    def test_the_freeze_functions_survive_without_a_surface(self):
        self.assertFalse(hasattr(node_tools, "_FREEZE_SPECS"))
        self.assertTrue(callable(node_tools.build_step))

class UntriedConnectorTest(unittest.TestCase):
    CODE = "write_output('saved', bool(read_input('rows')))"

    def _plan(self, p, code=None):
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        p["plan"] = {"status": "draft", "summary": "Do.",
                     "nodes": [{"name": "Save rows", "type": "connector",
                                "read_only": False,
                                "external_impact": "adds rows to the sheet",
                                "inputs": [{"name": "rows", "type": "list"}],
                                "outputs": [{"name": "saved",
                                             "type": "boolean"}],
                                "code": code or self.CODE}],
                     "edges": []}

    def test_a_never_sent_connector_builds_green(self):
        p = _workflow([], pid="p_force1")
        self._plan(p)
        r = node_tools.build_step(p, "Save rows")
        self.assertTrue(r["ok"], r)
        n = node_tools.tool_read_node(p, "Save rows")
        self.assertNotIn("first_run_is_test", n["config"])
        self.assertTrue(node_tools.validate_node(p, r["node_id"])["ok"])

    def test_changing_its_code_does_not_re_gate_it(self):
        p = _workflow([], pid="p_force3")
        self._plan(p)
        r = node_tools.build_step(p, "Save rows")
        node_tools._set_code(
            p, r["node_id"],
            "write_output('saved', not bool(read_input('rows')))")
        self.assertTrue(node_tools.validate_node(p, r["node_id"])["ok"])

    def test_a_recorded_run_still_becomes_the_step_evidence(self):
        from agent import cells
        cells.record("p_force4", "Save rows", self.CODE, {"rows": ["a"]},
                     {"saved": True}, True, 0.1, [])
        p = _workflow([], pid="p_force4")
        self._plan(p)
        node_tools.fill_from_cells(p["id"], p["plan"]["nodes"])
        r = node_tools.build_step(p, "Save rows")
        self.assertTrue(r["ok"], r)
        self.assertTrue(node_tools.tool_read_node(p, "Save rows")["tests"])

class CodificationIsGoneTest(unittest.TestCase):
    def test_a_built_node_carries_none_of_it(self):
        p = _workflow([], pid="p_codif")
        node_tools._create_node(p, "Step", "code", "does a thing")
        node = p["nodes"][0]
        for dead in ("provenance", "preconditions", "deopt_to", "status"):
            self.assertNotIn(dead, node, dead)

    def test_stale_keys_on_disk_never_reach_the_model(self):
        p = _workflow([{"id": "n1", "name": "Old", "type": "code", "config": {},
                       "inputs": [], "outputs": [], "tests": [],
                       "status": "active", "provenance": "native",
                       "preconditions": [], "deopt_to": None,
                       "clean_run_streak": 0, "thresholds": {}}],
                     pid="p_codif2")
        got = node_tools.tool_read_node(p, "n1")
        for dead in ("status", "provenance", "preconditions", "deopt_to",
                     "clean_run_streak", "thresholds"):
            self.assertNotIn(dead, got, dead)

    def test_criteria_no_longer_carry_a_source(self):
        clean, errors = node_tools.clean_criteria(
            [{"expr": "rows > 0", "source": "provenance"}], "criterion")
        self.assertEqual(errors, [])
        self.assertNotIn("source", clean[0])

class FixDiagnosisCardTest(unittest.TestCase):
    def _fixing(self, pid, tid="tkt_abc123"):
        p = _workflow([{"id": "n1", "name": "Do it", "type": "code",
                       "config": {"code": "write_output('y', 1)"},
                       "inputs": [],
                       "outputs": [{"name": "y", "type": "number"}],
                       "tests": []}], pid=pid)
        p["plan_approved_ts"] = 1.0
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        p["tickets"] = [{"id": tid, "node_id": "n1", "status": "open"}]
        turnstate.of(p).fix_issue_id = tid
        node_tools.tool_run_cell(p, "Do it", "write_output('y', 2)", {})
        plan = {"summary": "Does a thing.", "nodes": [
            {"name": "Do it", "type": "code",
             "code": "write_output('y', 2)",
             "outputs": [{"name": "y", "type": "number"}]}],
            "deliverables": ["y"],

            "fix_note": {"went_wrong": "It produced the wrong number.",
                         "will_change": "It will count properly."}}
        return p, plan

    def test_the_fix_save_stamps_the_issue_and_raises_the_card(self):
        p, plan = self._fixing("p_fxc1")
        node_tools.tool_save_plan(p, dict(plan))
        self.assertEqual(p["plan"]["ticket_id"], "tkt_abc123")
        self.assertTrue(turnstate.of(p).opening_card_show_now)

        turnstate.of(p).take("opening_card_show_now")
        r = node_tools.tool_build_workflow(p)
        self.assertIn("hasn't approved this fix", r.get("error", ""))

    def test_the_approval_is_per_fix_never_global(self):
        p, plan = self._fixing("p_fxc2")
        node_tools.tool_save_plan(p, dict(plan))
        p["plan"]["fix_approved_ts"] = 2.0

        node_tools.tool_save_plan(p, dict(plan, fix_approved_ts=99.0))
        self.assertEqual(p["plan"]["fix_approved_ts"], 2.0)
        self.assertEqual(p["plan_approved_ts"], 1.0)
        self.assertTrue(node_tools.tool_build_workflow(p).get("ok"))

    def test_a_new_fix_owes_a_new_card(self):
        p, plan = self._fixing("p_fxc3")
        node_tools.tool_save_plan(p, dict(plan))
        p["plan"]["fix_approved_ts"] = 2.0

        turnstate.of(p).fix_issue_id = "tkt_def456"
        p["tickets"].append({"id": "tkt_def456", "node_id": "n1",
                             "status": "open"})
        node_tools.tool_save_plan(p, dict(plan))
        self.assertEqual(p["plan"]["ticket_id"], "tkt_def456")
        self.assertNotIn("fix_approved_ts", p["plan"])
        self.assertTrue(turnstate.of(p).opening_card_show_now)

    def test_an_ordinary_change_says_what_will_change(self):
        p, plan = self._fixing("p_fxc4")
        turnstate.of(p).take("fix_issue_id")
        plan = dict(plan, change_note={"found": "The step needs a tweak.",
                                       "will_change": "The step will change."})
        node_tools.tool_save_plan(p, dict(plan))
        self.assertTrue(turnstate.of(p).opening_card_show_now)
        self.assertNotIn("ticket_id", p["plan"])

        p["plan"]["change_approved_ts"] = 1.0
        turnstate.of(p).take("opening_card_show_now")
        node_tools.tool_save_plan(p, dict(plan))
        self.assertFalse(turnstate.of(p).opening_card_show_now)
        self.assertTrue(node_tools.tool_build_workflow(p).get("ok"))

class BuildIsAToolTest(unittest.TestCase):
    def _proven(self, pid, deliverable="y"):
        from agent import actions
        p = _workflow([], pid=pid)
        p["plan_approved_ts"] = 1.0
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        plan = {"summary": "Does a thing.", "nodes": [
            {"name": "Do it", "type": "code", "code": "write_output('y', 1)",
             "outputs": [{"name": "y", "type": "number"}]}],
            "deliverables": [deliverable]}
        node_tools.tool_save_plan(p, dict(plan))
        node_tools.tool_run_cell(p, "Do it", "write_output('y', 1)", {})
        node_tools.tool_save_plan(p, dict(plan))
        turnstate.of(p).take("opening_card_show_now")
        return p, actions

    def test_a_proven_plan_builds_in_one_tool_call(self):
        p, actions = self._proven("p_bit1")
        events = []
        env = actions.execute(p, {"name": "build_workflow", "input": {}},
                              events.append)
        self.assertTrue(env["ok"], env)
        self.assertEqual(p["plan"]["status"], "built")
        self.assertEqual([n["name"] for n in p["nodes"]], ["Do it"])
        self.assertIsNone(turnstate.of(p).build_now)
        self.assertIn("build-start", [e.get("type") for e in events])

        from agent import steps, transcript
        self.assertTrue(turnstate.of(p).built)
        card = transcript.last_request(p, "blueprint")["payload"]
        self.assertEqual(card["done"], steps.DONE_LINES)
        self.assertIn("OVER", node_tools.turn_over_error(p, "save_plan"))

    def test_a_failed_build_leaves_the_workflow_as_it_was(self):
        from unittest import mock
        from agent import orchestrator
        p, _ = self._proven("p_bit_rollback")
        before = [n.get("id") for n in p.get("nodes") or []]
        p["approval_grants"] = [{"kind": "send", "step": "Do it"}]
        with mock.patch("agent.receipts.check", return_value={"findings": ["a finding"]}):
            r = orchestrator.build_now(p, lambda ev: None)
        self.assertFalse(r["ok"])
        self.assertEqual([n.get("id") for n in p.get("nodes") or []], before)
        self.assertNotIn("built_ts", p["plan"])
        self.assertEqual(p["approval_grants"], [{"kind": "send", "step": "Do it"}])

    def test_pre_build_narration_persists_above_the_built_card(self):
        import turns
        from agent import transcript
        p, actions = self._proven("p_bit_narr")
        turns.begin(p["id"], "chat")
        turns.record(p["id"], {"type": "delta", "text": "Every step is ready - building it now."})
        events = []
        turns.bind_emit(p["id"], events.append)
        env = actions.execute(p, {"name": "build_workflow", "input": {}}, events.append)
        self.assertTrue(env["ok"], env)
        kinds = [(i.get("kind"), i.get("request"), (i.get("text") or "")[:20])
                 for i in transcript.items(p)]

        msg_at = next(k for k, i in enumerate(kinds) if i[0] == "message" and i[2].startswith("Every step"))
        built_at = next(k for k, i in enumerate(kinds) if i[1] == "blueprint")
        self.assertLess(msg_at, built_at)
        self.assertIn("shown", [e.get("type") for e in events])
        turns.finish(p["id"])

    def test_the_built_card_is_never_paired_to_a_later_message(self):
        from agent import transcript
        p, actions = self._proven("p_bit_pair")
        actions.execute(p, {"name": "build_workflow", "input": {}}, lambda e: None)
        self.assertEqual(node_tools.settle_open_asks(p), 0)
        self.assertEqual([i for i in transcript.items(p) if i.get("kind") == "answer"], [])

    def test_a_build_that_finds_something_is_an_ordinary_tool_error(self):
        from unittest import mock
        from agent import receipts
        p, actions = self._proven("p_bit2")
        with mock.patch.object(receipts, "check", return_value={
                "ok": False, "findings": ["the seam from \"Do it\" is broken"],
                "notes": [], "credential_scan": {}}):
            env = actions.execute(p, {"name": "build_workflow", "input": {}},
                                  lambda e: None)
        self.assertFalse(env["ok"])
        self.assertIn("the seam from", env["error"])
        self.assertIn("save_plan", env["error"])
        self.assertEqual(p["plan"]["status"], "draft")
        self.assertIsNone(turnstate.of(p).built)

        self.assertEqual(node_tools.turn_over_error(p, "save_plan"), "")

    def test_a_green_build_needs_no_ai_key(self):
        from unittest import mock
        from agent import orchestrator
        p, actions = self._proven("p_bit3")
        with mock.patch.object(orchestrator, "_master_key",
                               return_value=(None, "")):
            env = actions.execute(p, {"name": "build_workflow", "input": {}},
                                  lambda e: None)
        self.assertTrue(env["ok"], env)
        self.assertEqual(p["plan"]["status"], "built")

class BuildFixTriggerTest(unittest.TestCase):
    def test_build_workflow_arms_the_build(self):
        p = _workflow([], pid="p_bw")
        p["plan_approved_ts"] = 1.0
        p["plan"] = {"status": "draft", "summary": "s",
                     "nodes": [{"name": "x", "type": "code",
                                "code": "write_output('y', 1)"}]}
        r = node_tools.tool_build_workflow(p)
        self.assertTrue(r["ok"])
        self.assertEqual(turnstate.of(p).build_now, {"include_proposals": None})

    def test_build_workflow_needs_a_plan(self):
        p = _workflow([], pid="p_bw2")
        p["plan"] = {"status": "built"}
        r = node_tools.tool_build_workflow(p)
        self.assertIn("error", r)
        self.assertNotIn("_pending_build", p)

    def test_resolve_issue_stashes_pending_fix(self):
        p = _workflow([], pid="p_ri")
        p["tickets"] = [{"id": "tkt_9", "status": "open"}]
        r = node_tools.tool_resolve_issue(p)
        self.assertTrue(r["ok"])
        self.assertEqual(turnstate.of(p).pending_fix, {"ticket_id": "tkt_9"})

    def test_resolve_issue_no_open_tickets(self):
        p = _workflow([], pid="p_ri2")
        p["tickets"] = [{"id": "tkt_x", "status": "resolved"}]
        r = node_tools.tool_resolve_issue(p)
        self.assertIn("error", r)
        self.assertIsNone(turnstate.of(p).pending_fix)

class IssueResolvesOnAGreenBuildTest(unittest.TestCase):
    def _ticketed_workflow(self):
        from runtime import corpus
        p = _workflow([{"id": "n_ll", "name": "parse", "type": "code",
                       "config": {"code": "write_output('y', 1)"},
                       "inputs": [], "outputs": [], "tests": []}], pid="p_loop")
        cid = corpus.record_failure("p_loop", "n_ll", {"x": 1},
                                    verdict={"thrown": "X"})
        p["tickets"] = [{"id": "tkt_1", "status": "in-progress",
                         "node_id": "n_ll", "case_id": cid, "reason": "r",
                         "verdict": {}, "notes": ""}]
        return p, cid

    def test_the_retrospective_tool_is_gone(self):
        self.assertFalse(hasattr(node_tools, "tool_close_ticket_retrospective"))
        self.assertNotIn("mcp__cryogram__close_ticket_retrospective",
                         node_tools.AGENT_TOOL_NAMES)

    def test_a_green_fix_makes_the_issue_ready_without_agent_prose(self):
        from agent import orchestrator
        p, _ = self._ticketed_workflow()
        orchestrator._gate_ticket_fix(p, {"ticket_id": "tkt_1",
                                       "summary": "Add a retry."},
                                   lambda e: None)
        t = p["tickets"][0]
        self.assertEqual(t["status"], "ready")
        self.assertNotIn("retrospective", t)

    def test_the_plan_carries_the_issue_across_a_re_save(self):
        p, _ = self._ticketed_workflow()
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        p["plan"] = {"status": "draft", "summary": "Fix it.", "nodes": [],
                     "ticket_id": "tkt_1"}
        node_tools.tool_save_plan(p, {"summary": "Fix it.", "nodes": [
            {"name": "parse", "type": "code", "code": "write_output('y', 2)",
             "outputs": [{"name": "y", "type": "number"}]}]})
        self.assertEqual(p["plan"].get("ticket_id"), "tkt_1")

    def test_the_model_cannot_claim_an_issue_itself(self):
        p, _ = self._ticketed_workflow()
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "ticket_id": "tkt_1",
                                      "change_note": {"found": "F.", "will_change": "W."},
                                      "nodes": [
            {"name": "parse", "type": "code", "code": "write_output('y', 2)",
             "outputs": [{"name": "y", "type": "number"}]}]})
        self.assertIsNone(p["plan"].get("ticket_id"))

class AutoWireTest(unittest.TestCase):
    def _two(self):
        return _workflow([
            {"id": "n_a", "name": "A", "type": "code", "config": {"code": "x=1"},
             "inputs": [], "outputs": [{"name": "v", "type": "text"}], "tests": []},
            {"id": "n_b", "name": "B", "type": "code", "config": {"code": "x=1"},
             "inputs": [{"name": "v", "type": "text"}], "outputs": [], "tests": []}])

    def test_wires_missing_plan_edges_once(self):
        p = self._two()
        plan = {"edges": [{"src": "A", "dst": "B"}]}
        self.assertEqual(node_tools.auto_wire_plan_edges(p, plan), ["A -> B"])
        self.assertEqual(len(p["edges"]), 1)
        self.assertEqual(p["edges"][0], {"src": "n_a", "dst": "n_b", "when": ""})
        self.assertEqual(node_tools.auto_wire_plan_edges(p, plan), [])
        self.assertEqual(len(p["edges"]), 1)

    def test_carries_when_and_skips_unbuilt_ends(self):
        p = self._two()
        plan = {"edges": [{"src": "A", "dst": "B", "when": "v == 'x'"},
                          {"src": "A", "dst": "Ghost"}]}
        self.assertEqual(node_tools.auto_wire_plan_edges(p, plan), ["A -> B"])
        self.assertEqual(p["edges"][0]["when"], "v == 'x'")

class SeamAndChangesTest(unittest.TestCase):
    def test_consumer_input_type_corrected_from_evidence(self):
        from agent import cells
        cells.record("p_seam1", "Scan", "code", {}, {"networks": ["a", "b"]},
                     True, 0.1, [])
        plan = {"nodes": [
            {"name": "Scan", "type": "code",
             "outputs": [{"name": "networks", "type": "list"}]},
            {"name": "Pick", "type": "user-input",
             "inputs": [{"name": "networks", "type": "record"}],
             "outputs": [{"name": "chosen", "type": "text"}]}],
            "edges": [{"src": "Scan", "dst": "Pick"}]}
        changed = node_tools.derive_seam_types("p_seam1", plan)
        self.assertEqual(changed, ["Pick"])
        self.assertEqual(plan["nodes"][1]["inputs"][0]["type"], "list")

    def test_evidence_less_seam_conflict_is_a_design_gap(self):
        from agent import plan_logic
        plan = {"summary": "s", "nodes": [
            {"name": "A", "type": "code", "code_sketch": "makes v",
             "outputs": [{"name": "v", "type": "text"}]},
            {"name": "B", "type": "code", "code_sketch": "uses v",
             "inputs": [{"name": "v", "type": "record"}],
             "outputs": [{"name": "out", "type": "text"}]}],
            "edges": [{"src": "A", "dst": "B"}]}
        r = plan_logic.check({"id": "p_seam2", "nodes": [], "edges": [],
                              "variables": []}, plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("one shape per value" in f for f in r["findings"]),
                        r["findings"])

    def test_changes_list_normalised_and_auto_derived(self):
        node = {"id": "n_a", "name": "A", "type": "code",
                "config": {"code": "write_output('y', read_input('x'))"},
                "inputs": [{"name": "x", "type": "text"}],
                "outputs": [{"name": "y", "type": "text"}], "tests": []}
        p = _workflow([dict(node)])
        plan = {"nodes": [
            {"name": "A", "type": "code",
             "code": "write_output('y', read_input('x'))",
             "inputs": [{"name": "x", "type": "text"}],
             "outputs": [{"name": "y", "type": "text"}]},
            {"name": "B", "type": "code", "code": "write_output('z', 1)",
             "outputs": [{"name": "z", "type": "number"}]}]}
        node_tools.normalise_changes(p, plan)
        self.assertEqual(plan["changes"]["modified"], [])
        self.assertEqual(plan["changes"]["added"], ["B"])
        plan2 = {"changes": ["A"], "nodes": []}
        node_tools.normalise_changes(p, plan2)
        self.assertEqual(plan2["changes"]["modified"], ["A"])
        (plan2["changes"] or {}).get("removed")

class AskSentinelAndDuplicateCellTest(unittest.TestCase):
    def test_sentinel_ask_flags_the_open_question(self):
        p = _workflow([], pid="p_askflag")
        node_tools.tool_ask_user(p, "Which network?")
        self.assertTrue(turnstate.of(p).ask_open)

    def test_identical_ok_cell_is_answered_from_the_record(self):
        p = _workflow([], pid="p_dupcell")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Connect", "type": "code", "code_sketch": "connects",
             "outputs": [{"name": "connected", "type": "boolean"}]}]})
        code = "write_output('connected', True)"
        r1 = node_tools.tool_run_cell(p, "Connect", code, {})
        self.assertTrue(r1["ok"], r1)
        self.assertNotIn("duplicate", r1)
        r2 = node_tools.tool_run_cell(p, "Connect", code, {})
        self.assertTrue(r2["duplicate"])
        self.assertEqual(r2["output"], r1["output"])
        r3 = node_tools.tool_run_cell(p, "Connect",
                                      code + "  # changed", {})
        self.assertNotIn("duplicate", r3)

    def test_unchanged_code_answers_from_record_regardless_of_inputs(self):
        p = _workflow([], pid="p_dupcell2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Fetch", "type": "code", "code_sketch": "fetches",
             "outputs": [{"name": "posts", "type": "list"}]}]})
        code = "write_output('posts', [1, 2])"
        r1 = node_tools.tool_run_cell(p, "Fetch", code, {})
        self.assertTrue(r1["ok"], r1)

        r2 = node_tools.tool_run_cell(p, "Fetch", code, {"limit": 5})
        self.assertTrue(r2["duplicate"])
        self.assertIn("NOT executed", r2["note"])
        self.assertEqual(r2["output"], r1["output"])

        r3 = node_tools.tool_run_cell(p, "Fetch", code + "   \n", {})
        self.assertTrue(r3["duplicate"])

        r4 = node_tools.tool_run_cell(p, "Fetch", code, {}, fresh=True)
        self.assertNotIn("duplicate", r4)
        self.assertTrue(r4["ok"], r4)

class AiTryHoldsTheAnswerToTheContractTest(unittest.TestCase):
    OUTS = [{"name": "triaged", "type": "list",
             "item_fields": [{"name": "id", "type": "text", "required": True},
                             {"name": "verdict", "type": "text", "required": True}]},
            {"name": "summary", "type": "text"}]

    def _p(self, pid):
        from storage import store
        node = {"id": "n1", "name": "Triage", "type": "ai",
                "config": {"prompt": "judge", "model": {"model": "m1"}},
                "inputs": [{"name": "feed", "type": "list"}],
                "outputs": self.OUTS, "tests": []}
        p = _workflow([node], pid=pid)
        store.save(p)
        return p

    def _try(self, p, answer):
        import providers
        from runtime import capability
        from unittest import mock
        with mock.patch.object(providers, "is_ready", lambda m: True), \
             mock.patch.object(providers, "node_models", lambda: [{"name": "m1"}]), \
             mock.patch.object(capability, "ai_call", return_value=answer):
            return node_tools.tool_run_ai_step(p, "Triage", "judge", "m1",
                                               {"feed": {"posts": []}},
                                               self.OUTS)

    def test_an_answer_missing_a_declared_output_is_not_ok(self):
        r = self._try(self._p("p_aichk1"), {"summary": "s"})
        self.assertFalse(r["ok"])
        self.assertIn("Triaged", r["error"])
        self.assertIn("not produced", r["error"])

    def test_a_wrong_shaped_answer_is_not_ok(self):
        r = self._try(self._p("p_aichk2"), {"triaged": "x" * 4612,
                                            "summary": "s"})
        self.assertFalse(r["ok"])
        self.assertIn("should be a list", r["error"])

    def test_the_try_enforces_the_same_per_item_shape_as_the_run(self):
        r = self._try(self._p("p_aichk3"),
                      {"triaged": [{"id": "a"}], "summary": "s"})
        self.assertFalse(r["ok"])
        self.assertIn("verdict", r["error"])

    def test_a_real_answer_still_passes(self):
        r = self._try(self._p("p_aichk4"),
                      {"triaged": [{"id": "a", "verdict": "ENGAGE"}],
                       "summary": "s"})
        self.assertTrue(r["ok"], r)

class HumanNameRailTest(unittest.TestCase):
    def test_humanise_function(self):
        h = node_tools.humanise_name
        self.assertEqual(h("get_eur_rate"), "Get eur rate")
        self.assertEqual(h("fetch-rate"), "Fetch rate")
        self.assertEqual(h("get_EUR_rate"), "Get EUR rate")
        self.assertEqual(h("Get EUR to NOK rate"), "Get EUR to NOK rate")
        self.assertEqual(h("Prepare rate row"), "Prepare rate row")
        self.assertEqual(h(""), "")

    def test_seams_apply_the_same_rail(self):
        p = _workflow([], pid="p_hname")
        r = node_tools._create_node(p, "scan_networks", "code")
        node = next(n for n in p["nodes"] if n["id"] == r["node_id"])
        self.assertEqual(node["name"], "Scan networks")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "fetch_rate", "type": "code", "code_sketch": "x",
             "outputs": [{"name": "rate", "type": "number"}]},
            {"name": "store_rate", "type": "code", "code_sketch": "y",
             "inputs": [{"name": "rate", "type": "number"}]}],
            "edges": [{"src": "fetch_rate", "dst": "store_rate"}],
            "changes": ["store_rate"],
            "change_note": {"found": "The rate is not stored.", "will_change": "It will be."}})
        names = [n["name"] for n in p["plan"]["nodes"]]
        self.assertEqual(names, ["Fetch rate", "Store rate"])

        ids = {n["name"]: n["id"] for n in p["plan"]["nodes"]}
        e = p["plan"]["edges"][0]
        self.assertEqual((e["src"], e["dst"]), (ids["Fetch rate"], ids["Store rate"]))
        self.assertIn(ids["Store rate"], p["plan"]["changes"]["modified"])

class UniqueNameRailTest(unittest.TestCase):
    def test_save_plan_refuses_duplicate_step_names(self):
        p = _workflow([], pid="p_uniq1")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "fetch_rate", "type": "code", "code_sketch": "x",
             "outputs": [{"name": "rate", "type": "number"}]},
            {"name": "Fetch rate", "type": "code", "code_sketch": "y",
             "outputs": [{"name": "rate2", "type": "number"}]}]})
        self.assertFalse(r.get("ok"), r)
        self.assertTrue(any("share the name" in e for e in r["errors"]), r)

    def test_create_node_refuses_an_existing_name(self):
        p = _workflow([], pid="p_uniq2")
        r1 = node_tools._create_node(p, "Scan networks", "code")
        self.assertTrue(r1.get("ok"), r1)

        r2 = node_tools._create_node(p, "scan_networks", "code")
        self.assertIn("already exists", r2.get("error", ""))
        self.assertEqual(len(p["nodes"]), 1)

    def test_freeze_still_updates_an_existing_node_in_place(self):
        p = _workflow([], pid="p_uniq3")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        step = dict(name="Shape it", type="code", description="shapes",
                    inputs=[{"name": "x", "type": "text"}],
                    outputs=[{"name": "y", "type": "text"}],
                    code="write_output('y', read_input('x').upper())",
                    tests=[{"name": "recorded run", "inputs": {"x": "ab"},
                            "expect": "ok", "asserts": []}])
        p["plan"] = {"status": "draft", "summary": "Do.",
                     "nodes": [dict(step)], "edges": []}
        r1 = node_tools.build_step(p, "Shape it")
        self.assertTrue(r1["ok"], r1)
        r2 = node_tools.build_step(p, "Shape it")
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r1["node_id"], r2["node_id"])
        self.assertEqual(len(p["nodes"]), 1)

class SurfaceTruthTest(unittest.TestCase):
    pass

class EdgeNameSymmetryTest(unittest.TestCase):
    def _plan(self, out_name, in_name, when=""):
        return {"summary": "s.", "nodes": [
            {"name": "Find files", "type": "code", "code_sketch": "x",
             "outputs": [{"name": out_name, "type": "record"}]},
            {"name": "Pick file", "type": "code", "code_sketch": "y",
             "inputs": [{"name": in_name, "type": "text"}],
             "outputs": [{"name": "chosen", "type": "record"}]}],
            "edges": [{"src": "Find files", "dst": "Pick file", "when": when}]}

    def test_mismatched_edge_is_an_order_edge_at_save(self):
        p = _workflow([], pid="p_sym1")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, self._plan("pdf_files", "options"))

        self.assertTrue(r.get("ok"), r)
        gaps = r.get("design_gaps") or []
        self.assertTrue(any("nothing reads" in g for g in gaps), gaps)
        self.assertNotIn("options", [v["name"] for v in p.get("variables") or []])

    def test_matching_edge_and_when_edge_pass(self):
        p = _workflow([], pid="p_sym2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, self._plan("files", "files"))
        self.assertTrue(r.get("ok"), r)
        p2 = _workflow([], pid="p_sym3")
        node_tools.tool_save_intent(p2, "Purpose.", instructions="Run it.")
        r2 = node_tools.tool_save_plan(
            p2, self._plan("pdf_files", "options", when="pdf_files != []"))
        self.assertTrue(r2.get("ok"), r2)

class AskPersistenceTest(unittest.TestCase):
    def test_folder_type_is_a_valid_port_type(self):
        self.assertIn("folder", node_tools.PORT_TYPES)
        from runtime import port_checks
        probs = port_checks.check_ports(
            [{"name": "watch_folder", "type": "folder"}],
            {"watch_folder": "/Users/x/Invoices"})
        self.assertEqual(probs, [])
        probs = port_checks.check_ports(
            [{"name": "watch_folder", "type": "folder"}], {"watch_folder": 3})
        self.assertTrue(probs)

    def test_ask_schema_carries_upload_and_folder(self):
        spec = next(s for s in node_tools._AGENT_SPECS
                    if s[0] == "ask_user")
        self.assertIn("upload", spec[3])
        self.assertIn("folder", spec[3])
        self.assertIn("folder picker", spec[2])

class IntentInstructionsTest(unittest.TestCase):
    def test_instructions_store_preserve_and_sources_retired(self):
        p = _workflow([], pid="p_instr")
        node_tools.tool_save_intent(p, "Purpose.", sources=["chat"])
        self.assertNotIn("sources", p["intent"])
        self.assertEqual(p["intent"]["instructions"], "")
        node_tools.tool_save_intent(
            p, "Purpose.",
            instructions="Press Run; your LinkedIn username is the last part of your profile URL.")
        self.assertIn("LinkedIn", p["intent"]["instructions"])
        node_tools.tool_save_intent(p, "Purpose, updated.")
        self.assertIn("LinkedIn", p["intent"]["instructions"])
        r = node_tools.tool_save_intent(p, "Purpose.", instructions="New text.")
        self.assertEqual(p["intent"]["instructions"], "New text.")
        self.assertIn("Information tab", r["note"])

class EndTurnAtAskTest(unittest.TestCase):
    def test_in_turn_answer_routes_are_gone(self):
        self.assertFalse(hasattr(node_tools, "settle_secret_answer"))
        self.assertFalse(hasattr(node_tools, "settle_chat_reply"))

    def test_new_message_settles_every_open_ask(self):
        p = _workflow([], pid="p_endturn")
        from agent import transcript
        a = transcript.append_request(p, "ask", {"question": "Which channel?",
                                                 "options": ["A", "B"]})
        b = transcript.append_request(p, "ask", {"question": "already answered"})
        transcript.append_answer(p, b["iid"], "B", shown="B")
        c = transcript.append_request(p, "ask", {"question": "And the URL?",
                                                 "secret": True})
        frozen = [dict(a), dict(b), dict(c)]
        n = node_tools.settle_open_asks(p)
        self.assertEqual(n, 2)
        self.assertEqual(transcript.open_requests(p, "ask"), [])

        self.assertEqual([dict(x) for x in _bootstrap.shown_requests(p, "ask")],
                         frozen)
        self.assertEqual(node_tools.settle_open_asks(p), 0)

class TurnOverRailTest(unittest.TestCase):
    def test_open_ask_refuses_everything(self):
        p = _workflow([], pid="p_over1")
        self.assertEqual(node_tools.turn_over_error(p, "run_cell"), "")
        turnstate.of(p).ask_open = True
        self.assertIn("OVER", node_tools.turn_over_error(p, "run_cell"))
        self.assertIn("OVER", node_tools.turn_over_error(p, "build_workflow"))
        self.assertIn("OVER", node_tools.turn_over_error(p, "Bash"))

    def test_a_plan_card_is_not_a_turn_ending(self):
        p = _workflow([], pid="p_over2")
        node_tools.append_plan_entry(p, {"summary": "Do.", "nodes": []})
        self.assertEqual(node_tools.turn_over_error(p, "run_cell"), "")
        self.assertEqual(node_tools.turn_over_error(p, "save_plan"), "")

    def test_a_landed_build_or_stashed_fix_refuses_everything(self):
        p = _workflow([], pid="p_over4")
        turnstate.of(p).built = "Built it."
        self.assertIn("OVER", node_tools.turn_over_error(p, "run_cell"))
        self.assertIn("OVER", node_tools.turn_over_error(p, "build_workflow"))
        p2 = _workflow([], pid="p_over5")
        turnstate.of(p2).pending_fix = {"ticket": "t1"}
        self.assertIn("OVER", node_tools.turn_over_error(p2, "save_plan"))

class FlushOnCardTest(unittest.TestCase):
    def test_pre_card_narration_is_its_own_message_above_the_plan_card(self):
        import turns
        from agent import actions, transcript
        p = _workflow([], pid="p_flushcard")
        p.pop("plan_approved_ts", None)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        turns.begin("p_flushcard", "chat")
        turns.record("p_flushcard", {"type": "delta", "text": "Here's my plan for this."})
        try:
            actions.execute(p, {"name": "save_plan", "input": {"plan": {
                "summary": "Does a thing.", "nodes": [
                    {"name": "Do it", "type": "code", "code_sketch": "do",
                     "outputs": [{"name": "y", "type": "number"}]}]}}},
                lambda e: None)
        finally:
            turns.finish("p_flushcard")
        items = transcript.items(p)
        card_at = next(i for i, it in enumerate(items) if it.get("request") == "blueprint")
        self.assertEqual(items[card_at - 1]["kind"], "message")
        self.assertEqual(items[card_at - 1]["from"], "assistant")
        self.assertEqual(items[card_at - 1]["text"], "Here's my plan for this.")
        self.assertNotIn("lead", items[card_at]["payload"])
        self.assertNotIn("chat", p)
        self.assertFalse(hasattr(node_tools, "_flush_narration"))

    def test_the_card_carries_only_what_it_shows(self):
        p = _workflow([], pid="p_psevent")
        p["plan"] = {"ts": 1.0, "status": "draft", "summary": "S",
                     "nodes": [{"name": "Fetch it", "type": "code",
                                "code": "NEVER-ON-THE-CARD",
                                "packages": ["requests"],
                                "domains": ["x.example"]}]}
        item = node_tools.append_plan_entry(p, p["plan"])
        self.assertEqual(item["request"], "blueprint")
        self.assertEqual(item["payload"]["summary"], "S")
        self.assertEqual(item["payload"]["packages"], ["requests"])
        self.assertNotIn("NEVER-ON-THE-CARD", str(item))

class PreviewRunTest(unittest.TestCase):
    def test_walk_names_pauses_and_problems(self):
        p = _workflow([
            {"id": "n_in", "name": "Type your message", "type": "user-input",
             "config": {}, "inputs": [],
             "outputs": [{"name": "message", "type": "text"}], "tests": []},
            {"id": "n_send", "name": "Send it", "type": "connector",
             "read_only": False, "config": {"code": "x=1"},
             "inputs": [{"name": "message", "type": "text"},
                        {"name": "lost_value", "type": "text"}],
             "outputs": [{"name": "ok", "type": "boolean"}], "tests": []}],
            pid="p_walk")
        p["edges"] = [{"src": "n_in", "dst": "n_send", "when": ""}]
        r = node_tools.tool_preview_run(p)
        walk = " | ".join(r["walk"])
        self.assertIn("asked on the RUN FORM", walk)
        self.assertIn("PAUSES for the user's approval", walk)
        self.assertIn("CANNOT RECEIVE 'lost_value'", walk)
        self.assertEqual(r["problems"], ["Send it: lost_value"])

        self.assertIn("mcp__cryogram__preview_run",
                      node_tools.AGENT_TOOL_NAMES)

class ReplyAnswersThePlanCardTest(unittest.TestCase):
    def _proposed(self, pid):
        p = _workflow([], pid=pid)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        plan = {"summary": "Do the thing every day.", "nodes": [
            {"name": "Make it", "type": "code", "code_sketch": "x",
             "outputs": [{"name": "value", "type": "text"}]}]}
        r = node_tools.tool_save_plan(p, plan)
        assert r.get("ok"), r
        node_tools.append_plan_entry(p, p["plan"])
        return p

    def test_a_message_answers_the_card_without_touching_it(self):
        p = self._proposed("p_sup1")
        card = _bootstrap.shown_requests(p, "blueprint")[-1]
        self.assertTrue(node_tools.supersede_open_plan(p))
        self.assertIn(card["iid"], node_tools._transcript.answered(p))
        self.assertNotIn("superseded", card)
        self.assertNotIn("superseded", p["plan"])

        self.assertFalse(node_tools.supersede_open_plan(p))

    def test_nothing_to_answer_without_a_card(self):
        p = _workflow([], pid="p_sup2")
        self.assertFalse(node_tools.supersede_open_plan(p))

    def test_the_built_record_is_not_a_question(self):
        p = self._proposed("p_sup3")
        node_tools.supersede_open_plan(p)
        node_tools.append_plan_entry(p, p["plan"], force_new=True,
                                      head="built")
        self.assertFalse(node_tools.supersede_open_plan(p))

    def test_a_reply_never_blocks_the_build(self):
        p = self._proposed("p_sup4")
        node_tools.tool_run_cell(p, "Make it", "write_output('value', 'v')", {})
        _user_approved(p)
        node_tools.supersede_open_plan(p)
        _bootstrap.said(p, "user", "build it")
        self.assertTrue(node_tools.tool_build_workflow(p).get("ok"))

class InstantVariableCaptureTest(unittest.TestCase):
    def test_value_stored_on_declare(self):
        p = _workflow([], pid="p_var1")
        r = node_tools.tool_declare_variables(p, [
            {"name": "manager_email", "value": "boss@firm.no"}])
        self.assertTrue(r["ok"])
        self.assertIn("manager_email", r.get("values_stored", []))
        v = next(x for x in p["variables"] if x["name"] == "manager_email")
        self.assertEqual(v["value"], "boss@firm.no")

    def test_empty_entry_filled_set_entry_never_overwritten(self):
        p = _workflow([], pid="p_var2")
        node_tools.tool_declare_variables(p, [{"name": "region"}])
        r = node_tools.tool_declare_variables(p, [
            {"name": "region", "value": "EU"}])
        self.assertIn("region", r.get("values_stored", []))
        r2 = node_tools.tool_declare_variables(p, [
            {"name": "region", "value": "US"}])
        self.assertNotIn("region", r2.get("values_stored", []))
        v = next(x for x in p["variables"] if x["name"] == "region")
        self.assertEqual(v["value"], "EU")

    def test_conflicting_value_returns_a_pointed_note(self):
        p = _workflow([], pid="p_var4")
        node_tools.tool_declare_variables(p, [
            {"name": "feed_url", "value": "https://old.example"}])
        r = node_tools.tool_declare_variables(p, [
            {"name": "feed_url", "value": "https://new.example"}])
        kept = " ".join(r.get("values_kept", []))
        self.assertIn("https://old.example", kept)
        self.assertIn("overwrite", kept)
        v = next(x for x in p["variables"] if x["name"] == "feed_url")
        self.assertEqual(v["value"], "https://old.example")

    def test_overwrite_true_replaces_after_confirmation(self):
        p = _workflow([], pid="p_var5")
        node_tools.tool_declare_variables(p, [
            {"name": "feed_url", "value": "https://old.example"}])
        r = node_tools.tool_declare_variables(p, [
            {"name": "feed_url", "value": "https://new.example",
             "overwrite": True}])
        self.assertIn("feed_url", r.get("values_stored", []))
        v = next(x for x in p["variables"] if x["name"] == "feed_url")
        self.assertEqual(v["value"], "https://new.example")

    def test_same_value_again_is_quietly_already_present(self):
        p = _workflow([], pid="p_var6")
        node_tools.tool_declare_variables(p, [
            {"name": "region", "value": "EU"}])
        r = node_tools.tool_declare_variables(p, [
            {"name": "region", "value": "EU"}])
        self.assertNotIn("values_kept", r)
        self.assertIn("region", r.get("already_present", []))

    def test_a_pasted_secret_is_stored_by_code_and_named_only(self):
        from storage import secrets_store
        p = _workflow([], pid="p_var3")
        r = node_tools.tool_declare_variables(p, [
            {"name": "hook_url_var3", "secret": True,
             "value": "hook-value-var3-9f8e7d6c"}])
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("secrets_stored"), ["hook_url_var3"])
        self.assertNotIn("hook-value-var3", json.dumps(r))
        var = p["variables"][0]
        self.assertTrue(var["secret"])
        self.assertNotIn("hook-value", str(var.get("value")))
        self.assertEqual(secrets_store.get_secret("hook_url_var3", secrets_store.workflow_owner("p_var3")),
                         "hook-value-var3-9f8e7d6c")
        secrets_store.delete_secret("hook_url_var3", secrets_store.workflow_owner("p_var3"))

class StaleDeliverableAutoHealTest(unittest.TestCase):
    def _step(self, outputs):
        return {"name": "Make report", "type": "code", "code_sketch": "builds",
                "inputs": [],
                "outputs": [{"name": o, "type": "list"} for o in outputs],
                "tests": []}

    def test_single_output_rename_retargets_the_deliverable(self):
        p = _workflow([], pid="p_deliv1")
        p["deliverables"] = [{"node": "Make report", "port": "report",
                              "label": "Report"}]
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {
            "summary": "Build the shortlist.",
            "nodes": [self._step(["shortlist"])], "edges": []})
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("deliverables_retargeted"), r)
        self.assertEqual(p["deliverables"][0]["port"], "shortlist")
        self.assertFalse(any("saved result" in g
                             for g in r.get("design_gaps", [])), r)

    def test_ambiguous_rename_stays_a_design_gap(self):
        p = _workflow([], pid="p_deliv2")
        p["deliverables"] = [{"node": "Make report", "port": "report",
                              "label": "Report"}]
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {
            "summary": "Build the shortlist.",
            "nodes": [self._step(["shortlist", "count"])], "edges": []})
        self.assertNotIn("deliverables_retargeted", r)
        self.assertEqual(p["deliverables"][0]["port"], "report")
        self.assertTrue(any("saved result" in g
                            for g in r.get("design_gaps", [])), r)

class UnsetSecretWarningTest(unittest.TestCase):
    def test_unset_secret_named_on_the_card(self):
        p = _workflow([], pid="p_secwarn1")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {
            "summary": "Post the digest.", "nodes": [
                {"name": "Post it", "type": "connector",
                 "external_impact": "posts a message",
                 "code": "def post(m):\n"
                         "    url = get_secret('pa_hook_never_set')\n"
                         "    return {'posted': True}\n",
                 "inputs": [{"name": "m", "type": "text"}],
                 "outputs": [{"name": "posted", "type": "boolean"}]}]})
        self.assertTrue(r.get("ok"), r)
        warns = " ".join((p.get("plan") or {}).get("warnings") or [])
        self.assertIn("pa_hook_never_set", warns)

    def test_stored_secret_does_not_warn(self):
        from storage import secrets_store
        secrets_store.set_secret("hook_all_set", "https://x.example/h", secrets_store.workflow_owner("p_secwarn2"))
        p = _workflow([], pid="p_secwarn2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {
            "summary": "Post the digest.", "nodes": [
                {"name": "Post it", "type": "connector",
                 "external_impact": "posts a message",
                 "code": "def post(m):\n"
                         "    url = get_secret('hook_all_set')\n"
                         "    return {'posted': True}\n",
                 "inputs": [{"name": "m", "type": "text"}],
                 "outputs": [{"name": "posted", "type": "boolean"}]}]})
        self.assertTrue(r.get("ok"), r)
        warns = " ".join((p.get("plan") or {}).get("warnings") or [])
        self.assertNotIn("hook_all_set", warns)

class PlanContinuityRailTest(unittest.TestCase):
    def _save(self, p, names, removed=None):
        plan = {"summary": "Do the thing.", "nodes": [
            {"name": n, "type": "code", "code": "write_output('y', 1)",
             "outputs": [{"name": "y", "type": "text"}]} for n in names]}
        if removed:
            plan["changes"] = {"removed": removed}
        return node_tools.tool_save_plan(p, plan)

    def test_a_dropped_step_is_carried_forward_unless_replaced(self):
        p = _workflow([], pid="p_rail1")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        self.assertTrue(self._save(p, ["Fetch", "Triage", "Post"]).get("ok"))
        r = self._save(p, ["Fetch", "Post"])
        self.assertTrue(r.get("ok"), r)
        self.assertEqual([n["name"] for n in p["plan"]["nodes"]],
                         ["Fetch", "Triage", "Post"])
        plan = {"summary": "Do.", "replace": True, "nodes": [
            {"name": n, "type": "code", "code": "write_output('y', 1)",
             "outputs": [{"name": "y", "type": "text"}]} for n in ["Fetch", "Post"]]}
        r2 = node_tools.tool_save_plan(p, plan)
        self.assertFalse(r2.get("ok"))
        self.assertTrue(any("Triage" in e and "vanished" in e
                            for e in r2.get("errors", [])), r2)
        self.assertEqual([n["name"] for n in p["plan"]["nodes"]],
                         ["Fetch", "Triage", "Post"])

    def test_removed_list_passes(self):
        p = _workflow([], pid="p_rail2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        self.assertTrue(self._save(p, ["Fetch", "Triage"]).get("ok"))
        r = self._save(p, ["Fetch"], removed=["Triage"])
        self.assertTrue(r.get("ok"), r)

    def test_changes_only_plan_is_exempt(self):
        p = _workflow([], pid="p_rail3")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        self.assertTrue(self._save(p, ["Fetch", "Triage"]).get("ok"))
        r = node_tools.tool_save_plan(p, {"summary": "Fix it.",
                                          "changes": {"modified": ["Fetch"]}})
        self.assertTrue(r.get("ok"), r)

    def test_discarded_previous_plan_is_exempt(self):
        p = _workflow([], pid="p_rail4")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        self.assertTrue(self._save(p, ["Fetch", "Triage"]).get("ok"))
        p["plan"]["status"] = "discarded"
        r = self._save(p, ["Something Else"])
        self.assertTrue(r.get("ok"), r)

    def test_names_compare_humanised(self):
        p = _workflow([], pid="p_rail5")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        self.assertTrue(self._save(p, ["Fetch rate"]).get("ok"))
        r = self._save(p, ["fetch_rate"])
        self.assertTrue(r.get("ok"), r)

class StepIdentityByIdTest(unittest.TestCase):
    CODE = "write_output('y', read_input('x').upper())"

    def _io(self):
        return {"inputs": [{"name": "x", "type": "text"}],
                "outputs": [{"name": "y", "type": "text"}]}

    def _built(self, pid):
        from agent import cells
        p = _workflow([{"id": "n_id1", "name": "Get data from London",
                       "type": "code", "config": {"code": self.CODE},
                       "tests": [{"name": "recorded run",
                                  "inputs": {"x": "a"}, "expect": "ok",
                                  "asserts": []}], **self._io()}], pid=pid)
        cells.record(pid, "Get data from London", self.CODE,
                     {"x": "a"}, {"y": "A"}, True, 0.1, [])
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {"summary": "Do.",
                                          "change_note": {"found": "F.", "will_change": "W."},
                                          "nodes": [
            {"name": "Get data from London", "type": "code", **self._io()}]})
        assert r.get("ok"), r
        return p

    def test_rename_by_id_keeps_proof_and_node(self):
        from agent import cells
        p = self._built("p_id1")
        r = node_tools.tool_save_plan(p, {"summary": "Do.",
                                          "change_note": {"found": "F.", "will_change": "W."},
                                          "nodes": [
            {"id": "n_id1", "name": "Get data from location",
             "type": "code", **self._io()}]})
        self.assertTrue(r.get("ok"), r)
        rec = cells.latest_ok("p_id1", "Get data from location", kind="code",
                              step_id="n_id1")
        self.assertIsNotNone(rec)
        self.assertEqual(node_tools.untested_steps(p, p["plan"]["nodes"]), [])
        fr = node_tools.build_step(p, "Get data from location")
        self.assertTrue(fr["ok"], fr)
        self.assertEqual(fr["node_id"], "n_id1")
        n = node_tools.tool_read_node(p, "n_id1")
        self.assertEqual(n["name"], "Get data from location")
        self.assertEqual(len(n["tests"]), 1)

    def test_rename_carries_the_rest_and_a_replace_drop_is_named(self):
        p = self._built("p_id2")
        r0 = node_tools.tool_save_plan(p, {"summary": "Do.",
                                           "change_note": {"found": "F.", "will_change": "W."},
                                           "nodes": [
            {"name": "Get data from London", "type": "code", **self._io()},
            {"name": "Summarise", "type": "code", "code_sketch": "sums",
             "outputs": [{"name": "s", "type": "text"}]}]})
        self.assertTrue(r0.get("ok"), r0)
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"id": "n_id1", "name": "Get data from location",
             "type": "code", **self._io()}]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual([n["name"] for n in p["plan"]["nodes"]],
                         ["Get data from location", "Summarise"])
        r2 = node_tools.tool_save_plan(p, {"summary": "Do.", "replace": True,
                                           "nodes": [
            {"id": "n_id1", "name": "Get data from location",
             "type": "code", **self._io()}]})
        self.assertFalse(r2.get("ok"))
        errs = " ".join(r2.get("errors", []))
        self.assertIn("Summarise", errs)
        self.assertNotIn("London", errs)

    def test_prebuild_changes_renamed_migrates_recordings(self):
        from agent import cells
        p = _workflow([], pid="p_id3")
        cells.record("p_id3", "Fetch London", self.CODE,
                     {"x": "a"}, {"y": "A"}, True, 0.1, [])
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r0 = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Fetch London", "type": "code", **self._io()}]})
        self.assertTrue(r0.get("ok"), r0)
        r = node_tools.tool_save_plan(p, {
            "summary": "Do.",
            "nodes": [{"name": "Fetch city data", "type": "code",
                       **self._io()}],
            "changes": {"renamed": [{"from": "Fetch London",
                                     "to": "Fetch city data"}]}})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual([n["name"] for n in p["plan"]["nodes"]], ["Fetch city data"])
        rec = cells.latest_ok_for("p_id3", p["plan"]["nodes"][0], kind="code")
        self.assertIsNotNone(rec)

        self.assertEqual(node_tools.untested_steps(p, p["plan"]["nodes"]), [])

class StickyCodeStalenessTest(unittest.TestCase):
    def test_code_key_ignores_cosmetics_keeps_indentation(self):
        a = "def f():\n    return 1\n"
        b = "\ndef f():  \n    return 1\n\n"
        c = "def f():\n        return 1\n"
        self.assertEqual(node_tools.code_key(a), node_tools.code_key(b))
        self.assertNotEqual(node_tools.code_key(a), node_tools.code_key(c))

    def test_trailing_whitespace_is_not_stale(self):
        from agent import cells
        p = _workflow([], pid="p_stale1")
        cells.record("p_stale1", "Extract", "write_output('y', 1)",
                     {}, {"y": 1}, True, 0.1, [])
        nodes = [{"name": "Extract", "type": "code",
                  "code": "write_output('y', 1)  \n",
                  "outputs": [{"name": "y", "type": "number"}]}]
        self.assertEqual(node_tools.untested_steps(p, nodes), [])

    def test_real_change_is_stale_and_named_in_discarded_proofs(self):
        from agent import cells
        p = _workflow([], pid="p_stale2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        cells.record("p_stale2", "Extract", "write_output('y', 1)",
                     {}, {"y": 1}, True, 0.1, [])
        nodes = [{"name": "Extract", "type": "code",
                  "code": "write_output('y', 2)",
                  "outputs": [{"name": "y", "type": "number"}]}]
        self.assertTrue(any("code changed" in str(u)
                            for u in node_tools.untested_steps(p, nodes)))
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": nodes})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("discarded_tests"), ["Extract"])
        self.assertIn("OMITTED", r.get("discarded_tests_note", ""))

    def test_a_built_step_is_finished_however_it_got_there(self):
        from agent import orchestrator
        p = _workflow([{"id": "n1", "name": "Extract", "type": "code",
                       "config": {"code": "write_output('y', 1)   \n"},
                       "inputs": [], "outputs": [], "tests": []}],
                     pid="p_stale3")
        p["plan"] = {"status": "built", "nodes": [{"name": "Extract",
                                                   "type": "code"}]}
        self.assertEqual(node_tools.step_states(p), {"Extract": "finished"})
        body = orchestrator._workflow_context(p)
        self.assertNotIn("proven", body)
        self.assertNotIn("untested", body)

class IntentFactsMergeTest(unittest.TestCase):
    def test_facts_merge_and_dedupe(self):
        p = _workflow([], pid="p_intent1")
        node_tools.tool_save_intent(p, "Purpose.",
                                    facts=["Sheet: https://x.example/s1"])
        node_tools.tool_save_intent(p, "Purpose, refined.",
                                    facts=["Cap: 20 posts per run",
                                           "sheet:   https://x.example/s1"])
        facts = p["intent"]["facts"]
        self.assertEqual(facts, ["Sheet: https://x.example/s1",
                                 "Cap: 20 posts per run"])

    def test_remove_facts_is_the_only_way_out(self):
        p = _workflow([], pid="p_intent2")
        node_tools.tool_save_intent(p, "Purpose.", facts=["A fact", "Old fact"])
        node_tools.tool_save_intent(p, "Purpose.", facts=[])
        self.assertEqual(p["intent"]["facts"], ["A fact", "Old fact"])
        node_tools.tool_save_intent(p, "Purpose.", remove_facts=["old fact"])
        self.assertEqual(p["intent"]["facts"], ["A fact"])

class RecordedDuplicatesTest(unittest.TestCase):
    def _proven(self, pid, name="Extract text"):
        p = _workflow([], pid=pid)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": name, "type": "code", "code_sketch": "reads it",
             "inputs": [{"name": "x", "type": "text"}],
             "outputs": [{"name": "y", "type": "text"}]}]})
        node_tools.tool_run_cell(
            p, name, "write_output('y', read_input('x').upper())", {"x": "abc"})
        return p

    def test_retyped_code_and_recorded_test_are_carried_not_stored(self):
        p = self._proven("p_dup1")
        code = "write_output('y', read_input('x').upper())"
        sp = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Extract text", "type": "code", "code": code,
             "tests": [{"name": "recorded run", "expect": "ok",
                        "inputs": {"x": "abc"}}],
             "inputs": [{"name": "x", "type": "text"}],
             "outputs": [{"name": "y", "type": "text"}]}]})
        self.assertTrue(sp["ok"], sp)
        self.assertEqual(sp.get("carried_from_recordings"), ["Extract text"])
        pn = p["plan"]["nodes"][0]

        self.assertIn("write_output('y'", pn["code"])
        self.assertEqual(pn["tests"][0]["inputs"], {"x": "abc"})

        self.assertEqual(node_tools.untested_steps(p, p["plan"]["nodes"]), [])

    def test_a_real_code_change_is_never_dropped(self):
        p = self._proven("p_dup2")
        sp = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Extract text", "type": "code",
             "code": "write_output('y', read_input('x').lower())",
             "inputs": [{"name": "x", "type": "text"}],
             "outputs": [{"name": "y", "type": "text"}]}]})
        self.assertTrue(sp["ok"], sp)
        self.assertNotIn("Extract text",
                         sp.get("carried_from_recordings") or [])
        self.assertIn("lower()", p["plan"]["nodes"][0]["code"])

        self.assertTrue(sp.get("discarded_tests"))

    def test_an_extra_authored_test_stands(self):
        p = self._proven("p_dup3")
        sp = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Extract text", "type": "code",
             "tests": [{"name": "empty input", "expect": "ok",
                        "inputs": {"x": ""}}],
             "inputs": [{"name": "x", "type": "text"}],
             "outputs": [{"name": "y", "type": "text"}]}]})
        self.assertTrue(sp["ok"], sp)
        self.assertNotIn("Extract text",
                         sp.get("carried_from_recordings") or [])
        self.assertEqual(p["plan"]["nodes"][0]["tests"][0]["name"],
                         "empty input")

    def test_cosmetic_retype_of_code_still_counts_as_duplicate(self):
        p = self._proven("p_dup4")
        sp = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Extract text", "type": "code",
             "code": "\nwrite_output('y', read_input('x').upper())   \n",
             "inputs": [{"name": "x", "type": "text"}],
             "outputs": [{"name": "y", "type": "text"}]}]})
        self.assertEqual(sp.get("carried_from_recordings"), ["Extract text"])

class CheapLookupTest(unittest.TestCase):
    def _built(self):
        p = _workflow([
            {"id": "n_a", "name": "Fetch", "type": "code",
             "config": {"code": "write_output('y', 1)"},
             "outputs": [{"name": "y", "type": "number"}],
             "status": "active", "provenance": "native",
             "tests": [{"name": "recorded run", "expect": "ok",
                        "inputs": {"page": "x" * 5000}}]},
            {"id": "n_b", "name": "Shape", "type": "code",
             "config": {"code": "write_output('z', 2)"},
             "outputs": [{"name": "z", "type": "number"}]}],
            pid="p_read1")
        return p

    def test_read_node_batches(self):
        p = self._built()
        out = node_tools.tool_read_node(p, node_ids=["Fetch", "Shape"])
        self.assertTrue(out["ok"])
        self.assertEqual([n["name"] for n in out["nodes"]], ["Fetch", "Shape"])

    def test_fixture_values_are_summarised_not_dumped(self):
        p = self._built()
        out = node_tools.tool_read_node(p, "Fetch")
        self.assertEqual(len(out["tests"]), 1)
        self.assertEqual(out["tests"][0]["input_names"], ["page"])
        self.assertNotIn("x" * 100, __import__("json").dumps(out))

        for dead in node_tools._READ_NODE_DROP:
            self.assertNotIn(dead, out)

    def test_recorded_chain_error_names_what_is_recorded(self):
        p = _workflow([], pid="p_read2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Fetch", "type": "code", "code_sketch": "gets it",
             "outputs": [{"name": "pages", "type": "list"}]},
            {"name": "Parse", "type": "code", "code_sketch": "parses it",
             "outputs": [{"name": "rows", "type": "list"}]}]})
        node_tools.tool_run_cell(p, "Fetch", "write_output('pages', [1])", {})
        r = node_tools.tool_run_cell(p, "Parse", "write_output('rows', [1])",
                                     {"raw_pages": "$recorded"})
        self.assertIn("'pages'", r["error"])

    def test_missing_sample_names_the_ones_that_exist(self):
        p = _workflow([], pid="p_read3")
        p["samples"] = [{"name": "criteria.txt", "ref": "blob:" + "a" * 64}]
        from storage import store as _st
        _st.save(p)
        r = node_tools.tool_read_sample(p, "guessed-name.txt")
        self.assertIn("criteria.txt", r["error"])
        self.assertNotIn("list_samples", r["error"])

class ReadNodeWholeCodeTest(unittest.TestCase):
    def test_long_code_ships_whole(self):
        code = ("url = read_input('url')\n" + "x = 1\n" * 400
                + "write_output('raw', url)\n")
        p = _workflow([{"id": "n_big", "name": "Fetch", "type": "code",
                       "config": {"code": code},
                       "inputs": [{"name": "url", "type": "text"}],
                       "outputs": [{"name": "raw", "type": "list"}]}],
                     pid="p_summary")
        out = node_tools.tool_read_node(p, "Fetch")
        self.assertEqual(out["config"]["code"], code)

class FixCardSaysWhyAndWhatTest(unittest.TestCase):
    def _built(self, pid):
        p = _workflow([{"id": "n_send", "name": "Send requests", "type": "connector",
                       "read_only": False, "config": {"code": "x"}, "inputs": [],
                       "outputs": [{"name": "sent", "type": "list"}], "tests": []},
                      {"id": "n_read", "name": "Read table", "type": "connector",
                       "read_only": True, "config": {"code": "y"}, "inputs": [],
                       "outputs": [{"name": "rows", "type": "list"}], "tests": []}],
                     pid=pid)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        p["plan"] = {"status": "built", "summary": "S", "nodes": [
            {"id": "n_send", "name": "Send requests", "type": "connector",
             "read_only": False, "outputs": [{"name": "sent", "type": "list"}]},
            {"id": "n_read", "name": "Read table", "type": "connector",
             "read_only": True, "outputs": [{"name": "rows", "type": "list"}]}]}
        p["plan_approved_ts"] = 1.0
        turnstate.of(p).fix_issue_id = "tkt_0fa11e"
        p["tickets"] = [{"id": "tkt_0fa11e", "status": "in-progress", "node_id": "n_send",
                         "run_id": "r", "reason": "no-effect", "verdict": {}, "notes": ""}]
        return p

    def _fix_plan(self, note=True):
        plan = {"summary": "S", "nodes": [
            {"id": "n_send", "name": "Send requests", "type": "connector",
             "read_only": False, "external_impact": "sends", "code_sketch": "send",
             "outputs": [{"name": "sent", "type": "list"},
                         {"name": "outcomes", "type": "list"}]},
            {"id": "n_read", "name": "Read table", "type": "connector",
             "read_only": True, "external_impact": "reads", "code_sketch": "read",
             "outputs": [{"name": "rows", "type": "list"}]}],
            "changes": {"modified": ["n_send"]}}
        if note:
            plan["fix_note"] = {"went_wrong": "It reached none of the 8 people and said nothing about why.",
                                "will_change": "The sending step will report what happened to each person."}
        return plan

    def test_a_fix_save_without_the_two_sentences_is_refused(self):
        p = self._built("p_fixnote1")
        r = node_tools.tool_save_plan(p, self._fix_plan(note=False))
        self.assertFalse(r["ok"])
        self.assertTrue(any("fix_note" in e for e in r["errors"]))

    def test_the_sentences_ride_inside_the_card_payload(self):
        import turns
        from agent import actions, transcript
        p = self._built("p_fixnote2")
        turns.begin(p["id"], "chat")
        try:
            env = actions.execute(p, {"name": "save_plan", "input": {"plan": self._fix_plan()}},
                                  lambda e: None)
        finally:
            turns.finish(p["id"])
        items = transcript.items(p)
        card_at = next(i for i, it in enumerate(items) if it.get("request") == "blueprint")
        payload = items[card_at]["payload"]
        self.assertEqual(payload["changes"]["modified"], ["Send requests"])
        self.assertEqual(payload["fix_note"]["will_change"],
                         "The sending step will report what happened to each person.")
        self.assertFalse([it for it in items if it.get("kind") == "message"
                          and "What went wrong:" in str(it.get("text"))])

class AnOutputNothingReadsIsAResultTest(unittest.TestCase):
    def test_unread_outputs_are_kept_and_a_send_report_is_not(self):
        from agent import orchestrator
        p = _workflow([
            {"id": "n_f", "name": "Filter", "type": "code", "config": {"code": "x"},
             "inputs": [], "tests": [],
             "outputs": [{"name": "rows", "type": "list"}, {"name": "count", "type": "number"},
                         {"name": "repeat_merchants", "type": "list"},
                         {"name": "token", "type": "secret"}]},
            {"id": "n_s", "name": "Send", "type": "connector", "read_only": False,
             "config": {"code": "y"}, "inputs": [{"name": "rows", "type": "list"}],
             "tests": [], "outputs": [{"name": "status", "type": "number"}]}],
            pid="p_unread_kept")
        p["edges"] = [{"src": "n_f", "dst": "n_s", "when": "count > 0"}]
        p["deliverables"] = [{"node": "n_f", "port": "count", "label": "Count"}]
        added = orchestrator._keep_unread_outputs(p, lambda e: None)
        self.assertEqual(added, ["repeat_merchants"])
        self.assertEqual([d["port"] for d in p["deliverables"]], ["count", "repeat_merchants"])
        self.assertEqual(orchestrator._keep_unread_outputs(p, lambda e: None), [])

class ApprovedStepsAreTheContractTest(unittest.TestCase):
    def test_changes_match_by_type_in_order_and_names_play_no_part(self):
        from agent import steps
        a = [{"name": "Upload", "type": "user-input"}, {"name": "Filter", "type": "code"}]
        renamed = [{"name": "Upload file", "type": "user-input"}, {"name": "Filter rows", "type": "code"}]
        self.assertEqual(steps.plan_step_changes(a, renamed), {"removed": [], "added": []})
        inserted = steps.plan_step_changes(a, [a[0], {"name": "Fetch", "type": "connector"}, a[1]])
        self.assertEqual(inserted["removed"], [])
        self.assertEqual([x["name"] for x in inserted["added"]], ["Fetch"])
        dropped = steps.plan_step_changes(a, [a[0]])
        self.assertEqual([x["name"] for x in dropped["removed"]], ["Filter"])
        retyped = steps.plan_step_changes(a, [a[0], {"name": "Filter", "type": "ai"}])
        self.assertEqual(([x["type"] for x in retyped["removed"]], [x["type"] for x in retyped["added"]]),
                         (["code"], ["ai"]))

    def test_a_changed_plan_shows_the_two_lists_and_holds_the_build(self):
        from agent import steps
        p = _workflow([], pid="p_approved_steps")
        p["plan"] = {"summary": "Do.", "status": "draft", "nodes": [
            {"id": "s1", "name": "Upload", "type": "user-input"},
            {"id": "s2", "name": "Filter", "type": "code"}]}
        p["plan_approved_ts"] = 1.0
        p["approved_steps"] = steps.approved_steps_of(p["plan"])
        self.assertFalse(steps.plan_steps_need_approval(p))
        p["plan"]["nodes"][1]["name"] = "Filter rows"
        self.assertFalse(steps.plan_steps_need_approval(p))
        p["plan"]["nodes"].pop()
        self.assertTrue(steps.plan_steps_need_approval(p))
        card = steps.opening_card_payload(p, p["plan"], "plan")
        self.assertTrue(card["plan_changed"])
        self.assertEqual([x["name"] for x in card["plan_removed"]], ["Filter"])
        self.assertNotIn("plan_added", card)
        self.assertIn("steps the user approved", node_tools.tool_build_workflow(p)["error"])

    def test_a_removal_matches_by_id_and_never_a_step_the_save_lists(self):
        from agent import steps
        prev = {"nodes": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]}
        gone = steps.merge_amend(prev, {"nodes": [], "changes": {"removed": ["b"]}})
        self.assertEqual([n["name"] for n in gone["nodes"]], ["A"])
        back = steps.merge_amend(gone, {"nodes": [{"id": "b", "name": "B again"}]})
        self.assertEqual([n["name"] for n in back["nodes"]], ["A", "B again"])
        self.assertNotIn("removed", back.get("changes") or {})
        both = steps.merge_amend(prev, {"nodes": [{"id": "b", "name": "B2"}],
                                        "changes": {"removed": ["b"]}})
        self.assertEqual([n["name"] for n in both["nodes"]], ["A", "B2"])

    def test_the_plan_argument_names_the_five_step_types(self):
        from agent import actions, steps
        schema = next(s for s in actions.schemas() if s["name"] == "save_plan")["input_schema"]
        item = schema["properties"]["plan"]["properties"]["nodes"]["items"]
        self.assertEqual(set(item["properties"]["type"]["enum"]), steps.PORT_TYPE_NAMES)

        self.assertIn("REQUIRED", schema["properties"]["plan"]["properties"]["edges"]["description"])

class ChangeDeclarationIsTheScopeTest(unittest.TestCase):
    def _built(self, pid):
        read = {"id": "n_read", "name": "Read table", "type": "connector", "read_only": True,
                "description": "Reads the rows.", "external_impact": "reads a table",
                "config": {"code": "write_output('rows', [{'a': 1}])", "domains": ["example.com"]}, "inputs": [],
                "outputs": [{"name": "rows", "type": "list"}], "tests": []}
        send = {"id": "n_send", "name": "Send requests", "type": "connector", "read_only": False,
                "description": "Sends each row.", "external_impact": "sends requests",
                "config": {"code": "rows = read_input('rows')\nwrite_output('sent', rows)", "domains": ["example.com"]},
                "inputs": [{"name": "rows", "type": "list"}],
                "outputs": [{"name": "sent", "type": "list"}], "tests": []}
        p = _workflow([read, send], pid=pid)
        p["edges"] = [{"src": "n_read", "dst": "n_send"}]
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        def plan_step(n):
            return {k: n[k] for k in ("id", "name", "type", "read_only", "description",
                                     "external_impact", "inputs", "outputs")} | {
                "code": n["config"]["code"], "domains": n["config"]["domains"]}
        p["plan"] = {"status": "built", "summary": "S", "built_ts": 1.0,
                     "nodes": [plan_step(read), plan_step(send)],
                     "edges": [{"src": "n_read", "dst": "n_send"}]}
        p["plan_approved_ts"] = 1.0
        p["tickets"] = []
        turnstate.of(p).fix_issue_id = None
        return p

    def _declare(self, names, note=True):
        plan = {"nodes": [{"id": "n_send", "name": "Send requests"}],
                "changes": {"modified": list(names)}}
        if note:
            plan["change_note"] = {"found": "Two of eight were skipped.",
                                   "will_change": "The sending step will report each person."}
        return plan

    def _approve(self, p):
        from agent import steps
        import time as _t
        turnstate.of(p).take("opening_card_show_now")
        p["plan"]["change_approved_ts"] = _t.time()
        p["plan"]["approved_changes"] = sorted(steps.declared_change_names(p, p["plan"]))
        from agent import steps as _s
        _s.save(p)

    def test_a_note_with_nothing_declared_says_how_to_declare(self):
        p = self._built("p_decl1")
        r = node_tools.tool_save_plan(p, {"nodes": [{"id": "n_send", "name": "Send requests"}],
                                          "change_note": {"found": "F.", "will_change": "W."}})
        self.assertTrue(r["ok"], r)
        self.assertIn("nothing is declared", r["note"])
        self.assertNotIn("Every step is ready", r["note"])
        self.assertFalse(turnstate.of(p).opening_card_show_now)

    def test_a_fix_note_outside_a_fix_is_the_changes_note(self):
        p = self._built("p_decl2")
        plan = self._declare(["Send requests"], note=False)
        plan["fix_note"] = {"went_wrong": "It skipped two.", "will_change": "It will report each."}
        r = node_tools.tool_save_plan(p, plan)
        self.assertTrue(r["ok"], r)
        self.assertIn("read as change_note", r["note"])
        self.assertEqual(p["plan"]["change_note"]["found"], "It skipped two.")
        self.assertTrue(turnstate.of(p).opening_card_show_now)

    def test_a_declaration_with_no_code_shows_the_card(self):
        from agent import steps
        p = self._built("p_decl3")
        r = node_tools.tool_save_plan(p, self._declare(["Send requests"]))
        self.assertTrue(r["ok"], r)
        self.assertTrue(turnstate.of(p).opening_card_show_now)
        self.assertEqual(steps.declared_change_names(p, p["plan"]), {"Send requests"})

        self.assertIn("waiting for the user's click", steps.change_scope_refusal(p, "Send requests"))

    def test_nothing_declared_means_declare_first(self):
        from agent import steps
        p = self._built("p_decl4")
        why = steps.change_scope_refusal(p, "Send requests")
        self.assertIn("Declare it first", why)
        self.assertIn("changes {modified", why)
        self.assertIn("what the step asks the model",
                      steps.change_scope_refusal(p, "Send requests", what="what the step asks the model"))

    def test_the_click_approves_the_declared_steps_and_a_step_outside_is_refused(self):
        from agent import steps
        p = self._built("p_decl5")
        node_tools.tool_save_plan(p, self._declare(["Send requests"]))
        self._approve(p)
        self.assertEqual(p["plan"]["approved_changes"], ["Send requests"])
        self.assertIsNone(steps.change_scope_refusal(p, "Send requests"))
        why = steps.change_scope_refusal(p, "Read table")
        self.assertIn('"Read table" is not among them', why)
        self.assertIn('"Send requests"', why)

    def test_widening_the_declaration_asks_again_and_the_approval_carries(self):
        from agent import steps
        p = self._built("p_decl6")
        node_tools.tool_save_plan(p, self._declare(["Send requests"]))
        self._approve(p)
        r = node_tools.tool_save_plan(p, {"nodes": [{"id": "n_read", "name": "Read table"}],
                                          "changes": {"modified": ["Read table"]}})
        self.assertTrue(r["ok"], r)
        self.assertTrue(turnstate.of(p).opening_card_show_now)
        self.assertEqual(p["plan"]["approved_changes"], ["Send requests"])
        self.assertEqual(steps.change_widened(p, p["plan"]), {"Read table"})

    def test_an_approved_change_whose_step_is_untouched_is_worked_not_built(self):
        p = self._built("p_decl7")
        node_tools.tool_save_plan(p, self._declare(["Send requests"]))
        self._approve(p)
        r = node_tools.tool_save_plan(p, {"nodes": [{"id": "n_send", "name": "Send requests",
                                                     "description": "Sends each row, and says so."}]})
        self.assertTrue(r["ok"], r)
        self.assertIn("work the declared steps now", r["note"])
        self.assertIn('"Send requests"', r["note"])
        self.assertNotIn("Every step is ready", r["note"])
        self.assertFalse(turnstate.of(p).opening_card_show_now)

class ChangeCardSaysWhatWasFoundTest(unittest.TestCase):
    _built = FixCardSaysWhyAndWhatTest._built
    _fix_plan = FixCardSaysWhyAndWhatTest._fix_plan

    def _change(self, pid):
        p = self._built(pid)
        turnstate.of(p).fix_issue_id = None
        p["tickets"] = []
        return p

    def test_a_change_save_without_its_note_is_refused(self):
        p = self._change("p_chnote1")
        r = node_tools.tool_save_plan(p, self._fix_plan(note=False))
        self.assertFalse(r["ok"])
        self.assertTrue(any("change_note" in e for e in r["errors"]))

    def test_the_note_rides_the_card_and_carries_across_revisions(self):
        from agent import steps
        p = self._change("p_chnote2")
        plan = self._fix_plan(note=False)
        plan["change_note"] = {"found": "Only two threads were read.",
                               "will_change": "The fetch will read up to the limit you set."}
        r = node_tools.tool_save_plan(p, plan)
        self.assertTrue(r["ok"], r)
        card = steps.opening_card_payload(p, p["plan"], "plan")
        self.assertEqual(card["change_note"]["found"], "Only two threads were read.")
        r = node_tools.tool_save_plan(p, {"nodes": [{"id": "n_send", "description": "Sends."}]})
        self.assertTrue(r["ok"], r)
        self.assertEqual(p["plan"]["change_note"]["will_change"],
                         "The fetch will read up to the limit you set.")

    def test_a_revision_works_its_change_list_out_fresh(self):
        from agent import steps
        prev = {"nodes": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
                "changes": {"modified": ["A"], "removed": ["Gone", "B"]}}
        merged = steps.merge_amend(prev, {"nodes": [{"id": "b", "name": "B"}]})
        self.assertNotIn("modified", merged.get("changes") or {})
        self.assertEqual(merged["changes"]["removed"], ["Gone"])
        self.assertNotIn("changes", steps.merge_amend({"nodes": [], "changes": {"modified": ["A"]}},
                                                       {"nodes": []}))

    def test_the_built_card_carries_the_done_lines_warning_and_continue(self):
        from agent import steps
        p = self._change("p_chnote3")
        plan = dict(p["plan"], _done_warnings=["No AI model is ready."],
                    _continue={"ticket_id": "tkt_1", "run_id": "run_1"})
        card = steps.opening_card_payload(p, plan, "built")
        self.assertEqual(card["done"], steps.DONE_LINES)
        self.assertIn("No AI model is ready.", card["warnings"])
        self.assertEqual(card["continue"]["run_id"], "run_1")
        opening = steps.opening_card_payload(p, plan, "plan")
        self.assertNotIn("done", opening)
        self.assertNotIn("continue", opening)

class FixStaysAFixTest(unittest.TestCase):
    def _after_typed_revision(self, pid):
        t = FixCardSaysWhyAndWhatTest()
        p = t._built(pid)
        r = node_tools.tool_save_plan(p, t._fix_plan())
        self.assertTrue(r["ok"], r)
        turnstate.of(p).take("opening_card_show_now")
        turnstate.of(p).take("fix_issue_id")
        return p, t

    def test_the_one_reader(self):
        p, _ = self._after_typed_revision("p_fixstay0")
        self.assertEqual(node_tools.fix_in_progress(p), "tkt_0fa11e")
        p["tickets"][0]["status"] = "closed"
        self.assertEqual(node_tools.fix_in_progress(p), "")
        turnstate.of(p).fix_issue_id = "tkt_other1"
        self.assertEqual(node_tools.fix_in_progress(p), "tkt_other1")

    def test_a_later_save_still_raises_the_card_and_carries_the_note(self):
        p, t = self._after_typed_revision("p_fixstay1")
        plan = t._fix_plan(note=False)
        r = node_tools.tool_save_plan(p, plan)
        self.assertTrue(r["ok"], r)
        self.assertEqual(p["plan"]["ticket_id"], "tkt_0fa11e")
        self.assertTrue(turnstate.of(p).opening_card_show_now)
        self.assertEqual(p["plan"]["fix_note"]["went_wrong"],
                         "It reached none of the 8 people and said nothing about why.")

        plan = t._fix_plan(note=False)
        plan["fix_note"] = {"will_change": "It will write the time as it sends."}
        node_tools.tool_save_plan(p, plan)
        self.assertEqual(p["plan"]["fix_note"]["will_change"],
                         "It will write the time as it sends.")
        self.assertIn("reached none", p["plan"]["fix_note"]["went_wrong"])

    def test_the_card_stamps_per_fix_without_the_marker(self):
        import turns
        from agent import actions, transcript
        p, t = self._after_typed_revision("p_fixstay2")
        turns.begin(p["id"], "chat")
        try:
            env = actions.execute(p, {"name": "save_plan",
                                      "input": {"plan": t._fix_plan(note=False)}},
                                  lambda e: None)
        finally:
            turns.finish(p["id"])
        card = next(it for it in transcript.items(p) if it.get("request") == "blueprint")
        self.assertEqual(card["payload"].get("fix"), "tkt_0fa11e")
        self.assertIn("Looks right", card["payload"]["options"][0])

    def test_resolve_issue_names_the_fix_under_way(self):
        p, _ = self._after_typed_revision("p_fixstay3")
        r = node_tools.tool_resolve_issue(p)
        self.assertIn("already being fixed", r["error"])
        self.assertIsNone(turnstate.of(p).pending_fix)

    def test_the_chat_seam_marks_a_continuation(self):
        from agent import orchestrator
        p, _ = self._after_typed_revision("p_fixstay4")
        brief = orchestrator._fix_continuation_brief(p, "tkt_0fa11e")
        self.assertIn("continues the fix for issue tkt_0fa11e", brief)
        self.assertIn("Send requests", brief)
        self.assertIn("NOT approved", brief)
        self.assertIn("HOW A CHANGE WORKS HERE", brief)

class PastedSecretIsStoredTest(unittest.TestCase):
    def test_store_scrub_and_name_only(self):
        from agent import transcript, chatlog
        from storage import secrets_store
        p = _workflow([], pid="p_pastedsec")
        p["id"] = "p_pastedsec"

        key = "xoxb-" + "1234567890-abcdefghijklmnop"
        transcript.append_message(p, "user", f"here is my slack token {key} use it")
        chatlog.append("p_pastedsec", "user", {"content": f"token {key}"})
        r = node_tools.tool_declare_variables(
            p, [{"name": "slack_token", "value": key, "secret": True}])
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("secrets_stored"), ["slack_token"])
        self.assertNotIn(key, json.dumps(r))
        self.assertEqual(secrets_store.get_secret("slack_token", secrets_store.workflow_owner("p_pastedsec")), key)
        var = next(v for v in p["variables"] if v["name"] == "slack_token")
        self.assertTrue(var["secret"])
        self.assertNotEqual(var.get("value"), key)
        texts = [it.get("text", "") for it in transcript.items(p)]
        self.assertTrue(all(key not in t for t in texts))
        self.assertTrue(any("(secret slack_token)" in t for t in texts))
        import config
        log = (config.DATA_DIR / "logs" / "p_pastedsec.jsonl").read_text()
        self.assertNotIn(key, log)
        self.assertIn("(secret slack_token)", log)
        secrets_store.delete_secret("slack_token", secrets_store.workflow_owner("p_pastedsec"))

    def test_the_chat_seam_nudges_on_a_credential_shape(self):
        from agent import orchestrator
        note = orchestrator._pasted_credential_note(
            "use this key " + "AIza" + "SyA1234567890abcdefghijklmnopqrstuv"
            + " for maps")
        self.assertIn("store it NOW", note)
        self.assertIn("Google API key", note)
        self.assertNotIn("AIza" + "Sy", note)
        self.assertEqual(orchestrator._pasted_credential_note("hello there"), "")

class BrowserTypeAndCaptureTest(unittest.TestCase):
    def test_predicates_treat_browser_like_a_connector(self):
        import models
        import step_types
        b = {"type": "browser", "read_only": False}
        self.assertTrue(step_types.reaches_outside(b))
        self.assertTrue(step_types.writes_outside(b))
        self.assertTrue(step_types.carries_code(b))
        self.assertFalse(step_types.writes_outside({"type": "browser", "read_only": True}))
        self.assertFalse(step_types.reaches_outside({"type": "code"}))
        self.assertTrue(step_types.carries_code({"type": models.NodeType.CONNECTOR}))
        self.assertIn("browser", step_types.TYPES)
        self.assertIn("browser", node_tools.PORT_TYPE_NAMES)

    def test_a_write_browser_step_needs_approval_and_impact(self):
        from step_types import writes_outside
        self.assertTrue(writes_outside({"type": "browser", "read_only": False}))
        self.assertFalse(writes_outside({"type": "browser", "read_only": True}))

    def test_a_launch_of_its_own_is_refused_the_apps_window_is_not(self):
        p = _workflow([], pid="p_btype3")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Read the page", "type": "browser", "read_only": True,
             "url": "https://example.test/",
             "external_impact": "opens the page", "code_sketch": "reads",
             "outputs": [{"name": "html", "type": "longtext"}]}]})
        own = ("ctx = pw.chromium.launch_persistent_context(profile,\n"
               "    args=['--disable-blink-features=AutomationControlled'])\n"
               "write_output('html', 'x')")
        r = node_tools.tool_run_cell(p, "Read the page", own, reason="first look")
        self.assertIn("already open", r.get("error", ""))
        self.assertIsNone(r.get("needs_browser_ok"))
        ours = ("with browser_page() as page:\n"
                "    browser_goto(page, 'https://example.test/')\n"
                "    write_output('html', 'x')")
        r2 = node_tools.tool_run_cell(p, "Read the page", ours, reason="first look")
        self.assertEqual(r2.get("needs_browser_ok"), "Read the page")

    def test_a_name_error_names_the_surface(self):
        p = _workflow([], pid="p_btype5")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Parse it", "type": "code", "code_sketch": "parses",
             "outputs": [{"name": "n", "type": "number"}]}]})
        r = node_tools.tool_run_cell(p, "Parse it", "x = read_file('a')\nwrite_output('n', 1)")
        self.assertFalse(r.get("ok"))

        self.assertIn("'read_file' is read but never defined", r.get("error", ""))
        self.assertIn("browser_goto", r.get("error", ""))
        self.assertNotIn("browser_snapshot", r.get("error", ""))

class StepIdentityForLifeTest(unittest.TestCase):
    def _plan(self, p, steps, **extra):
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        return node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": steps, **extra})

    def test_ids_are_minted_and_references_store_them(self):
        p = _workflow([], pid="p_sid1")
        r = self._plan(p, [
            {"name": "Fetch", "type": "code", "code_sketch": "f",
             "outputs": [{"name": "rows", "type": "list"}]},
            {"name": "Judge", "type": "code", "code_sketch": "j",
             "inputs": [{"name": "rows", "type": "list"}],
             "outputs": [{"name": "verdicts", "type": "list"}]}],
            edges=[{"src": "Fetch", "dst": "Judge"}])
        self.assertTrue(r.get("ok"), r)
        steps = p["plan"]["nodes"]
        self.assertTrue(all(str(s.get("id") or "").startswith("node_") for s in steps))
        self.assertEqual(p["plan"]["edges"][0],
                         {"src": steps[0]["id"], "dst": steps[1]["id"]})

        r2 = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Judge", "type": "code", "code_sketch": "j2",
             "inputs": [{"name": "rows", "type": "list"}],
             "outputs": [{"name": "verdicts", "type": "list"}]}],
            "edges": [{"src": "Fetch", "dst": "Judge"}]})
        self.assertTrue(r2.get("ok"), r2)
        self.assertEqual([s["id"] for s in p["plan"]["nodes"]], [steps[0]["id"], steps[1]["id"]])

    def test_a_rename_by_id_keeps_the_recording_and_the_decline(self):
        from agent import cells, transcript
        p = _workflow([], pid="p_sid2")
        cells.record("p_sid2", "Check login", "write_output('ok', True)", {},
                     {"ok": True}, True, 0.1)
        r = self._plan(p, [{"name": "Check login", "type": "browser",
                            "read_only": True, "external_impact": "opens the site",
                            "code_sketch": "c", "outputs": [{"name": "ok", "type": "boolean"}]}])
        self.assertTrue(r.get("ok"), r)
        step = p["plan"]["nodes"][0]; sid = step["id"]
        self.assertIsNotNone(cells.latest_ok_for("p_sid2", step))

        e = transcript.append_request(p, "approval", {"title": "t", "detail": "d",
                                                      "scope": "s", "step": "Check login",
                                                      "step_id": sid}, iid="int_sid2")
        transcript.append_answer(p, e["iid"], "No", shown="deny")
        self.assertTrue(node_tools.is_declined(p, step))
        r2 = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"id": sid, "name": "Probe LinkedIn login state", "type": "browser",
             "read_only": True, "external_impact": "opens the site",
             "code_sketch": "c", "outputs": [{"name": "ok", "type": "boolean"}]}]})
        self.assertTrue(r2.get("ok"), r2)
        new = p["plan"]["nodes"][0]
        self.assertEqual(new["name"], "Probe LinkedIn login state")
        self.assertEqual(new["id"], sid)
        self.assertIsNotNone(cells.latest_ok_for("p_sid2", new))
        self.assertTrue(node_tools.is_declined(p, new))
        self.assertTrue(node_tools.is_declined(p, "Probe LinkedIn login state"))

    def test_a_new_name_without_the_id_adopts_the_removed_step(self):
        from agent import cells
        p = _workflow([], pid="p_sid3")
        cells.record("p_sid3", "Check login", "write_output('ok', True)", {},
                     {"ok": True}, True, 0.1)
        self._plan(p, [{"name": "Check login", "type": "browser", "read_only": True,
                        "external_impact": "opens", "code_sketch": "c",
                        "outputs": [{"name": "ok", "type": "boolean"}]}])
        sid = p["plan"]["nodes"][0]["id"]
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Probe login", "type": "browser", "read_only": True,
             "external_impact": "opens", "code_sketch": "c",
             "outputs": [{"name": "ok", "type": "boolean"}]}],
            "changes": {"removed": ["Check login"]}})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual([n["name"] for n in p["plan"]["nodes"]], ["Probe login"])
        self.assertEqual(p["plan"]["nodes"][0]["id"], sid)
        self.assertTrue(any("renamed" in n for n in r.get("notes") or []), r.get("notes"))

    def test_the_built_node_carries_the_plan_steps_id(self):
        from agent import cells
        p = _workflow([], pid="p_sid4")
        code = "write_output('n', 1)"
        self._plan(p, [{"name": "Count", "type": "code", "code": code,
                        "outputs": [{"name": "n", "type": "number"}]}])
        sid = p["plan"]["nodes"][0]["id"]
        cells.record("p_sid4", "Count", code, {}, {"n": 1}, True, 0.1, step_id=sid)
        r = node_tools.build_step(p, "Count")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(p["nodes"][0]["id"], sid)

    def test_the_window_keeps_the_page_without_being_asked(self):
        from agent import gate
        self.assertEqual(gate.check_recording(
            "with browser_page() as page:\n    browser_goto(page, u)\n"), [])

    def test_an_ask_never_shows_internal_words(self):
        from agent import actions
        self.assertEqual(actions._internal_words("Shall I prove the skeleton's design gap?"),
                         ["prove", "skeleton", "design gap"])
        self.assertEqual(actions._internal_words("Which fit criteria and which spreadsheet cell?"), [])
        p = _workflow([], pid="p_sid6")
        r = actions._ask_user_owned(p, lambda e: None,
                                   {"question": "Should I re-prove the parsing step?",
                                    "options": ["Yes", "No"]})
        self.assertIn("internal words", r.get("error", ""))
        self.assertIn("prove", r["error"])

class EnvironmentWallTest(unittest.TestCase):
    def _attached(self, pid, eid, variables):
        from storage import environments
        environments.save({"id": eid, "name": "Google Drive", "description": "",
                           "group": "", "variables": variables})
        p = _workflow([], pid=pid)
        p["environment_ids"] = [eid]
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Log it", "type": "code", "code_sketch": "logs",
             "outputs": [{"name": "y", "type": "text"}]}]})
        return p

    def test_a_cell_reads_an_environment_value_by_name_and_records_only_what_was_passed(self):
        from agent import cells
        p = self._attached("p_wall_cell", "env_wall1", [
            {"name": "service_account_email_address", "secret": False,
             "value": "cv-scanner@example-workflow.iam.gserviceaccount.com"}])
        r = node_tools.tool_run_cell(
            p, "get service account email",
            "write_output('email', read_input('service_account_email_address'))")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["output"]["email"],
                         "cv-scanner@example-workflow.iam.gserviceaccount.com")
        rec = cells.latest_ok("p_wall_cell", "get service account email")
        self.assertEqual(rec["inputs"], {})

    def test_another_workflows_secret_is_unset_here(self):
        from storage import secrets_store
        p = self._attached("p_wall_sec", "env_wall2", [])
        secrets_store.set_secret("wall_key", "not-yours", secrets_store.workflow_owner("wfl_stranger"))
        try:
            r = node_tools.tool_run_cell(p, "probe key", "write_output('k', get_secret('wall_key'))")
            self.assertFalse(r.get("ok"), r)
            self.assertIn("no stored value called", r["error"])
            self.assertNotIn("not-yours", json.dumps(r))
            secrets_store.set_secret("wall_key", "mine", secrets_store.workflow_owner("p_wall_sec"))
            r = node_tools.tool_run_cell(p, "probe key", "write_output('k', len(get_secret('wall_key')))")
            self.assertTrue(r.get("ok"), r)
            self.assertEqual(r["output"]["k"], len("mine"))
        finally:
            secrets_store.delete_secret("wall_key", secrets_store.workflow_owner("wfl_stranger"))
            secrets_store.delete_secret("wall_key", secrets_store.workflow_owner("p_wall_sec"))

    def test_an_environment_secret_reaches_a_cell_by_name(self):
        from storage import secrets_store
        p = self._attached("p_wall_envsec", "env_wall3", [
            {"name": "google_service_account_key", "secret": True, "value": None}])
        secrets_store.set_secret("google_service_account_key", "env-held",
                                 secrets_store.environment_owner("env_wall3"))
        try:
            r = node_tools.tool_run_cell(p, "read key",
                                         "write_output('n', len(get_secret('google_service_account_key')))")
            self.assertTrue(r.get("ok"), r)
            self.assertEqual(r["output"]["n"], len("env-held"))
        finally:
            secrets_store.delete_secret("google_service_account_key",
                                        secrets_store.environment_owner("env_wall3"))

    def test_declare_refuses_a_name_an_attached_environment_supplies(self):
        p = self._attached("p_wall_decl", "env_wall4", [
            {"name": "service_account_email_address", "secret": False, "value": "a@b.example"},
            {"name": "google_service_account_key", "secret": True, "value": None}])
        r = node_tools.tool_declare_variables(p, [
            {"name": "service_account_email_address", "value": "typed@copy.example"}])
        self.assertIn("error", r)
        self.assertIn("Google Drive", r["error"])
        self.assertNotIn("a@b.example", r["error"])
        r = node_tools.tool_declare_variables(p, [
            {"name": "google_service_account_key", "secret": True}])
        self.assertIn("error", r)
        self.assertIn("never copied out", r["error"])
        self.assertEqual(p.get("variables"), [])
        r = node_tools.tool_declare_variables(p, [
            {"name": "service_account_email_address", "value": "typed@copy.example",
             "overwrite": True}])
        self.assertTrue(r.get("ok"), r)

    def test_a_copied_value_in_code_or_a_setting_is_a_design_gap_never_quoted(self):
        from agent import plan_logic
        p = self._attached("p_wall_scan", "env_wall5", [
            {"name": "service_account_email_address", "secret": False,
             "value": "cv-scanner@example-workflow.iam.gserviceaccount.com"},
            {"name": "region", "secret": False, "value": "eu"}])
        plan = {"summary": "x", "edges": [], "nodes": [
            {"name": "Share it", "type": "code",
             "code": "write_output('ok', share('cv-scanner@example-workflow.iam.gserviceaccount.com'))",
             "outputs": [{"name": "ok", "type": "boolean"}]},
            {"name": "Region step", "type": "code", "code": "write_output('r', 'eu')",
             "outputs": [{"name": "r", "type": "text"}]}]}
        r = plan_logic.check(p, plan)
        hits = [f for f in r["findings"] if "Google Drive" in f]
        self.assertEqual(len(hits), 1, r["findings"])
        self.assertIn('"Share it"', hits[0])
        self.assertIn("service_account_email_address", hits[0])
        self.assertNotIn("cv-scanner", hits[0])
        p["variables"] = [{"name": "service_account_email_address", "secret": False,
                           "value": "cv-scanner@example-workflow.iam.gserviceaccount.com",
                           "persistent": True}]
        r = plan_logic.check(p, {"summary": "x", "edges": [], "nodes": [plan["nodes"][1]]})
        self.assertTrue(any("holds a copy" in f and "cv-scanner" not in f
                            for f in r["findings"]), r["findings"])

class DeclaredFoldersTest(unittest.TestCase):
    def test_paths_sync_onto_the_node(self):
        p = _workflow([], pid="p_paths1")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        p["plan"] = {"status": "draft", "summary": "Do.", "edges": [],
                     "nodes": [{"name": "Read it", "type": "code",
                                "description": "reads", "intention": "read",
                                "inputs": [{"name": "x", "type": "text"}],
                                "outputs": [{"name": "y", "type": "text"}],
                                "paths": ["~/Documents/reports"],
                                "code": "write_output('y', read_input('x'))",
                                "tests": [{"name": "recorded run", "inputs": {"x": "a"},
                                           "expect": "ok", "asserts": []}]}]}
        r = node_tools.build_step(p, "Read it")
        self.assertTrue(r["ok"], r)
        n = node_tools.find_node(p, r["node_id"])
        self.assertEqual(n["config"]["paths"], ["~/Documents/reports"])
        p["plan"]["nodes"][0].pop("paths")
        node_tools.build_step(p, "Read it")
        self.assertNotIn("paths", node_tools.find_node(p, r["node_id"])["config"])

    def test_the_card_carries_and_diffs_the_folders(self):
        p = _workflow([], pid="p_paths2")
        plan = {"summary": "s", "nodes": [{"name": "A", "type": "code",
                                            "paths": ["/Users/me/Desktop", "/Users/me/Documents"]}]}
        self.assertEqual(node_tools.opening_card_payload(p, plan, "plan")["paths"],
                         ["/Users/me/Desktop", "/Users/me/Documents"])
        p["path_allowlist"] = ["/Users/me/Desktop"]
        plan["changes"] = {"modified": ["A"]}
        self.assertEqual(node_tools.opening_card_payload(p, plan, "plan")["paths"],
                         ["/Users/me/Documents"])

class ProposalFirstTest(unittest.TestCase):
    def _built(self, pid):
        p = _workflow([{"id": "n_1", "name": "Read the page", "type": "code",
                       "config": {"code": "write_output('rows', [1])"},
                       "inputs": [], "outputs": [{"name": "rows", "type": "list"}],
                       "tests": []}], pid=pid)
        p["plan"] = {"status": "built", "nodes": [
            {"name": "Read the page", "type": "code", "id": "n_1",
             "outputs": [{"name": "rows", "type": "list"}]}]}
        return p

    def test_changed_step_code_waits_for_the_card(self):
        p = self._built("p_pf1")
        r = node_tools.tool_run_cell(p, "Read the page",
                                     "write_output('rows', [1, 2])")
        self.assertIn("has not seen", r.get("error", ""))

    def test_the_step_exactly_as_built_still_runs(self):
        p = self._built("p_pf2")
        r = node_tools.tool_run_cell(p, "Read the page",
                                     "write_output('rows', [1])")
        self.assertTrue(r.get("ok"), r)

    def test_after_the_click_the_work_runs(self):
        p = self._built("p_pf3")
        p["plan"]["change_approved_ts"] = 1.0
        r = node_tools.tool_run_cell(p, "Read the page",
                                     "write_output('rows', [1, 2])")
        self.assertTrue(r.get("ok"), r)

    def test_a_fix_waits_for_its_own_diagnosis_card(self):
        p = self._built("p_pf4")
        p["tickets"] = [{"id": "tkt_9", "status": "in-progress", "node_id": "n_1",
                         "reason": "node threw: KeyError", "verdict": {}}]
        turnstate.of(p).fix_issue_id = "tkt_9"
        r = node_tools.tool_run_cell(p, "Read the page",
                                     "write_output('rows', [1, 2])")
        self.assertIn("fix_note", r.get("error", ""))
        p["plan"]["fix_approved_ts"] = 1.0
        self.assertTrue(node_tools.tool_run_cell(
            p, "Read the page", "write_output('rows', [1, 2])").get("ok"))

    def test_a_probe_under_its_own_name_is_free(self):
        p = self._built("p_pf5")
        r = node_tools.tool_run_cell(p, "check the response shape",
                                     "write_output('n', 1)")
        self.assertTrue(r.get("ok"), r)

    def test_the_build_refuses_a_change_the_user_never_saw(self):
        p = self._built("p_pf6")
        p["plan"]["changes"] = {"modified": ["Read the page"]}
        p["plan"]["status"] = "draft"
        self.assertIn("hasn't seen",
                      node_tools.tool_build_workflow(p).get("error", ""))

class TheWindowIsTheAppsTest(unittest.TestCase):
    def _proj(self, pid):
        p = _workflow([], pid=pid)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Read the page", "type": "browser", "code_sketch": "reads",
             "external_impact": "reads a page", "read_only": True,
             "outputs": [{"name": "page", "type": "file"}]}]})
        _user_approved(p)
        return p

    def test_its_own_launch_is_refused(self):
        p = self._proj("p_wk1")
        own = ("ctx = pw.chromium.launch_persistent_context(profile, record_har_path=h)\n"
               "write_output('page', write_file('p.html', browser_snapshot(page)))")
        r = node_tools.tool_run_cell(p, "Read the page", own, reason="first look")
        self.assertIn("already open", r.get("error", ""))

    def test_the_apps_window_goes_on_to_the_ordinary_card(self):
        p = self._proj("p_wk2")
        code = ("with browser_page() as page:\n"
                "    browser_goto(page, 'https://example.test/')\n"
                "    write_output('page', 'x')")
        r = node_tools.tool_run_cell(p, "Read the page", code, reason="first look")
        self.assertNotIn("browser_page()", r.get("error", ""))
        self.assertIn("go-ahead", r.get("error", ""))

class FailingRunKeepsItsRecordingTest(unittest.TestCase):
    def _fixing(self, pid, saw):
        p = _workflow([{"id": "n_1", "name": "Read the page", "type": "browser",
                       "config": {"code": "x"}, "inputs": [],
                       "outputs": [{"name": "rows", "type": "list"}], "tests": []}],
                     pid=pid)
        p["tickets"] = [{"id": "tkt_7", "status": "in-progress", "node_id": "n_1",
                         "case_id": None, "reason": "service-unavailable",
                         "verdict": {}, "saw": saw}]
        turnstate.of(p).fix_issue_id = "tkt_7"
        return p

    def test_the_page_and_the_har_are_chainable_by_name(self):
        p = self._fixing("p_fk1", {"page": {"url": "https://x/y",
                                            "page": "blob:aa", "screenshot": "blob:bb"},
                                   "har": "blob:cc",
                                   "files": {"threads.json": "blob:dd"}})
        pool = node_tools.issue_case_inputs(p)
        self.assertEqual(pool["page"], "blob:aa")
        self.assertEqual(pool["screenshot"], "blob:bb")
        self.assertEqual(pool["har"], "blob:cc")
        self.assertEqual(pool["threads.json"], "blob:dd")

    def test_a_run_that_kept_nothing_says_so(self):
        p = self._fixing("p_fk2", {})
        self.assertEqual(node_tools.issue_case_inputs(p), {})
        _, err = node_tools.resolve_recorded("rows", "$issue", {},
                                             node_tools.issue_case_inputs(p))
        self.assertIn("kept NOTHING", err)

class RunTimeRecordingGateTest(unittest.TestCase):
    def setUp(self):
        from agent import gate
        self.gate = gate

    LAUNCH = ("ctx = p.chromium.launch_persistent_context(browser_profile(), channel=\"chrome\")\n")

    def test_a_window_a_step_opens_itself_is_flagged(self):
        v = self.gate.check_recording(self.LAUNCH + "page.goto(url)\n")
        self.assertEqual(len(v), 1)
        self.assertIn("already open", v[0])

    def test_the_apps_window_passes(self):
        code = ("with browser_page() as page:\n"
                "    browser_goto(page, url)\n"
                "    write_output('rows', rows)\n")
        self.assertEqual(self.gate.check_recording(code), [])

    def test_code_that_opens_no_window_is_none_of_its_business(self):
        self.assertEqual(self.gate.check_recording("write_output('a', 1)"), [])

    def test_a_loop_that_never_says_a_word_is_flagged(self):
        code = ("for t in read_input('threads'):\n"
                "    page.goto(t['url'])\n")
        self.assertEqual(len(self.gate.check_silent_loop(code)), 1)

    def test_a_loop_that_reports_as_it_goes_passes(self):
        code = ("for i, t in enumerate(read_input('threads')):\n"
                "    heartbeat(f'thread {i}')\n"
                "    page.goto(t['url'])\n")
        self.assertEqual(self.gate.check_silent_loop(code), [])

    def test_a_loop_that_touches_nothing_outside_passes(self):
        code = "for t in read_input('rows'):\n    total = total + t['n']\n"
        self.assertEqual(self.gate.check_silent_loop(code), [])

    def test_a_step_that_drives_chrome_must_be_typed_browser(self):
        from agent import plan_logic
        p = _workflow([], pid="p_btype")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        plan = {"summary": "Do.", "nodes": [
            {"name": "Read the page", "type": "connector", "read_only": True,
             "external_impact": "reads a page",
             "code": self.LAUNCH + "write_output('rows', [1])",
             "outputs": [{"name": "rows", "type": "list"}]}]}
        r = plan_logic.check(p, plan)
        self.assertTrue(any('type to "browser"' in f for f in r["findings"]),
                        r["findings"])

class OnlyAWindowCanAskTest(unittest.TestCase):
    def _check(self, node_type, code):
        from agent import plan_logic
        p = _workflow([], pid=f"p_ask_{node_type}")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        plan = {"summary": "Do.", "nodes": [
            {"name": "Read the page", "type": node_type, "code": code,
             "read_only": True, "external_impact": "reads a page",
             "outputs": [{"name": "rows", "type": "list"}]}]}
        return plan_logic.check(p, plan)["findings"]

    def test_a_code_step_may_not_ask(self):
        found = self._check("code", "confirm_with_user('do a thing')\n"
                                    "write_output('rows', [1])")
        self.assertTrue(any("only a browser step can" in f for f in found), found)

    def test_a_browser_step_may(self):
        code = ("ctx = p.chromium.launch_persistent_context(browser_profile(), record_har_path=browser_har_path())\n"
                "confirm_with_user(\"Log in, then confirm\")\n"
                "snap = browser_snapshot(page)\n"
                "write_output(\"rows\", write_file(\"p.html\", snap))\n")
        found = self._check("browser", code)
        self.assertFalse(any("confirm_with_user" in f for f in found), found)

class TheWindowIsTheAppsTest(unittest.TestCase):
    def test_the_packages_gate_runs_before_any_card(self):
        from agent import cell_gates
        names = [g.__name__ for g in cell_gates.CELL_GATES]
        self.assertLess(names.index("gate_packages"), names.index("gate_browser_card"))
        self.assertLess(names.index("gate_packages"), names.index("gate_send_card"))

    def test_the_app_declares_playwright_for_its_own_launcher(self):
        from agent import gate
        code = "browser_goto(page, 'https://x.test')\n"
        self.assertEqual(gate.required_packages(code, [], is_browser=True),
                         ["playwright"])
        self.assertEqual(gate.required_packages(code, ["playwright==1.4"],
                                                is_browser=True),
                         ["playwright==1.4"])
        self.assertEqual(gate.required_packages(code, []), [])
        self.assertEqual(gate.required_packages("x = 1", [], is_browser=True),
                         ["playwright"])

    def test_a_cell_carries_the_library_without_the_model_saying_so(self):
        from agent import cell_gates
        p = _workflow([], pid="p_win_pkgs")
        c = cell_gates.CellCall(p, name="fetch", code="browser_goto(page, 'https://x.test')\n",
                                inputs={}, packages=[], fresh=False, browser_ok=False,
                                reason="r", send_ok=False, missing="", browser=True)
        self.assertEqual(c.packages, ["playwright"])
        plain = cell_gates.CellCall(p, name="fetch", code="x = 1\n",
                                    inputs={}, packages=[], fresh=False, browser_ok=False,
                                    reason="r", send_ok=False, missing="")
        self.assertEqual(plain.packages, [])

    def test_the_launcher_ignores_the_options_it_owns(self):
        import inspect
        from runtime import capability
        src = inspect.getsource(capability.browser_page)
        for own in ("record_har_path", "channel", "headless", "args"):
            self.assertIn(own, src)

        self.assertLess(src.index("context_options.pop"), src.index("sync_playwright().start()"))

    def test_a_failed_launch_does_not_spend_the_yes(self):
        from unittest import mock
        from agent import actions, node_tools
        p = _workflow([], pid="p_win_grant")
        cards = []

        def fake_approve(workflow, emit, title, detail, scope, step="", step_id="", **kw):
            cards.append(title)
            return "allow"

        calls = {"n": 0}

        def fn(workflow, **kw):
            calls["n"] += 1
            if not kw.get("browser_ok"):
                return {"ok": False, "needs_browser_ok": "Fetch page",
                        "browser_repeat": False}

            return {"ok": False, "error": "NodeError: No module named 'playwright'",
                    "window_opened": False}

        with mock.patch.object(actions, "_approve", fake_approve):
            out = actions._cell_approvals(p, lambda ev: None, fn,
                                          {"name": "Fetch page", "reason": "r"},
                                          fn(p))
        self.assertFalse(out["ok"])
        self.assertEqual(len(cards), 1)

        grants = p.get("approval_grants") or []
        self.assertEqual([g["step"] for g in grants], ["Fetch page"])
        self.assertEqual(actions._approve(p, lambda ev: None, "Open a browser window?",
                                          "d", "s", step="Fetch page"), "allow")
        self.assertEqual(p.get("approval_grants"), [])

class FirstCardHasNoDeltaTest(unittest.TestCase):
    def test_nothing_built_means_no_changes_on_the_card(self):
        p = {"id": "wfl_t", "nodes": [], "edges": [], "egress_allowlist": []}
        plan = {"summary": "s", "nodes": [{"name": "Keep rows", "type": "code",
                                           "description": "d"}],
                "changes": {"removed": ["Filter expense rows"], "modified": [],
                            "added": []}}
        card = node_tools.opening_card_payload(p, plan, "plan")
        self.assertEqual(card["changes"], {})
        self.assertEqual([s["name"] for s in card["steps"]], ["Keep rows"])

    def test_a_built_workflow_keeps_its_delta(self):
        p = {"id": "wfl_t", "edges": [], "egress_allowlist": [],
             "nodes": [{"id": "n1", "name": "Keep rows", "type": "code",
                        "config": {}, "inputs": [], "outputs": []}]}
        plan = {"summary": "s", "nodes": [{"id": "n1", "name": "Keep rows",
                                           "type": "code", "description": "d"}],
                "changes": {"removed": ["Old step"], "modified": ["Keep rows"],
                            "added": []}}
        card = node_tools.opening_card_payload(p, plan, "plan")
        self.assertEqual(card["changes"].get("removed"), ["Old step"])

class TypesBeforeTheFirstTryTest(unittest.TestCase):
    def _plan(self, p, step):
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        return node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [step]})

    def test_an_untyped_port_is_refused_at_the_try_and_at_the_build(self):
        from agent import steps as _steps
        p = _workflow([], pid="p_types1")
        r = self._plan(p, {"name": "Count them", "type": "code", "code_sketch": "counts",
                           "inputs": [{"name": "limit"}], "outputs": [{"name": "n", "type": "number"}]})
        self.assertTrue(r["ok"], r)
        r = node_tools.tool_run_cell(p, "Count them", "write_output('n', 1)", {"limit": 2})
        self.assertIn("no type yet", r.get("error", ""))
        self.assertIn(_steps.UNTYPED_PORTS, r.get("error", ""))
        r = node_tools.tool_build_workflow(p)
        self.assertIn(_steps.UNTYPED_PORTS, r.get("error", ""))

    def test_the_try_checks_inputs_and_outputs_as_a_run_does(self):
        from agent import cells
        p = _workflow([], pid="p_types2")
        self._plan(p, {"name": "Count them", "type": "code", "code_sketch": "counts",
                       "inputs": [{"name": "limit", "type": "number"}],
                       "outputs": [{"name": "n", "type": "number"}]})

        r = node_tools.tool_run_cell(p, "Count them", "write_output('n', 1)", {"limit": "10"})
        self.assertFalse(r.get("ok"))
        self.assertIn("inputs do not match", r["error"])
        self.assertIn("should be a number", r["error"])
        self.assertIn("never convert", r["note"])
        self.assertIsNone(cells.latest_ok("p_types2", "Count them"))

        r = node_tools.tool_run_cell(p, "Count them", "write_output('n', 'one')", {"limit": 10})
        self.assertFalse(r.get("ok"))
        self.assertIn("result did not match", r["error"])
        self.assertIsNone(cells.latest_ok("p_types2", "Count them"))
        self.assertEqual(r["output"], {"n": "one"})

        r = node_tools.tool_run_cell(p, "Count them", "write_output('n', 3)", {"limit": 10})
        self.assertTrue(r.get("ok"), r)

    def test_an_empty_required_output_fails_the_try_and_an_optional_one_passes(self):
        p = _workflow([], pid="p_types3")
        self._plan(p, {"name": "Read post", "type": "code", "code_sketch": "reads",
                       "outputs": [{"name": "title", "type": "text"},
                                   {"name": "text", "type": "longtext"}]})
        code = "write_output('title', 'A'); write_output('text', '')"
        r = node_tools.tool_run_cell(p, "Read post", code, {})
        self.assertFalse(r.get("ok"))
        self.assertIn("produced it empty", r["error"])
        self.assertIn("optional", r["note"])
        self._plan(p, {"name": "Read post", "type": "code", "code_sketch": "reads",
                       "outputs": [{"name": "title", "type": "text"},
                                   {"name": "text", "type": "longtext", "optional": True}]})
        r = node_tools.tool_run_cell(p, "Read post", code, {})
        self.assertTrue(r.get("ok"), r)

    def test_a_setting_takes_the_type_of_the_port_that_reads_it_at_save(self):
        p = _workflow([], pid="p_types4")
        p["variables"] = [{"name": "limit", "label": "Limit", "value": "10", "persistent": True},
                          {"name": "note", "label": "Note", "value": "abc", "persistent": True}]
        from storage import store as _store
        _store.save(p)
        r = self._plan(p, {"name": "Count them", "type": "code", "code_sketch": "counts",
                           "inputs": [{"name": "limit", "type": "number"}],
                           "outputs": [{"name": "n", "type": "number"}]})
        self.assertTrue(r["ok"], r)
        limit = next(v for v in p["variables"] if v["name"] == "limit")
        self.assertEqual(limit["value"], 10)
        self.assertEqual(limit["type"], "number")

        r = node_tools.tool_run_cell(p, "Count them",
                                     "write_output('n', read_input('limit') + 1)", {})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["output"], {"n": 11})

        r = self._plan(p, {"name": "Count them", "type": "code", "code_sketch": "counts",
                           "inputs": [{"name": "note", "type": "number"}],
                           "outputs": [{"name": "n", "type": "number"}]})
        gaps = " ".join(p["plan"].get("design_gaps") or [])
        self.assertIn('"note" is read as a number', gaps)

    def test_a_recording_that_would_fail_the_run_is_a_gap_at_save(self):
        from agent import cells, plan_logic
        cells.record("p_types5", "Read post", "x", {}, {"title": "A", "text": ""}, True, 0.1, [])
        node = {"name": "Read post", "type": "code", "code_sketch": "reads",
                "outputs": [{"name": "title", "type": "text"}, {"name": "text", "type": "longtext"}],
                "inputs": []}
        r = plan_logic.check(_workflow([], pid="p_types5"),
                             {"summary": "s", "nodes": [node], "edges": []})
        self.assertFalse(r["ok"])
        self.assertTrue(any("in the step's try" in f and "produced it empty" in f
                            for f in r["findings"]), r["findings"])

class EveryYesRidesTheLoopTest(unittest.TestCase):
    def _run(self, p, fn, args):
        from unittest import mock
        from agent import actions
        cards = []

        def fake_approve(workflow, emit, title, detail, scope, step="", step_id="", **kw):
            cards.append(title)
            return "allow"
        with mock.patch.object(actions, "_approve", fake_approve):
            out = actions._cell_approvals(p, lambda ev: None, fn, args, fn(p, **args))
        return out, cards

    def test_a_window_yes_survives_the_address_card_that_follows(self):
        p = _workflow([], pid="p_loop_win")
        seen = []

        def fn(workflow, **kw):
            seen.append(dict(kw))
            if not kw.get("browser_ok"):
                return {"ok": False, "needs_browser_ok": "Probe page", "domain": "x.com"}
            if "api.x.com" not in (workflow.get("egress_allowlist") or []):
                return {"ok": False, "error": "egress blocked: ('api.x.com', 443) is not on the allowlist"}
            return {"ok": True, "output": {"n": 1}}
        out, cards = self._run(p, fn, {"name": "Probe page", "reason": "r", "browser": True,
                                       "url": "https://x.com/"})
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(len(cards), 2)
        self.assertTrue(seen[-1].get("browser_ok"))

    def test_a_send_yes_survives_the_address_card_that_follows(self):
        p = _workflow([], pid="p_loop_send")
        seen = []

        def fn(workflow, **kw):
            seen.append(dict(kw))
            if not kw.get("send_ok"):
                return {"ok": False, "needs_send_ok": "Save rows", "code_key": "k1"}
            if "sheets.googleapis.com" not in (workflow.get("egress_allowlist") or []):
                return {"ok": False, "error": "egress blocked: ('sheets.googleapis.com', 443) is not on the allowlist"}
            return {"ok": True, "output": {"written": 3}}
        out, cards = self._run(p, fn, {"name": "Save rows", "reason": "r"})
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(len(cards), 2)
        self.assertTrue(seen[-1].get("send_ok"))

    def test_a_kept_send_yes_does_not_answer_an_address_card(self):
        from unittest import mock
        from agent import actions
        p = _workflow([], pid="p_loop_kept")
        p["approval_grants"] = [{"step": "Save rows", "kind": "send", "title": "t",
                                 "code_key": "k1", "always": False, "ts": 1.0}]
        raised = []

        def fake_raise(workflow, request_kind, payload, ikind, ipayload, prefix):
            raised.append(payload.get("title"))
            return "iid", {}, {"text": "allow"}
        with mock.patch.object(actions, "_raise_card", fake_raise), \
                mock.patch("agent.reply.settle", lambda *a, **k: None):
            d = actions._approve(p, lambda ev: None, "Send it?", "d", "s", step="Save rows",
                                 holds="send", code_key="k1")
            self.assertEqual(d, "allow")
            self.assertEqual(raised, [])

            d = actions._approve(p, lambda ev: None, "Let this workflow connect to a.com",
                                 "d", "s", step="Save rows", holds="", domain="a.com")
        self.assertEqual(d, "allow")
        self.assertEqual(raised, ["Let this workflow connect to a.com"])

    def test_a_recording_read_as_a_path_gets_the_one_line_hint(self):
        p = _workflow([], pid="p_har_hint")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Size it", "type": "code", "code_sketch": "sizes",
             "inputs": [{"name": "har", "type": "longtext"}], "outputs": []}]})
        r = node_tools.tool_run_cell(p, "Size it", "import os\nos.path.getsize(read_input('har'))",
                                     {"har": '{"log": {"entries": []}}' * 400})
        self.assertFalse(r.get("ok"))
        self.assertIn("path=True", r.get("note", ""))
        self.assertIn("<the contents of input 'har'>", r.get("error", ""))

class CodeCheckedWholeTest(unittest.TestCase):
    def _skel(self, p, name):
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": name, "type": "code", "code_sketch": "does it", "outputs": []}]})

    def test_a_syntax_error_is_named_with_its_line(self):
        from agent import cells
        p = _workflow([], pid="p_code1")
        self._skel(p, "Parse it")
        r = node_tools.tool_run_cell(p, "Parse it", "x = 1\ny = (2\nwrite_output('z', 1)", {})
        self.assertIn("syntax error at line", r.get("error", ""))
        self.assertIsNone(cells.latest_ok("p_code1", "Parse it"))

    def test_undefined_names_and_missing_imports_come_back_together(self):
        from unittest import mock
        from storage import deps
        p = _workflow([], pid="p_code2")
        self._skel(p, "Parse it")
        with mock.patch.object(deps, "missing_modules", lambda wid, roots, app_packages=False: [r for r in roots if r == "bs4"]):
            r = node_tools.tool_run_cell(
                p, "Parse it", "import json, bs4\nwrite_output('z', frob(json.dumps({})))", {})
        err = r.get("error", "")
        self.assertIn("'frob' is read but never defined", err)
        self.assertIn("`bs4` is imported but neither declared", err)
        self.assertIn("'beautifulsoup4'", err)
        self.assertIn("every problem is listed", err)

    def test_a_browser_cells_page_and_the_standard_library_are_known(self):
        from agent import gate
        self.assertEqual(gate.check_undefined_names("page.goto('https://x')\nwrite_output('t', 1)"), [])
        self.assertEqual(gate.import_roots("import json, os\nfrom collections import Counter\nimport bs4"), ["bs4"])
        p = _workflow([], pid="p_code3")
        self._skel(p, "Count it")
        r = node_tools.tool_run_cell(p, "Count it", "import json\nwrite_output('n', len(json.dumps([1])))", {})
        self.assertTrue(r.get("ok"), r)

class SeveralTriesInOneCallTest(unittest.TestCase):
    def _plan(self, p, *names):
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": n, "type": "code", "code_sketch": f"does {n}",
             "outputs": [{"name": f"out_{i}", "type": "number"}]} for i, n in enumerate(names)]})

    def test_independent_cells_run_together_and_come_back_in_order(self):
        from agent import cells
        p = _workflow([], pid="p_many1")
        self._plan(p, "First", "Second")
        r = node_tools.tool_run_cell(p, cells=[
            {"name": "First", "code": "import time\ntime.sleep(0.3)\nwrite_output('out_0', 1)"},
            {"name": "Second", "code": "write_output('out_1', 2)"}])
        self.assertTrue(r["ok"], r)
        self.assertEqual([x["cell"] for x in r["results"]], ["First", "Second"])
        self.assertEqual(r["results"][0]["output"], {"out_0": 1})
        self.assertEqual(r["results"][1]["output"], {"out_1": 2})
        self.assertIsNotNone(cells.latest_ok("p_many1", "First"))
        self.assertIsNotNone(cells.latest_ok("p_many1", "Second"))

    def test_a_cell_reading_another_of_the_same_call_is_refused(self):
        p = _workflow([], pid="p_many2")
        self._plan(p, "First", "Second")
        r = node_tools.tool_run_cell(p, cells=[
            {"name": "First", "code": "write_output('out_0', 1)"},
            {"name": "Second", "code": "write_output('out_1', read_input('out_0'))",
             "inputs": {"out_0": "$recorded"}}])
        self.assertIn("must not read each other's outputs", r["error"])
        self.assertIn('"Second" reads \'out_0\' from "First"', r["error"])

    def test_a_cell_a_gate_answers_is_not_run_and_the_rest_are(self):
        p = _workflow([], pid="p_many3")
        self._plan(p, "First", "Second")
        r = node_tools.tool_run_cell(p, cells=[
            {"name": "First", "code": "write_output('out_0', (1"},
            {"name": "Second", "code": "write_output('out_1', 2)"}])
        self.assertFalse(r["ok"])
        self.assertIn("syntax error", r["results"][0]["error"])
        self.assertEqual(r["results"][0]["cell"], "First")
        self.assertTrue(r["results"][1]["ok"], r["results"][1])

from storage import store

class BenchFindingsOfSeptemberSixteenTest(unittest.TestCase):
    def test_a_fix_that_built_keeps_the_issue_ready(self):
        from agent import orchestrator
        t = FixCardSaysWhyAndWhatTest()
        p = t._built("p_fixready")
        ticket = p["tickets"][0]
        real_key = orchestrator._master_key
        orchestrator._master_key = lambda: ("k", "KEY")

        def built_it(workflow, message, emit):
            for tk in workflow["tickets"]:
                tk["status"] = "ready"
            workflow["plan"]["status"] = "built"
            import time as _t
            workflow["plan"]["ts"] = _t.time()
            return {"content": "", "kind": "text"}
        real = orchestrator._agent_turn
        orchestrator._agent_turn = built_it
        try:
            orchestrator.investigate(p, ticket, lambda e: None)
        finally:
            orchestrator._agent_turn = real
            orchestrator._master_key = real_key
        from storage import store
        self.assertEqual(p["tickets"][0]["status"], "ready")
        self.assertEqual(store.load("p_fixready")["tickets"][0]["status"], "ready")

    def test_a_fix_turn_that_built_nothing_reopens_the_issue(self):
        from agent import orchestrator
        t = FixCardSaysWhyAndWhatTest()
        p = t._built("p_fixopen")
        ticket = p["tickets"][0]
        real = orchestrator._agent_turn
        real_key = orchestrator._master_key
        orchestrator._master_key = lambda: ("k", "KEY")
        orchestrator._agent_turn = lambda w, m, e: {"content": "Nothing to change.", "kind": "text"}
        try:
            orchestrator.investigate(p, ticket, lambda e: None)
        finally:
            orchestrator._agent_turn = real
            orchestrator._master_key = real_key
        self.assertEqual(p["tickets"][0]["status"], "open")

    def test_a_removed_output_takes_its_kept_result_with_it(self):
        node = {"id": "n_sum", "name": "Make report", "type": "code",
                "config": {"code": "write_output('summary', {})\nwrite_output('report_csv', 'a,b')"},
                "inputs": [],
                "outputs": [{"name": "summary", "type": "record"},
                            {"name": "report_csv", "type": "text"}]}
        p = _workflow([node], pid="p_deliv_drop")
        p["deliverables"] = [{"node": "n_sum", "port": "report_csv", "label": "Report csv"},
                             {"node": "n_sum", "port": "summary", "label": "Summary"}]
        p["plan"] = {"status": "built", "built_ts": 1.0, "nodes": [
            {"id": "n_sum", "name": "Make report", "type": "code", "code_sketch": "builds",
             "inputs": [], "outputs": [{"name": "summary", "type": "record"},
                                       {"name": "report_csv", "type": "text"}], "tests": []}],
            "edges": [], "approved_steps": [{"name": "Make report", "type": "code"}]}
        store.save(p)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {
            "summary": "Only the summary now.",
            "nodes": [{"id": "n_sum", "name": "Make report", "type": "code", "code_sketch": "builds",
                       "inputs": [], "outputs": [{"name": "summary", "type": "record"}], "tests": []}],
            "edges": [], "change_note": {"found": "The CSV is not wanted.", "will_change": "Drop it."}})
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("deliverables_dropped"), r)
        self.assertNotIn("deliverables_retargeted", r)
        self.assertEqual([d["port"] for d in p["deliverables"]], ["summary"])

    def test_an_input_named_after_a_stored_secret_is_typed_secret(self):
        from storage import secrets_store
        p = _workflow([], pid="p_secret_port")
        p["variables"] = [{"name": "portal_password", "label": "Portal password",
                           "value": True, "secret": True, "persistent": True}]
        store.save(p)
        secrets_store.set_secret("portal_password", "hunter2", secrets_store.workflow_owner(p["id"]))
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {
            "summary": "Sign in.",
            "nodes": [{"name": "Sign in", "type": "connector", "read_only": True,
                       "code_sketch": "signs in", "domains": ["example.com"],
                       "inputs": [{"name": "portal_password", "type": "text"}],
                       "outputs": [{"name": "signed_in", "type": "boolean"}], "tests": []}],
            "edges": []})
        self.assertTrue(r.get("ok"), r)
        port = p["plan"]["nodes"][0]["inputs"][0]
        self.assertEqual(port["type"], "secret")
        self.assertTrue(any("typed as secret" in n for n in r.get("notes") or []), r.get("notes"))

    def test_a_blank_setting_beside_a_near_named_value_is_noted(self):
        p = _workflow([], pid="p_near_name")
        p["variables"] = [{"name": "orders_portal_username", "label": "Username",
                           "value": "bench", "secret": False, "persistent": True}]
        store.save(p)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {
            "summary": "Sign in.",
            "nodes": [{"name": "Sign in", "type": "connector", "read_only": True,
                       "code_sketch": "signs in", "domains": ["example.com"],
                       "inputs": [{"name": "username", "type": "text"}],
                       "outputs": [{"name": "signed_in", "type": "boolean"}], "tests": []}],
            "edges": []})
        self.assertTrue(r.get("ok"), r)
        notes = " ".join(r.get("notes") or [])
        self.assertIn('"username" is blank while "orders_portal_username" holds a value', notes)

class BenchFindingsSecondBatchTest(unittest.TestCase):
    def test_the_build_refusal_carries_the_freeze_reasons(self):
        from agent import assembler
        p = _workflow([], pid="p_freeze_why")
        p["plan"] = {"status": "draft", "nodes": [{"id": "s1", "name": "Collect", "type": "code",
                                                   "code_sketch": "c", "inputs": [], "outputs": []}],
                     "edges": []}
        real = node_tools.build_step
        node_tools.build_step = lambda w, n: {"ok": False, "step": n,
                                              "errors": ["no recorded run on its current text",
                                                         "output 'orders' never written"]}
        try:
            errors = assembler.assemble(p, p["plan"], lambda e: None)
        finally:
            node_tools.build_step = real
        self.assertTrue(any("no recorded run on its current text; output 'orders' never written" in e
                            for e in errors), errors)
        self.assertFalse(any("could not be built" in e for e in errors), errors)

    def test_a_step_re_sent_under_a_new_id_adopts_its_earlier_self(self):
        from agent import cells
        p = _workflow([], pid="p_newid")
        cells.record("p_newid", "Collect all orders", "write_output('orders', [1])", {},
                     {"orders": [1]}, True, 0.1)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"id": "fetch_orders", "name": "Collect all orders", "type": "browser", "read_only": True,
             "url": "http://127.0.0.1:1/", "external_impact": "reads", "code_sketch": "c",
             "outputs": [{"name": "orders", "type": "list"}]}]})
        self.assertTrue(r.get("ok"), r)
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "replace": True, "nodes": [
            {"id": "collect_orders_v2", "name": "Collect all orders", "type": "browser", "read_only": True,
             "url": "http://127.0.0.1:1/", "external_impact": "reads", "code_sketch": "c",
             "outputs": [{"name": "orders", "type": "list"}]}],
            "changes": {"removed": ["fetch_orders"]}})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(p["plan"]["nodes"][0]["id"], "fetch_orders")
        self.assertIsNotNone(cells.latest_ok_for("p_newid", p["plan"]["nodes"][0]))

    def test_a_repointed_result_takes_its_new_outputs_label(self):
        p = _workflow([], pid="p_deliv_label")
        p["deliverables"] = [{"node": "Make report", "port": "report", "label": "Report"}]
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(p, {"summary": "Build the shortlist.", "nodes": [
            {"name": "Make report", "type": "code", "code_sketch": "builds", "inputs": [],
             "outputs": [{"name": "shortlist", "type": "list", "label": "The shortlist"}], "tests": []}],
            "edges": []})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(p["deliverables"][0]["port"], "shortlist")
        self.assertEqual(p["deliverables"][0]["label"], "The shortlist")

    def test_a_change_turn_that_ends_mid_work_gets_one_nudge(self):
        import time as _t
        from agent import orchestrator
        p = _workflow([{"id": "n1", "name": "Count", "type": "code", "config": {"code": "write_output('n', 1)"},
                        "inputs": [], "outputs": [{"name": "n", "type": "number"}]}], pid="p_nudge")
        p["plan"] = {"status": "built", "built_ts": 1.0, "nodes": [], "edges": []}
        p["intent"] = {"summary": "Count things.", "instructions": "Run it."}
        seen = []

        def turn(workflow, message, emit):
            seen.append(message)
            if len(seen) == 1:
                workflow["plan"]["status"] = "draft"
                workflow["plan"]["ts"] = _t.time()
                return {"content": "Now I'll run the cell with the new code.", "kind": "text"}
            workflow["plan"]["status"] = "built"
            return {"content": "", "kind": "text"}
        real_turn, real_key = orchestrator._agent_turn, orchestrator._master_key
        orchestrator._agent_turn, orchestrator._master_key = turn, lambda: ("k", "KEY")
        try:
            reply = orchestrator.handle_chat_stream(p, "also flag repeats", lambda e: None)
        finally:
            orchestrator._agent_turn, orchestrator._master_key = real_turn, real_key
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1], orchestrator.CHANGE_UNFINISHED_NUDGE)
        self.assertEqual(reply["content"], "")

    def test_no_nudge_when_the_workflow_was_never_built_or_a_card_is_open(self):
        import time as _t
        from agent import orchestrator, transcript
        seen = []

        def turn(workflow, message, emit):
            seen.append(message)
            workflow["plan"]["status"] = "draft"
            workflow["plan"]["ts"] = _t.time()
            return {"content": "Working on it.", "kind": "text"}
        real_turn, real_key = orchestrator._agent_turn, orchestrator._master_key
        orchestrator._agent_turn, orchestrator._master_key = turn, lambda: ("k", "KEY")
        try:
            p = _workflow([], pid="p_nonudge1")
            p["plan"] = {"status": "draft", "nodes": [], "edges": []}
            orchestrator.handle_chat_stream(p, "build it", lambda e: None)
            self.assertEqual(len(seen), 1)
            p2 = _workflow([], pid="p_nonudge2")
            p2["plan"] = {"status": "built", "built_ts": 1.0, "nodes": [], "edges": []}
            transcript.append_request(p2, "ask", {"question": "Which?", "options": ["a", "b"]})
            orchestrator.handle_chat_stream(p2, "change it", lambda e: None)
            self.assertEqual(len(seen), 2)
        finally:
            orchestrator._agent_turn, orchestrator._master_key = real_turn, real_key
