# Tests: plan_logic: validate a plan's LOGIC before any code is written
from __future__ import annotations

import unittest

from tests import _bootstrap

from agent import plan_logic

def _code(name, inputs=None, outputs=None, sketch="does the thing", tests=None):
    return {"name": name, "type": "code",
            "inputs": [{"name": n, "type": "text"} for n in (inputs or [])],
            "outputs": [{"name": n, "type": "text"} for n in (outputs or [])],
            "code_sketch": sketch, "tests": tests or []}

def _plan(nodes, edges=None):
    return {"summary": "x", "nodes": nodes, "edges": edges or []}

def _proj(**kw):
    p = {"id": "p", "nodes": [], "edges": [], "variables": []}
    p.update(kw)
    return p

class ResolutionTest(unittest.TestCase):
    def test_coherent_plan_passes(self):
        plan = _plan(
            [_code("scan", outputs=["net"]),
             _code("pick", inputs=["net"], outputs=["chosen"])],
            [{"src": "scan", "dst": "pick"}])
        r = plan_logic.check(_proj(), plan)
        self.assertTrue(r["ok"], r["findings"])

    def test_unresolved_input_is_a_SETTING_not_a_gap(self):
        plan = _plan(
            [_code("scan", outputs=["net"]),
             _code("pick", inputs=["net", "missing"], outputs=["chosen"])],
            [{"src": "scan", "dst": "pick"}])
        r = plan_logic.check(_proj(), plan)
        self.assertTrue(r["ok"], r["findings"])
        self.assertEqual(r["settings"], ["missing"])

    def test_a_declared_variable_is_never_reported_as_a_setting(self):
        plan = _plan([_code("greet", inputs=["region"], outputs=["msg"])])
        proj = _proj(variables=[{"name": "region", "value": ""}])
        r = plan_logic.check(proj, plan)
        self.assertTrue(r["ok"])
        self.assertEqual(r["settings"], [])

    def test_a_near_name_across_an_edge_is_a_TYPO_not_a_setting(self):
        plan = _plan(
            [_code("a", outputs=["customerId"]),
             _code("b", inputs=["customer_id"], outputs=["out"])],
            [{"src": "a", "dst": "b"}])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(r["ok"])
        self.assertEqual(r["settings"], [])
        f = next(f for f in r["findings"] if '"customer_id"' in f)
        self.assertIn("customerId", f)
        self.assertIn("spelled differently", f)

    def test_an_unrelated_name_across_an_edge_is_the_orphan_pair(self):
        plan = _plan(
            [_code("a", outputs=["result"]),
             _code("b", inputs=["data"], outputs=["out"])],
            [{"src": "a", "dst": "b"}])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(r["ok"])
        self.assertEqual(r["settings"], [])
        gap = [f for f in r["findings"] if "nothing reads" in f]
        self.assertTrue(gap, r["findings"])
        self.assertIn('"result"', gap[0])
        self.assertIn('"data"', gap[0])

    def test_input_resolved_from_variable(self):
        plan = _plan([_code("greet", inputs=["region"], outputs=["msg"])])
        proj = _proj(variables=[{"name": "region", "value": "EMEA"}])
        self.assertTrue(plan_logic.check(proj, plan)["ok"])

    def test_input_resolved_from_user_input_entry(self):
        ui = {"name": "ask", "type": "user-input", "inputs": [],
              "per_run_reason": "the user picks a region each run",
              "outputs": [{"name": "region", "type": "text"}]}
        plan = _plan([ui, _code("greet", inputs=["region"], outputs=["msg"])],
                     edges=[{"src": "ask", "dst": "greet"}])
        self.assertTrue(plan_logic.check(_proj(), plan)["ok"])

    def test_secret_input_always_resolves(self):
        node = {"name": "call", "type": "connector", "external_impact": "sends",
                "code_sketch": "calls the api",
                "inputs": [{"name": "api_key", "type": "secret"}],
                "outputs": [{"name": "resp", "type": "text"}]}
        self.assertTrue(plan_logic.check(_proj(), _plan([node]))["ok"])

class LogicPresenceTest(unittest.TestCase):
    def test_missing_code_sketch_flagged(self):
        plan = _plan([_code("a", outputs=["x"], sketch="")])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("what it does" in f for f in r["findings"]))

    def test_ai_node_missing_prompt_flagged_blank_model_is_not(self):
        node = {"name": "judge", "type": "ai", "inputs": [],
                "outputs": [{"name": "verdict", "type": "text"}]}
        r = plan_logic.check(_proj(), _plan([node]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("no prompt" in f for f in r["findings"]))
        self.assertFalse(any("no model" in f for f in r["findings"]))

    def test_step_with_no_output_flagged(self):
        plan = _plan([_code("a", outputs=[])])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("no output" in f for f in r["findings"]))

class ARunGetsOnlyDeclaredInputsTest(unittest.TestCase):
    def test_an_undeclared_read_is_a_gap(self):
        step = _code("Save rows", inputs=["rows"], outputs=["done"])
        step["code"] = ("rows = read_input('rows')\n"
                        "url = read_input('tracking_sheet_url')\n"
                        "write_output('done', True)")
        other = _code("Select rows", inputs=["rows"], outputs=["picked"])
        other["code"] = ("url = read_input('tracking_sheet_url')\n"
                         "write_output('picked', read_input('rows'))")
        r = plan_logic.check(_proj(), _plan([step, other]))
        gaps = [f for f in r["findings"] if "does not declare it as an input" in f]
        self.assertEqual(len(gaps), 2, r["findings"])
        self.assertTrue(all('"tracking_sheet_url"' in g for g in gaps))

    def test_declared_reads_pass(self):
        step = _code("Save rows", inputs=["rows", "tracking_sheet_url"], outputs=["done"])
        step["code"] = ("rows = read_input('rows')\n"
                        "url = read_input('tracking_sheet_url')\n"
                        "write_output('done', True)")
        r = plan_logic.check(_proj(), _plan([step]))
        self.assertFalse([f for f in r["findings"] if "does not declare" in f], r["findings"])

class NoNumberCutsAListTest(unittest.TestCase):
    def _gaps(self, code, inputs):
        step = _code("Fetch threads", inputs=inputs, outputs=["messages"])
        step["code"] = code
        r = plan_logic.check(_proj(), _plan([step]))
        return [f for f in r["findings"] if "maximum from a setting" in f]

    def test_a_slice_on_an_input_is_a_gap(self):
        gaps = self._gaps("items = read_input('conversations')\n"
                          "for c in items[:2]:\n"
                          "    pass\n"
                          "write_output('messages', [])", ["conversations"])
        self.assertEqual(len(gaps), 1)
        self.assertIn('"conversations"', gaps[0])
        self.assertIn("first 2", gaps[0])

    def test_islice_and_a_direct_read_are_gaps(self):
        code = ("from itertools import islice\n"
                "for c in islice(read_input('conversations'), 3):\n"
                "    pass\n"
                "x = read_input('conversations')[0:5]\n"
                "write_output('messages', [])")
        self.assertEqual(len(self._gaps(code, ["conversations"])), 2)

    def test_a_maximum_read_from_a_setting_passes(self):
        code = ("items = read_input('conversations')\n"
                "cap = read_input('max_conversations')\n"
                "for c in items[:cap]:\n"
                "    pass\n"
                "write_output('messages', [])")
        self.assertEqual(self._gaps(code, ["conversations", "max_conversations"]), [])

