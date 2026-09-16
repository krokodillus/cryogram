# Tests: node_stats: the drawer's Stats numbers are COMPUTED from the corpus, and a failure is classified by WHY it failed (a check said no vs the step erroring out) - from the stored cause when present, from the verdict keys otherwise
from __future__ import annotations

import unittest

from tests import _bootstrap

from runtime import corpus
from storage import node_stats

PID = "p_node_stats"

_NODE = {"id": "", "config": {"code": "write_output('out', 1)"}}

def _stamp(node_id: str) -> dict:
    return {"config_hash": corpus.config_hash({**_NODE, "id": node_id})}

def _seed(node_id: str, successes: int = 0, failures: list | None = None):
    for i in range(successes):
        corpus.record_success(PID, node_id, {"n": i}, {"out": i},
                              meta=_stamp(node_id))
    for f in failures or []:
        corpus.record_failure(PID, node_id, {"n": 0}, meta=_stamp(node_id), **f)

class ClassifyTest(unittest.TestCase):
    def test_cause_wins(self):
        self.assertEqual(node_stats.classify_failure(
            {"cause": "output-check-failed", "verdict": {"thrown": "x"}}), "checks")
        self.assertEqual(node_stats.classify_failure(
            {"cause": "node threw: TimeoutError"}), "error")
        self.assertEqual(node_stats.classify_failure(
            {"cause": "input-contract-failed"}), "checks")
        self.assertEqual(node_stats.classify_failure(
            {"cause": "precondition-failed"}), "checks")

    def test_verdict_keys_classify_when_there_is_no_cause(self):
        self.assertEqual(node_stats.classify_failure(
            {"verdict_keys": ["standard_output_check"]}), "checks")
        self.assertEqual(node_stats.classify_failure(
            {"verdict_keys": ["hard_failures"]}), "checks")
        self.assertEqual(node_stats.classify_failure(
            {"verdict_keys": ["thrown"]}), "error")
        self.assertEqual(node_stats.classify_failure(
            {"verdict_keys": ["precondition"]}), "checks")

    def test_unknown_shape_counts_as_error(self):
        self.assertEqual(node_stats.classify_failure({}), "error")
        self.assertEqual(node_stats.classify_failure({"verdict": {"weird": 1}}), "error")

class ForNodeTest(unittest.TestCase):
    def test_code_node_rate_and_breakdown(self):
        _seed("n_code", successes=3, failures=[
            {"verdict": {"hard_failures": ["bad row"]}, "cause": "output-check-failed"},
            {"verdict": {"thrown": "HTTPError: 500"}, "cause": "node threw: HTTPError"},
        ])
        s = node_stats.for_node(PID, {**_NODE, "id": "n_code", "type": "code"})
        self.assertEqual(s["runs"], 5)
        self.assertEqual(s["successes"], 3)
        self.assertEqual(s["failure_count"], 2)
        self.assertAlmostEqual(s["success_rate"], 0.6)
        self.assertEqual(s["failed_checks"], 1)
        self.assertEqual(s["failed_errors"], 1)

    def test_rate_hidden_below_minimum_runs(self):
        _seed("n_thin", successes=2)
        s = node_stats.for_node(PID, {**_NODE, "id": "n_thin", "type": "connector"})
        self.assertEqual(s["runs"], 2)
        self.assertIsNone(s["success_rate"])

    def test_ai_node_gets_rate_but_no_breakdown(self):
        _seed("n_ai", successes=4, failures=[{"verdict": {"thrown": "x"}}])
        s = node_stats.for_node(PID, {**_NODE, "id": "n_ai", "type": "ai"})
        self.assertEqual(s["runs"], 5)
        self.assertAlmostEqual(s["success_rate"], 0.8)
        self.assertNotIn("failed_checks", s)
        self.assertNotIn("clean_streak", s)

    def test_user_input_gets_runs_only(self):
        _seed("n_user", successes=6)
        s = node_stats.for_node(PID, {**_NODE, "id": "n_user", "type": "user-input"})
        self.assertEqual(s["runs"], 6)
        self.assertNotIn("success_rate", s)
        self.assertNotIn("failed_checks", s)

class AttachTest(unittest.TestCase):
    def test_attach_always_computes_the_truth(self):
        _seed("n_live", successes=3)
        workflow = {"nodes": [
            {**_NODE, "id": "n_live", "type": "code",
             "stats": {"runs": 999, "agreement": 1.0}},
            {**_NODE, "id": "n_never", "type": "code", "stats": {"runs": 412}},
        ]}
        node_stats.attach(PID, workflow)
        self.assertEqual(workflow["nodes"][0]["stats"]["runs"], 3)
        self.assertNotIn("agreement", workflow["nodes"][0]["stats"])
        self.assertEqual(workflow["nodes"][1]["stats"]["runs"], 0)

class CounterResetsOnAChangeTest(unittest.TestCase):
    def _node(self, code):
        return {"id": "n_cnt", "name": "Shape it", "type": "code",
                "config": {"code": code}, "inputs": [], "outputs": []}

    def test_rewriting_a_step_resets_its_counter(self):
        from runtime import corpus
        node = self._node("write_output('y', 1)")
        corpus.record_success("p_cnt1", "n_cnt", {"a": 1}, {"y": 1},
                              meta={"config_hash": corpus.config_hash(node)})
        self.assertEqual(node_stats.for_node("p_cnt1", node)["runs"], 1)
        node["config"]["code"] = "write_output('y', 2)"
        self.assertEqual(node_stats.for_node("p_cnt1", node)["runs"], 0)

    def test_cosmetic_whitespace_keeps_the_history(self):
        from runtime import corpus
        node = self._node("write_output('y', 1)")
        corpus.record_success("p_cnt2", "n_cnt", {"a": 1}, {"y": 1},
                              meta={"config_hash": corpus.config_hash(node)})
        node["config"]["code"] = "write_output('y', 1)   \n"
        self.assertEqual(node_stats.for_node("p_cnt2", node)["runs"], 1)

    def test_an_unstamped_case_belongs_to_no_version(self):
        from runtime import corpus
        node = self._node("write_output('y', 1)")
        corpus.record_success("p_cnt3", "n_cnt", {"a": 1}, {"y": 1})
        self.assertEqual(node_stats.for_node("p_cnt3", node)["runs"], 0)

if __name__ == "__main__":
    unittest.main()

class RegressionSetAsymmetryTest(unittest.TestCase):
    def test_regression_check_sees_old_config_cases(self):
        from agent import receipts
        node = {"id": "n_asym", "type": "code", "name": "Asym",
                "inputs": [], "outputs": [{"name": "out", "type": "any"}],
                "config": {"code": "write_output('out', read_input('x'))"}}
        corpus.record_success("p_asym", "n_asym", {"x": 1}, {"out": 1},
                              meta={"config_hash": "OLD-CONFIG-HASH"})

        counts = node_stats.for_node("p_asym", node)
        self.assertEqual(counts.get("runs", 0), 0)

        stored = corpus.cases("p_asym", "n_asym", "success")
        self.assertEqual(len(stored), 1)
        r = receipts.regression_check(node, workflow_id="p_asym")
        self.assertEqual(r["total"], 1)
