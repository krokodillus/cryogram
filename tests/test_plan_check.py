# Tests: deterministic post-codify validation over crafted workflow+plan dicts: match, missing node, port drift, unwired edge, extra node, incomplete node
from __future__ import annotations

import unittest

from tests import _bootstrap

from agent import plan_check

def _port(name, type_="text", **kw):
    return {"name": name, "type": type_, "label": "", "options": [], **kw}

def _built_node(nid, name, type_="code", **kw):
    n = {"id": nid, "name": name, "type": type_,
         "config": {"code": "def f(x):\n    return x\n", "criteria":
                    [{"expr": "non_empty(y)", "hardness": "soft"}]},
         "inputs": [_port("x")], "outputs": [_port("y")],
         "tests": [{"name": "t", "inputs": {"x": "a"}, "expect": "ok", "asserts": []}]}
    n.update(kw)
    return n

def _plan_node(name, type_="code", **kw):
    return {"name": name, "type": type_, "inputs": [_port("x")],
            "outputs": [_port("y")], **kw}

class PlanCheckTest(unittest.TestCase):
    def test_matching_build_passes(self):
        workflow = {"id": "p", "nodes": [_built_node("n1", "parse")], "edges": [],
                   "deliverables": [{"node": "n1", "port": "y", "label": "Result"}]}
        plan = {"nodes": [_plan_node("parse")], "edges": []}
        res = plan_check.check(workflow, plan, prior_node_ids=set())
        self.assertTrue(res["ok"], res["findings"])

    def test_missing_deliverables_flagged(self):
        workflow = {"id": "p", "nodes": [_built_node("n1", "parse")], "edges": []}
        plan = {"nodes": [_plan_node("parse")], "edges": []}
        res = plan_check.check(workflow, plan, prior_node_ids=set())
        self.assertFalse(res["ok"])
        self.assertTrue(any("set_deliverables" in f for f in res["findings"]))

    def test_missing_node_flagged(self):
        res = plan_check.check({"id": "p", "nodes": [], "edges": []},
                               {"nodes": [_plan_node("ghost")]}, set())
        self.assertIn("was not built", res["findings"][0])

    def test_removed_node_still_present_flagged(self):
        workflow = {"id": "p", "nodes": [_built_node("n1", "parse"),
                                        _built_node("n2", "Debug probe")], "edges": []}
        plan = {"nodes": [_plan_node("parse")], "edges": [],
                "changes": {"removed": ["Debug probe"]}}
        res = plan_check.check(workflow, plan, prior_node_ids={"n1", "n2"})
        self.assertFalse(res["ok"])
        self.assertTrue(any("marked removed but still exists" in f for f in res["findings"]))

    def test_port_type_drift_flagged(self):
        built = _built_node("n1", "parse")
        built["outputs"] = [_port("y", "number")]
        plan = {"nodes": [_plan_node("parse")]}
        res = plan_check.check({"id": "p", "nodes": [built], "edges": []}, plan, set())
        self.assertTrue(any("port 'y'" in f and "plan says" in f
                            for f in res["findings"]), res["findings"])

    def test_unwired_planned_edge_flagged(self):
        workflow = {"id": "p", "nodes": [_built_node("n1", "a"), _built_node("n2", "b")],
                   "edges": []}
        plan = {"nodes": [_plan_node("a"), _plan_node("b")],
                "edges": [{"src": "a", "dst": "b"}]}
        res = plan_check.check(workflow, plan, set())
        self.assertTrue(any("was not wired" in f for f in res["findings"]))

    def test_extra_node_outside_plan_flagged(self):
        workflow = {"id": "p", "nodes": [_built_node("n1", "parse"),
                                        _built_node("n9", "surprise")], "edges": []}
        plan = {"nodes": [_plan_node("parse")]}
        res = plan_check.check(workflow, plan, prior_node_ids=set())
        self.assertTrue(any("not in the plan" in f for f in res["findings"]))

    def test_prior_nodes_are_not_extra(self):
        workflow = {"id": "p", "nodes": [_built_node("n1", "parse"),
                                        _built_node("n0", "existing")], "edges": [],
                   "deliverables": [{"node": "n1", "port": "y", "label": "Result"}]}
        plan = {"nodes": [_plan_node("parse")]}
        res = plan_check.check(workflow, plan, prior_node_ids={"n0"})
        self.assertTrue(res["ok"], res["findings"])

    def test_ai_node_checked_structurally_never_replayed(self):
        built = _built_node("n1", "judge", type_="ai")
        built["config"] = {"prompt": "", "model": {"model": ""}, "criteria": []}
        built["tests"] = []
        plan = {"nodes": [_plan_node("judge", type_="ai")]}
        res = plan_check.check({"id": "p", "nodes": [built], "edges": []}, plan, set())
        finding = next(f for f in res["findings"] if "structurally incomplete" in f)
        self.assertIn("tests not replayed", finding)

    def test_forced_untested_connector_accepted_via_marker(self):
        built = _built_node("n1", "send", type_="connector",
                            external_impact="adds rows to the sheet")
        built["tests"] = []
        built["config"]["first_run_is_test"] = True
        workflow = {"id": "p", "nodes": [built], "edges": [],
                   "deliverables": [{"node": "n1", "port": "y", "label": "R"}]}
        plan = {"nodes": [_plan_node("send", type_="connector")]}
        res = plan_check.check(workflow, plan, set())
        self.assertFalse(any("structurally incomplete" in f
                             for f in res["findings"]), res["findings"])

    def test_connector_without_impact_flagged(self):
        built = _built_node("n1", "send", type_="connector")
        built["external_impact"] = ""
        plan = {"nodes": [_plan_node("send", type_="connector",
                                     external_impact="creates a deal")]}
        res = plan_check.check({"id": "p", "nodes": [built], "edges": []}, plan, set())
        self.assertTrue(any("no external_impact" in f for f in res["findings"]))

if __name__ == "__main__":
    unittest.main()