class CodeStepDomainsTest(unittest.TestCase):
    def test_code_step_with_domains_is_a_gap(self):
        step = _code("fetch it", outputs=["rows"])
        step["domains"] = ["sheets.googleapis.com"]
        r = plan_logic.check(_proj(), _plan([step]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("make it a connector" in f for f in r["findings"]),
                        r["findings"])

    def test_connector_with_domains_is_fine(self):
        step = _code("fetch it", outputs=["rows"])
        step["type"] = "connector"
        step["read_only"] = True
        step["external_impact"] = "reads rows"
        step["domains"] = ["sheets.googleapis.com"]
        r = plan_logic.check(_proj(), _plan([step]))
        self.assertTrue(r["ok"], r["findings"])

    def test_browser_step_with_domains_is_a_gap(self):
        step = _code("read the page", outputs=["rows"])
        step.update(type="browser", read_only=True, external_impact="opens the page",
                    domains=["www.linkedin.com"])
        r = plan_logic.check(_proj(), _plan([step]))
        self.assertTrue(any("only a connector step reaches outside addresses" in f
                            for f in r["findings"]), r["findings"])

    def test_window_settings_belong_to_a_browser_step(self):
        step = _code("read the page", outputs=["rows"])
        step.update(browser_options={"locale": "en-GB"})
        r = plan_logic.check(_proj(), _plan([step]))
        self.assertTrue(any("only a browser step opens a window" in f
                            for f in r["findings"]), r["findings"])

    def test_plain_code_step_stays_fine(self):
        r = plan_logic.check(_proj(), _plan([_code("shape", outputs=["y"])]))
        self.assertTrue(r["ok"], r["findings"])

class PerRunReasonTest(unittest.TestCase):
    def _ui(self, **kw):
        return {"name": "Choose the city", "type": "user-input", "inputs": [],
                "outputs": [{"name": "city", "type": "text"}], **kw}

    def test_user_input_step_without_reason_is_a_gap(self):
        plan = _plan([self._ui(),
                      _code("greet", inputs=["city"], outputs=["msg"])])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(r["ok"])
        f = next(f for f in r["findings"] if "EVERY run" in f)
        self.assertIn("per_run_reason", f)
        self.assertIn("declare_variables", f)

    def test_reason_satisfies_the_gap(self):
        plan = _plan([self._ui(per_run_reason="a different city each run"),
                      _code("greet", inputs=["city"], outputs=["msg"])],
                     edges=[{"src": "Choose the city", "dst": "greet"}])
        self.assertTrue(plan_logic.check(_proj(), plan)["ok"])

class AiOutputShapeTest(unittest.TestCase):
    def _ai(self, outputs):
        return {"name": "Triage posts", "type": "ai", "prompt": "judge",
                "model": "", "inputs": [{"name": "posts", "type": "list"}],
                "outputs": outputs}

    def _with_feed(self, ai_node):
        feed = {"name": "Fetch posts", "type": "code", "code_sketch": "gets",
                "outputs": [{"name": "posts", "type": "list"}]}
        return _plan([feed, ai_node], [{"src": "Fetch posts",
                                        "dst": "Triage posts"}])

    def test_single_any_output_is_a_gap(self):
        r = plan_logic.check(_proj(), self._with_feed(
            self._ai([{"name": "triaged_posts", "type": "list"}])))
        self.assertFalse(r["ok"])
        f = next(x for x in r["findings"] if "unenforced" in x)
        self.assertIn("item_fields", f)

    def test_item_fields_with_outcome_field_satisfy(self):
        out = [{"name": "triaged_posts", "type": "list", "item_fields": [
            {"name": "verdict", "type": "enum",
             "options": ["ENGAGE", "SKIP"], "description": "the verdict"},
            {"name": "reason", "type": "text", "description": "why"}]},
            {"name": "note", "type": "text",
             "description": "one line: what was judged"}]
        r = plan_logic.check(_proj(), self._with_feed(self._ai(out)))
        self.assertTrue(r["ok"], r["findings"])

    def test_concrete_fields_satisfy(self):
        out = [{"name": "verdict", "type": "text"},
               {"name": "reason", "type": "text"}]
        r = plan_logic.check(_proj(), self._with_feed(self._ai(out)))
        self.assertTrue(r["ok"], r["findings"])

class AiOutcomeFieldTest(unittest.TestCase):
    def _ai(self, outputs):
        return {"name": "Triage posts", "type": "ai", "prompt": "judge",
                "model": "", "inputs": [{"name": "posts", "type": "list"}],
                "outputs": outputs}

    def _with_feed(self, ai_node):
        feed = {"name": "Fetch posts", "type": "code", "code_sketch": "gets",
                "outputs": [{"name": "posts", "type": "list"}]}
        return _plan([feed, ai_node], [{"src": "Fetch posts",
                                        "dst": "Triage posts"}])

    _ITEMS = [{"name": "verdict", "type": "enum",
               "options": ["ENGAGE", "SKIP"], "description": "the verdict"}]

    def test_only_list_outputs_is_a_gap(self):
        r = plan_logic.check(_proj(), self._with_feed(self._ai(
            [{"name": "triaged_posts", "type": "list",
              "item_fields": self._ITEMS}])))
        self.assertFalse(r["ok"])
        f = next(x for x in r["findings"]
                 if "indistinguishable from a failed call" in x)
        self.assertIn("outcome", f)

    def test_list_plus_outcome_field_passes(self):
        r = plan_logic.check(_proj(), self._with_feed(self._ai(
            [{"name": "triaged_posts", "type": "list",
              "item_fields": self._ITEMS},
             {"name": "note", "type": "text", "description": "outcome"}])))
        self.assertTrue(r["ok"], r["findings"])

class TestConsistencyTest(unittest.TestCase):
    def test_example_using_undeclared_input_flagged(self):
        node = _code("a", inputs=["x"], outputs=["y"],
                     tests=[{"name": "t1", "inputs": {"x": 1, "z": 2}, "expect": "ok"}])
        node2 = _code("src", outputs=["x"])
        plan = _plan([node2, node], [{"src": "src", "dst": "a"}])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any('"z"' in f and "disagree" in f for f in r["findings"]))

    def test_no_tests_and_no_criteria_is_still_coherent(self):
        plan = _plan([_code("a", outputs=["x"])])
        self.assertTrue(plan_logic.check(_proj(), plan)["ok"])

class RoutingTest(unittest.TestCase):
    def test_when_names_unknown_output_flagged(self):
        plan = _plan(
            [_code("a", outputs=["a"]), _code("b", outputs=["out"])],
            [{"src": "a", "dst": "b", "when": "kind == 'x'"}])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("does not" in f and '"kind"' in f for f in r["findings"]))

    def test_when_on_real_output_passes(self):
        plan = _plan(
            [_code("a", outputs=["kind"]), _code("b", outputs=["out"])],
            [{"src": "a", "dst": "b", "when": "kind == 'x'"}])
        self.assertTrue(plan_logic.check(_proj(), plan)["ok"])

class DatalessEdgeTest(unittest.TestCase):
    def test_a_dataless_line_is_an_order_link_the_save_questions(self):
        nodes = [_code("a", outputs=["a"]),
                 _code("b", inputs=["b"], outputs=["out"])]
        plan = _plan(nodes, [{"src": "a", "dst": "b"}])
        proj = _proj(variables=[{"name": "b", "value": "x"}])
        r = plan_logic.check(proj, plan)
        self.assertEqual(len(r["findings"]), 1, r["findings"])
        self.assertIn("share nothing", r["findings"][0])

    def test_ambiguous_name_from_two_earlier_steps_is_a_design_gap(self):
        nodes = [_code("a", outputs=["value"]),
                 _code("b", inputs=[], outputs=["value"]),
                 _code("c", inputs=["value"], outputs=["out"])]
        plan = _plan(nodes, [{"src": "a", "dst": "b"},
                             {"src": "b", "dst": "c"}])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("more than one earlier step" in f
                            for f in r["findings"]), r["findings"])

        nodes2 = [_code("a", outputs=["value"]),
                  _code("mid", inputs=["value"], outputs=["other"]),
                  _code("c", inputs=["value", "other"], outputs=["out"])]
        plan2 = _plan(nodes2, [{"src": "a", "dst": "mid"},
                               {"src": "mid", "dst": "c"}])
        self.assertTrue(plan_logic.check(_proj(), plan2)["ok"])

    def test_value_resolves_across_order_hops(self):
        nodes = [_code("a", outputs=["value"]),
                 {**_code("mid", inputs=["value"], outputs=["other"]),
                  "type": "connector", "read_only": False,
                  "external_impact": "posts a note"},
                 _code("c", inputs=["value"], outputs=["out"])]
        plan = _plan(nodes, [{"src": "a", "dst": "mid"},
                             {"src": "mid", "dst": "c"}])
        r = plan_logic.check(_proj(), plan)
        self.assertTrue(r["ok"], r["findings"])

    def test_routing_edge_not_flagged(self):
        nodes = [_code("a", outputs=["kind"]),
                 _code("b", inputs=["other"], outputs=["out"])]
        plan = _plan(nodes, [{"src": "a", "dst": "b", "when": "kind == 'x'"}])
        proj = _proj(variables=[{"name": "other", "value": "y"}])
        r = plan_logic.check(proj, plan)
        self.assertTrue(r["ok"], r["findings"])

    def test_data_edge_clean(self):
        nodes = [_code("a", outputs=["v"]), _code("b", inputs=["v"], outputs=["out"])]
        plan = _plan(nodes, [{"src": "a", "dst": "b"}])
        self.assertTrue(plan_logic.check(_proj(), plan)["ok"])

class DeliverableTest(unittest.TestCase):
    def test_deliverable_pointing_at_missing_port_flagged(self):
        plan = _plan([_code("b", outputs=["out"])])
        proj = _proj(deliverables=[{"node": "b", "port": "gone", "label": "R"}])
        r = plan_logic.check(proj, plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("saved result" in f for f in r["findings"]))

class EmptyPlanTest(unittest.TestCase):
    def test_change_only_plan_is_ok(self):
        self.assertTrue(plan_logic.check(_proj(), {"summary": "x", "nodes": []})["ok"])

class RecordedTypeTest(unittest.TestCase):
    def test_list_declared_record_is_a_gap(self):
        from agent import cells
        cells.record("p_rt1", "Scan", "code", {}, {"nets": ["a", "b"]}, True, 0.1, [])
        node = {"name": "Scan", "type": "code", "inputs": [],
                "outputs": [{"name": "nets", "type": "record"}],
                "code_sketch": "scans", "tests": []}
        r = plan_logic.check(_proj(id="p_rt1"), _plan([node]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("a list is type 'list'" in f for f in r["findings"]), r)

    def test_matching_and_uncheckable_types_pass(self):
        from agent import cells
        cells.record("p_rt2", "Scan", "code", {},
                     {"nets": ["a"], "count": 2, "name": "x"}, True, 0.1, [])
        node = {"name": "Scan", "type": "code", "inputs": [],
                "outputs": [{"name": "nets", "type": "list"},
                            {"name": "count", "type": "number"},
                            {"name": "name", "type": "text"}],
                "code_sketch": "scans", "tests": []}
        self.assertTrue(plan_logic.check(_proj(id="p_rt2"), _plan([node]))["ok"])

if __name__ == "__main__":
    unittest.main()

class SecretPortDesignGapTest(unittest.TestCase):
    def test_unread_secret_port_with_get_secret_is_a_gap(self):
        node = {"name": "Connect", "type": "connector",
                "external_impact": "joins wifi",
                "inputs": [{"name": "ssid", "type": "text"},
                           {"name": "wifi_password", "type": "secret"}],
                "outputs": [{"name": "ok", "type": "boolean"}],
                "code": ("ssid = read_input('ssid')\n"
                         "pw = get_secret('wifi_password')\n"
                         "write_output('ok', bool(ssid and pw))"),
                "tests": []}
        r = plan_logic.check(_proj(), _plan([node]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("wifi_password" in f and "drop the port" in f
                            for f in r["findings"]), r["findings"])

    def test_consistent_designs_pass(self):
        reads_port = dict(
            name="Connect", type="connector", external_impact="joins wifi",
            inputs=[{"name": "wifi_password", "type": "secret"}],
            outputs=[{"name": "ok", "type": "boolean"}],
            code="write_output('ok', bool(get_secret(read_input('wifi_password'))))", tests=[])
        no_port = dict(
            name="Connect2", type="connector", external_impact="joins wifi",
            inputs=[{"name": "ssid", "type": "text"}],
            outputs=[{"name": "ok", "type": "boolean"}],
            code=("write_output('ok', bool(read_input('ssid') and get_secret('wifi_password')))"), tests=[])
        proj = _proj(variables=[{"name": "ssid", "value": "net"}])
        for node in (reads_port, no_port):
            r = plan_logic.check(proj, _plan([node]))
            self.assertTrue(r["ok"], (node["name"], r["findings"]))

class GateSymmetryTest(unittest.TestCase):
    def test_plan_code_ignoring_inputs_is_a_gap(self):
        node = {"name": "Shape", "type": "code",
                "inputs": [{"name": "x", "type": "text"}],
                "outputs": [{"name": "y", "type": "text"}],
                "code": "write_output('y', 'fixed')", "tests": []}
        proj = _proj(variables=[{"name": "x", "value": "v"}])
        r = plan_logic.check(proj, _plan([node]))
        self.assertFalse(r["ok"])
        self.assertTrue(any('"Shape"' in f and "ignores" in f
                            for f in r["findings"]), r["findings"])

    def test_honest_plan_code_passes(self):
        node = {"name": "Shape", "type": "code",
                "inputs": [{"name": "x", "type": "text"}],
                "outputs": [{"name": "y", "type": "text"}],
                "code": "write_output('y', read_input('x').upper())", "tests": []}
        proj = _proj(variables=[{"name": "x", "value": "v"}])
        self.assertTrue(plan_logic.check(proj, _plan([node]))["ok"])

class RecordPickerGapTest(unittest.TestCase):
    def test_picker_over_records_without_value_field_is_a_gap(self):
        from agent import cells
        cells.record("p_pick1", "Scan", "code", {},
                     {"networks": [{"ssid": "A", "security": "WPA2"}]},
                     True, 0.1, [])
        plan = {"summary": "s", "nodes": [
            {"name": "Scan", "type": "code", "code_sketch": "scans",
             "outputs": [{"name": "networks", "type": "list"}]},
            {"name": "Pick", "type": "user-input",
             "inputs": [{"name": "networks", "type": "list"}],
             "outputs": [{"name": "ssid", "type": "text"}]}],
            "edges": [{"src": "Scan", "dst": "Pick"}]}
        r = plan_logic.check(_proj(id="p_pick1"), plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("value_field" in f for f in r["findings"]), r["findings"])
        plan["nodes"][1]["outputs"][0]["value_field"] = "ssid"
        self.assertTrue(plan_logic.check(_proj(id="p_pick1"), plan)["ok"])

class FieldSeamTest(unittest.TestCase):
    def _plan_for(self, code):
        return {"summary": "s", "nodes": [
            {"name": "Fetch", "type": "code", "code_sketch": "fetches",
             "outputs": [{"name": "customer", "type": "record"}]},
            {"name": "Use", "type": "code", "code": code,
             "inputs": [{"name": "customer", "type": "record"}],
             "outputs": [{"name": "out", "type": "text"}]}],
            "edges": [{"src": "Fetch", "dst": "Use"}]}

    def test_wrong_field_name_is_a_gap(self):
        from agent import cells
        cells.record("p_fld1", "Fetch", "code", {},
                     {"customer": {"customerId": 7, "name": "Acme"}}, True, 0.1, [])
        bad = ("c = read_input('customer')\n"
               "write_output('out', str(c['customer_id']))")
        r = plan_logic.check(_proj(id="p_fld1"), self._plan_for(bad))
        self.assertFalse(r["ok"])
        self.assertTrue(any("'customer_id'" in f and "customerId" in str(f)
                            for f in r["findings"]), r["findings"])
        good = ("c = read_input('customer')\n"
                "write_output('out', str(c['customerId']))")
        self.assertTrue(plan_logic.check(_proj(id="p_fld1"),
                                         self._plan_for(good))["ok"])

    def test_list_elements_and_loops_are_covered(self):
        from agent import cells
        cells.record("p_fld2", "Fetch", "code", {},
                     {"customer": [{"ssid": "A"}, {"ssid": "B"}]}, True, 0.1, [])
        loop = ("nets = read_input('customer')\n"
                "names = []\n"
                "for n in nets:\n"
                "    names.append(n['name'])\n"
                "write_output('out', ','.join(names))")
        r = plan_logic.check(_proj(id="p_fld2"), self._plan_for(loop))
        self.assertFalse(r["ok"])
        self.assertTrue(any("'name'" in f for f in r["findings"]), r["findings"])

    def test_no_recording_means_no_finding(self):
        code = ("c = read_input('customer')\n"
                "write_output('out', str(c['whatever']))")
        self.assertTrue(plan_logic.check(_proj(id="p_fld3"),
                                         self._plan_for(code))["ok"])

class FilePortEvidenceTest(unittest.TestCase):
    def test_inline_text_on_a_file_port_is_a_gap(self):
        from agent import cells
        cells.record("p_fp1", "Split", "code", {},
                     {"good": "name,email\nA,a@b.no"}, True, 0.1, [])
        node = {"name": "Split", "type": "code", "code_sketch": "splits",
                "outputs": [{"name": "good", "type": "file"}], "inputs": []}
        r = plan_logic.check(_proj(id="p_fp1"),
                             {"summary": "s", "nodes": [node], "edges": []})
        self.assertFalse(r["ok"])
        self.assertTrue(any("write_file" in f for f in r["findings"]), r["findings"])

    def test_blob_ref_on_a_file_port_passes(self):
        from agent import cells
        cells.record("p_fp2", "Split", "code", {},
                     {"good": "blob:" + "a" * 64}, True, 0.1, [])
        node = {"name": "Split", "type": "code", "code_sketch": "splits",
                "outputs": [{"name": "good", "type": "file"}], "inputs": []}
        self.assertTrue(plan_logic.check(
            _proj(id="p_fp2"), {"summary": "s", "nodes": [node], "edges": []})["ok"])

class NeverDemoteTest(unittest.TestCase):
    def test_stored_name_as_user_input_is_a_design_gap(self):
        from storage import secrets_store

        secrets_store.set_secret("teams_webhook_url", "https://x",
                                 secrets_store.workflow_owner("p_demote"))
        try:
            proj = {"id": "p_demote", "nodes": [], "edges": [],
                    "variables": [{"name": "teams_webhook_url", "secret": True,
                                   "value": True, "persistent": True}]}
            plan = _plan([
                dict(name="Ask for the webhook", type="user-input",
                     outputs=[{"name": "teams_webhook_url", "type": "text"}]),
                dict(name="Send it", type="connector", external_impact="posts",
                     inputs=[{"name": "teams_webhook_url", "type": "text"}],
                     code="write_output('ok', bool(read_input('teams_webhook_url')))",
                     outputs=[{"name": "ok", "type": "boolean"}], tests=[])])
            plan["edges"] = [{"src": "Ask for the webhook", "dst": "Send it"}]
            r = plan_logic.check(proj, plan)
            self.assertFalse(r["ok"])
            self.assertTrue(any("never demote" in f for f in r["findings"]), r)
            other = {"id": "p_demote_other", "nodes": [], "edges": [], "variables": []}
            r2 = plan_logic.check(other, plan)
            self.assertFalse(any("never demote" in f for f in r2["findings"]), r2)
        finally:
            secrets_store.delete_secret("teams_webhook_url",
                                        secrets_store.workflow_owner("p_demote"))

class SecretValueUseTest(unittest.TestCase):
    def test_name_used_as_value_refused_with_teaching(self):
        from agent import gate
        v = gate.check_secret_value_use(
            "import urllib.request\n"
            "url = read_input('webhook_url')\n"
            "urllib.request.urlopen(url)\n", ["webhook_url"])
        self.assertTrue(v)
        self.assertIn("NAME", v[0])
        ok = gate.check_secret_value_use(
            "u = get_secret(read_input('webhook_url'))\n", ["webhook_url"])
        self.assertEqual(ok, [])

class OutputNameContractTest(unittest.TestCase):
    CODE = ('result = {"logged_in": True, "cookie_names": ["reddit_session"]}\n'
            'write_output("login_status", result)\n')

    def _plan_with(self, code):
        return _plan([dict(name="Check Reddit login", type="connector",
                           external_impact="reads the login cookie",
                           read_only=True, inputs=[], code=code,
                           outputs=[{"name": "logged_in", "type": "boolean"}],
                           tests=[])])

    def test_written_name_that_no_port_declares_is_a_gap(self):
        r = plan_logic.check(_proj(id="p_outname"), self._plan_with(self.CODE))
        self.assertFalse(r["ok"], r["findings"])
        f = " ".join(r["findings"])
        self.assertIn("login_status", f)
        self.assertIn("logged_in", f)

    def test_matching_names_pass(self):
        good = 'write_output("logged_in", True)\n'
        r = plan_logic.check(_proj(id="p_outname2"), self._plan_with(good))
        self.assertTrue(r["ok"], r["findings"])

    def test_a_declared_port_never_written_is_a_gap(self):
        plan = _plan([dict(name="Fetch", type="code", inputs=[],
                           code='write_output("posts", [1, 2])\n',
                           outputs=[{"name": "posts", "type": "list"},
                                    {"name": "count", "type": "number"}],
                           tests=[])])
        r = plan_logic.check(_proj(id="p_outname3"), plan)
        self.assertFalse(r["ok"], r["findings"])
        self.assertIn("count", " ".join(r["findings"]))

    def test_a_computed_output_name_is_not_flagged(self):
        plan = _plan([dict(name="Fetch", type="code", inputs=[],
                           code=('for k in ["a", "b"]:\n'
                                 '    write_output(k, 1)\n'),
                           outputs=[{"name": "a", "type": "number"},
                                    {"name": "b", "type": "number"}],
                           tests=[])])
        r = plan_logic.check(_proj(id="p_outname4"), plan)
        self.assertTrue(r["ok"], r["findings"])

    def test_a_recording_of_OTHER_code_never_clears_the_step(self):
        from agent import cells
        cells.record("p_outname5", "Check Reddit login",
                     'write_output("logged_in", True)', {},
                     {"logged_in": True}, True, 0.1)
        r = plan_logic.check(_proj(id="p_outname5"), self._plan_with(self.CODE))
        self.assertFalse(r["ok"], r["findings"])
        self.assertIn("login_status", " ".join(r["findings"]))

    def test_a_matching_recording_resolves_computed_names(self):
        from agent import cells
        code = ('for k in ["a", "b"]:\n'
                '    write_output(k, 1)\n')
        cells.record("p_outname6", "Fetch", code, {}, {"a": 1, "b": 1},
                     True, 0.1)
        plan = _plan([dict(name="Fetch", type="code", inputs=[], code=code,
                           outputs=[{"name": "a", "type": "number"}],
                           tests=[])])
        r = plan_logic.check(_proj(id="p_outname6"), plan)
        self.assertTrue(r["ok"], r["findings"])
        plan2 = _plan([dict(name="Fetch", type="code", inputs=[], code=code,
                            outputs=[{"name": "a", "type": "number"},
                                     {"name": "c", "type": "number"}],
                            tests=[])])
        r2 = plan_logic.check(_proj(id="p_outname6"), plan2)
        self.assertFalse(r2["ok"], r2["findings"])
        self.assertIn('"c"', " ".join(r2["findings"]))

    def test_a_predicate_that_cannot_evaluate_is_a_gap(self):
        from agent import cells
        cells.record("p_when1", "Check", 'write_output("state", "ok")', {},
                     {"state": "ok"}, True, 0.1)
        plan = _plan([dict(name="Check", type="code", inputs=[],
                           code='write_output("state", "ok")',
                           outputs=[{"name": "state", "type": "text"}], tests=[]),
                      _code("Then", inputs=[], outputs=["done"])],
                     [{"src": "Check", "dst": "Then", "when": "logged_in == True"}])
        r = plan_logic.check(_proj(id="p_when1"), plan)
        self.assertFalse(r["ok"], r["findings"])
        self.assertIn("does not work on what that step actually produced",
                      " ".join(r["findings"]))

    def test_a_predicate_that_evaluates_passes(self):
        from agent import cells
        cells.record("p_when2", "Check", 'write_output("state", "ok")', {},
                     {"state": "ok"}, True, 0.1)
        plan = _plan([dict(name="Check", type="code", inputs=[],
                           code='write_output("state", "ok")',
                           outputs=[{"name": "state", "type": "text"}], tests=[]),
                      _code("Then", inputs=[], outputs=["done"])],
                     [{"src": "Check", "dst": "Then", "when": "state == 'ok'"}])
        r = plan_logic.check(_proj(id="p_when2"), plan)
        self.assertTrue(r["ok"], r["findings"])

class CoherenceTest(unittest.TestCase):
    def _chain(self, extra_edges=()):
        nodes = [_code("A", outputs=["a"]), _code("B", ["a"], ["b"]),
                 _code("C", ["b"], ["c"]), _code("D", ["c"], ["d"])]
        edges = [{"src": "A", "dst": "B"}, {"src": "B", "dst": "C"},
                 {"src": "C", "dst": "D"}] + list(extra_edges)
        return _plan(nodes, edges)

    def test_a_line_that_adds_nothing_is_reported(self):
        plan = self._chain([{"src": "A", "dst": "D"}])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any('"A" -> "D" adds nothing' in f
                            for f in r["findings"]), r["findings"])

    def test_a_conditional_bypass_is_a_real_path_and_stands(self):
        plan = self._chain([{"src": "A", "dst": "D", "when": "a == 'skip'"}])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(any("adds nothing" in f for f in r["findings"]),
                         r["findings"])

    def test_a_plain_chain_reports_nothing(self):
        self.assertTrue(plan_logic.check(_proj(), self._chain())["ok"])

    def _ask(self, name="Ask for the name", outputs=("name",)):
        return {"name": name, "type": "user-input", "inputs": [],
                "outputs": [{"name": o, "type": "text"} for o in outputs],
                "per_run_reason": "different each run"}

    def _browser(self, name, inputs=(), outputs=("post_text",), read_only=True):
        return {"name": name, "type": "browser", "url": "https://x.example",
                "read_only": read_only,
                "inputs": [{"name": i, "type": "text"} for i in inputs],
                "outputs": [{"name": o, "type": "text"} for o in outputs],
                "code_sketch": "reads the page"}

    def test_a_line_between_two_steps_that_share_nothing_is_reported(self):
        plan = _plan([self._ask(), self._browser("Get top post"),
                      _code("Check", ["name", "post_text"], ["found"])],
                     [{"src": "Ask for the name", "dst": "Get top post"},
                      {"src": "Get top post", "dst": "Check"}])
        r = plan_logic.check(_proj(), plan)
        hit = [f for f in r["findings"] if "share nothing" in f]
        self.assertEqual(len(hit), 1, r["findings"])
        self.assertIn('"Ask for the name" -> "Get top post"', hit[0])
        self.assertIn("separate branches", hit[0])

    def test_two_starts_meeting_at_the_step_that_needs_both_is_clean(self):
        plan = _plan([self._ask(), self._browser("Get top post"),
                      _code("Check", ["name", "post_text"], ["found"])],
                     [{"src": "Ask for the name", "dst": "Check"},
                      {"src": "Get top post", "dst": "Check"}])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(any("share nothing" in f for f in r["findings"]),
                         r["findings"])

    def test_a_line_the_destination_reads_across_is_clean(self):
        plan = _plan([self._ask(), self._browser("Search", inputs=("name",)),
                      _code("Check", ["post_text"], ["found"])],
                     [{"src": "Ask for the name", "dst": "Search"},
                      {"src": "Search", "dst": "Check"}])
        r = plan_logic.check(_proj(), plan)
        self.assertFalse(any("share nothing" in f for f in r["findings"]),
                         r["findings"])

    def test_the_lines_left_alone(self):
        cases = {
            "send": ([self._browser("Post it", inputs=("text",), outputs=("status",),
                                    read_only=False),
                      _code("Log", [], ["logged"])],
                     [{"src": "Post it", "dst": "Log"}]),
            "browser pair": ([self._browser("Log in", outputs=("session",)),
                              self._browser("Read inbox", outputs=("mails",))],
                             [{"src": "Log in", "dst": "Read inbox"}]),
            "condition": ([_code("A", outputs=["a"]), _code("B", [], ["b"])],
                          [{"src": "A", "dst": "B", "when": "a == 'x'"}]),
            "no outputs": ([_code("Guard", outputs=[]), _code("B", [], ["b"])],
                           [{"src": "Guard", "dst": "B"}]),
            "folder": ([{**_code("Write file", outputs=["path"]),
                         "paths": ["~/Documents"]},
                        _code("Open it", [], ["opened"])],
                       [{"src": "Write file", "dst": "Open it"}]),
        }
        for label, (nodes, edges) in cases.items():
            r = plan_logic.check(_proj(), _plan(nodes, edges))
            self.assertFalse(any("share nothing" in f for f in r["findings"]),
                             (label, r["findings"]))

    def test_a_step_nothing_runs_is_reported(self):
        nodes = [_code("A", outputs=["a"]), _code("B", ["a"], ["b"]),
                 _code("Stranded", ["z"], ["y"])]
        r = plan_logic.check(_proj(), _plan(nodes, [
            {"src": "A", "dst": "B"}, {"src": "Stranded", "dst": "B"}]))
        self.assertFalse(r["ok"])
        self.assertTrue(any('nothing runs the "Stranded" step' in f
                            for f in r["findings"]), r["findings"])

    def test_a_floating_step_gets_one_finding_not_two(self):
        nodes = [_code("A", outputs=["a"]), _code("B", ["a"], ["b"]),
                 _code("Stranded", ["z"], ["y"])]
        r = plan_logic.check(_proj(), _plan(nodes, [{"src": "A", "dst": "B"}]))
        hits = [f for f in r["findings"] if "Stranded" in f]
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("not placed in the order", hits[0])

    def test_a_genuine_starting_step_is_not_flagged(self):
        nodes = [_code("A", outputs=["a"]), _code("B", ["a"], ["b"]),
                 _code("Also starts", outputs=["z"])]
        r = plan_logic.check(_proj(), _plan(nodes, [{"src": "A", "dst": "B"}]))
        self.assertFalse(any("nothing runs" in f for f in r["findings"]),
                         r["findings"])

    def test_a_line_naming_a_step_that_does_not_exist(self):
        plan = self._chain([{"src": "C", "dst": "Ghost"}])
        r = plan_logic.check(_proj(), plan)
        self.assertTrue(any("not a step in this plan" in f
                            for f in r["findings"]), r["findings"])

class RepeatedSiblingsTest(unittest.TestCase):
    def _fanout(self, n, body="judge every item in the list"):
        nodes = [_code("Split", outputs=[f"c{i}" for i in range(1, n + 1)]),
                 _code("Join", [f"r{i}" for i in range(1, n + 1)], ["out"])]
        edges = []
        for i in range(1, n + 1):
            nodes.append(_code(f"Do {i}", [f"c{i}"], [f"r{i}"],
                               sketch=f"{body} (chunk {i})"))
            edges += [{"src": "Split", "dst": f"Do {i}"},
                      {"src": f"Do {i}", "dst": "Join"}]
        return _plan(nodes, edges)

    def test_identical_siblings_are_a_design_gap(self):
        r = plan_logic.check(_proj(), self._fanout(10))
        self.assertFalse(r["ok"])
        hit = [f for f in r["findings"] if "same thing in the same place" in f]
        self.assertTrue(hit, r["findings"])
        self.assertIn("10 steps", hit[0])
        self.assertIn("ONE code step", hit[0])

    def test_two_is_already_the_pattern(self):
        r = plan_logic.check(_proj(), self._fanout(2))
        self.assertTrue(any("same thing in the same place" in f
                            for f in r["findings"]), r["findings"])

    def test_two_different_outside_sources_are_not_a_fan_out(self):
        nodes = [_code("Start", outputs=["go"]),
                 _code("Fetch vg.no", ["go"], ["vg"], sketch="fetch vg.no"),
                 _code("Fetch nrk.no", ["go"], ["nrk"], sketch="fetch nrk.no"),
                 _code("Combine", ["vg", "nrk"], ["all"], sketch="merge them")]
        edges = [{"src": "Start", "dst": "Fetch vg.no"},
                 {"src": "Start", "dst": "Fetch nrk.no"},
                 {"src": "Fetch vg.no", "dst": "Combine"},
                 {"src": "Fetch nrk.no", "dst": "Combine"}]
        r = plan_logic.check(_proj(), _plan(nodes, edges))
        self.assertFalse(any("same thing in the same place" in f
                             for f in r["findings"]), r["findings"])

    def test_the_same_work_at_different_points_is_not_a_fan_out(self):
        nodes = [_code("A", outputs=["a"], sketch="tidy the text"),
                 _code("B", ["a"], ["b"], sketch="tidy the text")]
        r = plan_logic.check(_proj(), _plan(nodes, [{"src": "A", "dst": "B"}]))
        self.assertFalse(any("same thing in the same place" in f
                             for f in r["findings"]), r["findings"])

    def test_ai_siblings_name_the_ai_type(self):
        nodes = [_code("Split", outputs=["c1", "c2"]),
                 {"name": "Judge 1", "type": "ai", "prompt": "rate item 1",
                  "inputs": [{"name": "c1", "type": "text"}],
                  "outputs": [{"name": "r1", "type": "text"},
                              {"name": "note1", "type": "text"}]},
                 {"name": "Judge 2", "type": "ai", "prompt": "rate item 2",
                  "inputs": [{"name": "c2", "type": "text"}],
                  "outputs": [{"name": "r2", "type": "text"},
                              {"name": "note2", "type": "text"}]},
                 _code("Join", ["r1", "r2"], ["out"])]
        edges = [{"src": "Split", "dst": "Judge 1"},
                 {"src": "Split", "dst": "Judge 2"},
                 {"src": "Judge 1", "dst": "Join"}, {"src": "Judge 2", "dst": "Join"}]
        r = plan_logic.check(_proj(), _plan(nodes, edges))
        hit = [f for f in r["findings"] if "same thing in the same place" in f]
        self.assertTrue(hit, r["findings"])
        self.assertIn("ONE ai step", hit[0])

class AiEchoesInputTest(unittest.TestCase):
    def _plan(self, item_fields):
        return _plan([{
            "name": "Triage", "type": "ai", "prompt": "judge each post",
            "inputs": [{"name": "posts", "type": "list"}],
            "outputs": [{"name": "triaged", "type": "list",
                         "item_fields": item_fields},
                        {"name": "summary", "type": "text"}]}])

    def _recorded(self, pid):
        from agent import cells
        cells.record(pid, "Triage", "judge each post",
                     {"posts": [{"id": "t3_1", "title": "A title",
                                 "body": "the body", "subreddit": "r/x"}]},
                     {"triaged": [], "summary": "s"}, True, 1.0, [], kind="ai")
        return _proj(id=pid)

    def test_copying_the_input_back_out_is_a_gap(self):
        p = self._recorded("p_echo_a")
        plan = self._plan([{"name": "title", "type": "text"},
                           {"name": "subreddit", "type": "text"},
                           {"name": "verdict", "type": "text"}])
        r = plan_logic.check(p, plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("straight back out" in f for f in r["findings"]),
                        r["findings"])

    def test_an_identifier_plus_a_judgement_is_the_right_shape(self):
        p = self._recorded("p_echo_b")
        plan = self._plan([{"name": "id", "type": "text"},
                           {"name": "verdict", "type": "text"},
                           {"name": "why", "type": "longtext"}])
        self.assertFalse(any("straight back out" in f
                             for f in plan_logic.check(p, plan)["findings"]))

    def test_no_recording_means_no_finding(self):
        plan = self._plan([{"name": "title", "type": "text"},
                           {"name": "body", "type": "longtext"}])
        self.assertFalse(any("straight back out" in f
                             for f in plan_logic.check(_proj(id="p_echo_c"),
                                                       plan)["findings"]))

class SettingVocabularyTest(unittest.TestCase):
    def _plan_with(self, port_type):
        return _plan([{"name": "Use it", "type": "code", "code_sketch": "x",
                       "inputs": [{"name": "thing", "type": port_type}],
                       "outputs": [{"name": "out", "type": "text"}]}])

    def test_every_setting_type_is_accepted(self):
        for t in ("text", "longtext", "number", "date", "boolean",
                  "folder", "file"):
            r = plan_logic.check(_proj(), self._plan_with(t))
            self.assertEqual(r["settings"], ["thing"], t)
            self.assertFalse(any("can only be" in f for f in r["findings"]), t)

    def test_an_undeclared_type_is_still_a_setting(self):
        for t in ("",):
            r = plan_logic.check(_proj(), self._plan_with(t))
            self.assertEqual(r["settings"], ["thing"], repr(t))

    def test_a_type_a_setting_cannot_be_is_a_design_gap(self):
        for t in ("enum", "record"):
            r = plan_logic.check(_proj(), self._plan_with(t))
            self.assertFalse(r["ok"], t)
            f = next(f for f in r["findings"] if "can only be" in f)
            self.assertIn("user-input step", f)
            self.assertEqual(r["settings"], [], t)

    def test_the_vocabulary_has_one_definition(self):
        from storage import environments
        self.assertEqual(environments.setting_type("longtext"), "text")
        self.assertEqual(environments.setting_type("enum"), "")
        self.assertEqual(environments.setting_type("secret"), "")
        for t in environments.VARIABLE_TYPES:
            self.assertEqual(environments.setting_type(t), t)

    def test_port_type_for_never_leaves_the_vocabulary(self):
        from storage import environments
        proj = {"id": "p", "nodes": [{"id": "n", "name": "N", "type": "code",
                "inputs": [{"name": "a", "type": "enum"},
                           {"name": "b", "type": "longtext"},
                           {"name": "c", "type": "date"}],
                "outputs": []}], "edges": [], "variables": []}
        self.assertEqual(environments.port_type_for(proj, "a"), "")
        self.assertEqual(environments.port_type_for(proj, "b"), "text")
        self.assertEqual(environments.port_type_for(proj, "c"), "date")

class CriterionNamesTest(unittest.TestCase):
    def _step(self, expr):
        return {"name": "Save results", "type": "connector",
                "code_sketch": "append rows",
                "external_impact": "writes rows to a sheet",
                "inputs": [{"name": "scored_people", "type": "list"}],
                "outputs": [{"name": "append_result", "type": "record",
                             "item_fields": [{"name": "rows_written",
                                              "type": "number"}]}],
                "criteria": [{"expr": expr, "label": "All rows written"}]}

    def test_a_nested_field_read_as_a_bare_name_is_a_gap(self):
        r = plan_logic.check(_proj(), _plan([self._step(
            "rows_written == len(scored_people)")]))
        gap = next((f for f in r["findings"] if "rows_written" in f), "")
        self.assertTrue(gap, r["findings"])

        self.assertIn("append_result['rows_written']", gap)

    def test_the_subscripted_form_passes(self):
        r = plan_logic.check(_proj(), _plan([self._step(
            "append_result['rows_written'] == len(scored_people)")]))
        self.assertFalse([f for f in r["findings"] if "check" in f],
                         r["findings"])

    def test_inputs_builtins_and_helpers_are_all_in_scope(self):
        r = plan_logic.check(_proj(), _plan([self._step(
            "len(scored_people) >= 0 and non_empty(append_result)")]))
        self.assertFalse([f for f in r["findings"] if "check" in f],
                         r["findings"])

    def test_comprehension_variables_are_not_unknown_names(self):
        step = {"name": "Score", "type": "ai", "prompt": "judge",
                "model": "m", "outputs": [
                    {"name": "people", "type": "list",
                     "item_fields": [{"name": "bucket", "type": "text"}]},
                    {"name": "note", "type": "text"}],
                "criteria": [{"expr": "all(p['bucket'] != '' for p in people)",
                              "label": "Everyone gets a bucket"}]}
        r = plan_logic.check(_proj(), _plan([step]))
        self.assertFalse([f for f in r["findings"] if "check" in f],
                         r["findings"])

class OutsidePathsTest(unittest.TestCase):
    def test_literal_home_path_is_a_gap(self):
        step = _code("read it", outputs=["rows"])
        step["code"] = "write_output('rows', open('/Users/me/Documents/x.csv').read())"
        r = plan_logic.check(_proj(), _plan([step]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("outside the workflow's allowed folders" in f
                            for f in r["findings"]), r["findings"])

    def test_declared_folder_clears_it(self):
        step = _code("read it", outputs=["rows"])
        step["code"] = "write_output('rows', open('/Users/me/Documents/x.csv').read())"
        step["paths"] = ["/Users/me/Documents"]
        r = plan_logic.check(_proj(), _plan([step]))
        self.assertFalse(any("outside the workflow's allowed folders" in f
                             for f in r["findings"]), r["findings"])

    def test_url_paths_and_api_routes_never_match(self):
        step = _code("call it", outputs=["rows"])
        step["code"] = ("u = 'https://x.example/Users/list'\n"
                        "p = '/api/v1/items'\n"
                        "write_output('rows', u + p)")
        r = plan_logic.check(_proj(), _plan([step]))
        self.assertFalse(any("allowed folders" in f for f in r["findings"]), r["findings"])

    def test_the_apps_own_folder_cannot_be_declared(self):
        import config
        step = _code("read it", outputs=["rows"])
        step["paths"] = [str(config.DATA_DIR / "workflows")]
        r = plan_logic.check(_proj(), _plan([step]))
        self.assertTrue(any("app's own data folder" in f for f in r["findings"]),
                        r["findings"])

    def test_a_relative_path_is_a_gap(self):
        step = _code("read it", outputs=["rows"])
        step["paths"] = ["reports"]
        r = plan_logic.check(_proj(), _plan([step]))
        self.assertTrue(any("absolute folder path" in f for f in r["findings"]),
                        r["findings"])

class PerItemTest(unittest.TestCase):
    LOOP = ("items = read_input('items')\n"
            "out = []\n"
            "for it in items:\n"
            "    out.append(it['id'])\n"
            "write_output('ids', out)\n")
    KEPT = ("items = read_input('items')\n"
            "done = checkpoints()\n"
            "for it in items:\n"
            "    if str(it['id']) in done:\n"
            "        continue\n"
            "    checkpoint(str(it['id']), it['id'])\n"
            "write_output('ids', list(checkpoints().values()))\n")

    def _step(self, code, per_item=None):
        n = _code("Send each", inputs=["items"], outputs=["ids"])
        n["code"] = code
        if per_item is not None:
            n["per_item"] = per_item
        return n

    def _gaps(self, step):
        return [f for f in plan_logic.check(_proj(variables=[
            {"name": "items", "value": "", "persistent": True}]),
            _plan([step]))["findings"] if "per_item" in f]

    def test_a_looping_step_without_the_declaration_is_a_gap(self):
        gaps = self._gaps(self._step(self.LOOP))
        self.assertEqual(len(gaps), 1, gaps)
        self.assertIn('works through "items" item by item', gaps[0])

    def test_declared_and_recorded_is_clean(self):
        self.assertEqual(self._gaps(self._step(self.KEPT, {"input": "items", "key": "id"})), [])

    def test_declared_but_never_recorded_is_a_gap(self):
        gaps = self._gaps(self._step(self.LOOP, {"input": "items", "key": "id"}))
        self.assertEqual(len(gaps), 1, gaps)
        self.assertIn("does not record each item", gaps[0])

    def test_a_key_or_input_that_is_not_there_is_a_gap(self):
        gaps = self._gaps(self._step(self.KEPT, {"input": "rows", "key": "id"}))
        self.assertTrue(any('names "rows"' in g for g in gaps), gaps)
        gaps = self._gaps(self._step(self.KEPT, {"input": "items"}))
        self.assertTrue(any("needs both" in g for g in gaps), gaps)

class OneKindOfWorkTest(unittest.TestCase):
    LAUNCH = ("from playwright.sync_api import sync_playwright\n"
              "with sync_playwright() as p:\n"
              "    ctx = p.chromium.launch_persistent_context(browser_profile())\n"
              "    page = ctx.pages[0]\n"
              "    page.goto('https://www.linkedin.com/feed')\n"
              "    write_output('html', page.content())\n")
    SHEETS = ("import urllib.request\n"
              "rows = read_input('rows')\n"
              "req = urllib.request.Request('https://sheets.googleapis.com/v4/x')\n"
              "tok = urllib.request.urlopen('https://oauth2.googleapis.com/token')\n"
              "write_output('written', len(rows))\n")
    TWO = ("import urllib.request\n"
           "a = urllib.request.urlopen('https://api.linkedin.com/v2/me')\n"
           "b = urllib.request.urlopen('https://sheets.googleapis.com/v4/x')\n"
           "write_output('done', True)\n")

    def _step(self, code, typ, **kw):
        n = _code("Do it", inputs=["rows"], outputs=["html"])
        n.update({"code": code, "type": typ, "external_impact": "x",
                  "read_only": True})
        n.update(kw)
        return n

    def _gaps(self, step):
        return [f for f in plan_logic.check(_proj(variables=[
            {"name": "rows", "value": "", "persistent": True}]),
            _plan([step]))["findings"] if f.startswith('the "Do it" step')]

    def test_signature_reads_browser_services_and_ai(self):
        from agent import gate
        sig = gate.work_signature(self.SHEETS)
        self.assertEqual((sig["browser"], sig["http_client"], sig["services"], sig["ai"]),
                         (False, True, ["googleapis.com"], False))
        self.assertTrue(gate.work_signature(self.LAUNCH)["browser"])
        self.assertEqual(gate.work_signature(self.TWO)["services"],
                         ["googleapis.com", "linkedin.com"])
        self.assertTrue(gate.work_signature("x = ai_call('p', {})")["ai"])
        self.assertEqual(gate._service_of("shop.example.co.uk"), "example.co.uk")

    def test_code_reaching_outside_is_a_gap(self):
        gaps = self._gaps(self._step(self.SHEETS, "code"))
        self.assertTrue(any("typed as plain code" in g and "googleapis.com" in g for g in gaps), gaps)
        gaps = self._gaps(self._step(self.LAUNCH, "code"))
        self.assertTrue(any("browser window" in g for g in gaps), gaps)
        gaps = self._gaps(self._step("write_output('html', 'x')", "code", domains=["api.x.com"]))
        self.assertTrue(any("typed as plain code" in g for g in gaps), gaps)

    def test_connector_opening_chrome_or_two_services_is_a_gap(self):
        gaps = self._gaps(self._step(self.LAUNCH, "connector"))
        self.assertTrue(any('set its type to "browser"' in g for g in gaps), gaps)
        gaps = self._gaps(self._step(self.TWO, "connector"))
        self.assertTrue(any("2 outside services" in g for g in gaps), gaps)
        self.assertEqual(self._gaps(self._step(self.SHEETS, "connector")), [])

    def test_browser_calling_another_service_is_a_gap(self):
        gaps = self._gaps(self._step(self.LAUNCH + self.SHEETS, "browser"))
        self.assertTrue(any("own connector step" in g for g in gaps), gaps)
        self.assertEqual(self._gaps(self._step(self.LAUNCH, "browser")), [])

    def test_ai_call_in_step_code_is_a_gap(self):
        gaps = self._gaps(self._step("write_output('html', ai_call('p', {}))", "code"))
        self.assertTrue(any("ai_call" in g for g in gaps), gaps)

    def test_the_save_never_changes_a_declared_type(self):
        import json
        from agent import node_tools
        from tests._bootstrap import workflow as _workflow
        p = _workflow([], pid="p_okw_intake")
        p["intent"] = {"summary": "x", "instructions": "y"}
        res = node_tools.tool_save_plan(p, {"summary": "s s s", "nodes": [
            self._step(self.LAUNCH, "connector")], "edges": []})
        step = (p.get("plan") or {}).get("nodes", [{}])[0]
        self.assertEqual(step.get("type"), "connector", res)
        self.assertIn('set its type to \\"browser\\"', json.dumps(res))

class EveryStepHasALineTest(unittest.TestCase):
    def test_a_plan_with_steps_and_no_lines_is_a_gap_and_one_step_is_not(self):
        from agent import plan_logic
        two = {"nodes": [{"name": "A", "type": "code"}, {"name": "B", "type": "code"}]}
        gaps = plan_logic._unplaced_steps(two, {n["name"]: n for n in two["nodes"]})
        self.assertEqual(len(gaps), 2)
        self.assertIn("not placed in the order", gaps[0])
        one = {"nodes": [{"name": "A", "type": "code"}]}
        self.assertEqual(plan_logic._unplaced_steps(one, {"A": one["nodes"][0]}), [])
        wired = {**two, "edges": [{"src": "A", "dst": "B"}]}
        self.assertEqual(plan_logic._unplaced_steps(wired, {n["name"]: n for n in two["nodes"]}), [])
