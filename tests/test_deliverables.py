# Tests: deliverables + run history: declaration validation (node/port resolution, secrets refused), manifest persistence (inline vs blob-bounded values, skipped branches), the run-history listing (order, halted attempts) and the freeze tool
from __future__ import annotations

import json
import time
import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from storage import blobstore
from storage import deliverables
from agent import node_tools
from runtime import run_state

def _nodes():
    return [
        {"id": "n_a", "name": "extract", "type": "code", "config": {}, "inputs": [],
         "outputs": [{"name": "total", "type": "number"},
                     {"name": "report", "type": "file", "label": "The report"},
                     {"name": "api_key", "type": "secret"}], "tests": []},
        {"id": "n_b", "name": "confirm", "type": "code", "config": {}, "inputs": [],
         "outputs": [{"name": "status", "type": "text"}], "tests": []},
    ]

class NormaliseTest(unittest.TestCase):
    def test_resolves_names_and_defaults_labels(self):
        p = _workflow(_nodes(), pid="p_del1")
        norm, errs = deliverables.normalise(p, [
            {"node": "extract", "port": "total", "label": "Grand total"},
            {"node": "n_a", "port": "report"}])
        self.assertEqual(errs, [])
        self.assertEqual(norm[0], {"node": "n_a", "port": "total", "label": "Grand total"})
        self.assertEqual(norm[1]["label"], "The report")

    def test_unknown_node_port_and_secret_refused(self):
        p = _workflow(_nodes(), pid="p_del2")
        _, errs = deliverables.normalise(p, [
            {"node": "nope", "port": "x"},
            {"node": "extract", "port": "missing"},
            {"node": "extract", "port": "api_key"}])
        self.assertEqual(len(errs), 3)
        self.assertIn("secret", errs[2])

class PersistTest(unittest.TestCase):
    def _proj(self, pid):
        p = _workflow(_nodes(), pid=pid)
        p["deliverables"], errs = deliverables.normalise(p, [
            {"node": "extract", "port": "total", "label": "Total"},
            {"node": "confirm", "port": "status", "label": "Confirmation"}])
        self.assertEqual(errs, [])
        return p

    def test_inline_values_and_skipped_branch(self):
        p = self._proj("p_del3")
        m = deliverables.persist(p, "run_t1", {"n_a": {"total": 42}}, ["n_a"])
        self.assertEqual(m["results"][0]["value"], 42)
        self.assertTrue(m["results"][1].get("skipped"))
        stored = json.loads((deliverables.outputs_dir("p_del3") / "run_t1.json").read_text())
        self.assertEqual(stored["run_id"], "run_t1")

    def test_step_trace_lands_and_a_halted_run_carries_it_too(self):
        p = self._proj("p_del_tr1")
        trace = [
            {"node": "n_a", "name": "extract", "type": "code",
             "status": "completed", "inputs": {"doc": "d" * 200},
             "output": {"total": 42}},
            {"node": "n_b", "name": "confirm", "type": "connector",
             "status": "skipped", "reason": "branch-not-taken"},
        ]
        m = deliverables.persist(p, "run_tr1", {"n_a": {"total": 42}},
                                 ["n_a"], steps=trace)
        self.assertEqual(m["steps"][0]["output"], {"total": 42})
        self.assertEqual(m["steps"][1]["reason"], "branch-not-taken")
        from runtime import run_state
        run_state.start("p_del_tr1", "run_tr2", {})
        run_state.record_step("run_tr2", "n_a", {"total": 1},
                              trace={"inputs": {"doc": "d"},
                                     "output": {"total": 1}})
        run_state.note_skip("run_tr2", "n_x", "branch-not-taken")
        run_state.halt("run_tr2", "n_b", "missing-value")
        row = next(r for r in deliverables.list_runs("p_del_tr1")
                   if r["run_id"] == "run_tr2")
        self.assertEqual(row["status"], "halted")
        self.assertEqual(row["steps"][0]["output"], {"total": 1})
        self.assertEqual(row["steps"][1]["reason"], "branch-not-taken")
        run_state.finish("run_tr2")

    def test_oversized_value_moves_to_blobstore(self):
        p = self._proj("p_del4")
        big = "x" * (300 * 1024)
        m = deliverables.persist(p, "run_t2", {"n_a": {"total": big},
                                               "n_b": {"status": "sent"}}, ["n_a", "n_b"])
        r = m["results"][0]
        self.assertNotIn("value", r)
        self.assertTrue(r["ref"].startswith("blob:"))
        self.assertEqual(json.loads(blobstore.get(r["ref"])), big)

    def test_blob_ref_output_stays_a_ref(self):
        p = self._proj("p_del5")

        ref = blobstore.put(b"%PDF-1.4 fake deliverable", "application/pdf",
                            {"name": "out.pdf"}, owner=blobstore.OWNER_APP)
        m = deliverables.persist(p, "run_t3", {"n_a": {"total": ref},
                                               "n_b": {"status": "ok"}}, ["n_a", "n_b"])
        r = m["results"][0]
        self.assertEqual(r["ref"], ref)
        self.assertEqual(r["mime"], "application/pdf")
        self.assertEqual(r["name"], "out.pdf")

class ListRunsTest(unittest.TestCase):
    def test_newest_first_and_halted_attempts_included(self):
        p = self._mk("p_del6")
        deliverables.persist(p, "run_old", {"n_a": {"total": 1}}, ["n_a"])
        old = deliverables.outputs_dir("p_del6") / "run_old.json"
        rec = json.loads(old.read_text())
        rec["ts"] = time.time() - 100
        old.write_text(json.dumps(rec))
        deliverables.persist(p, "run_new", {"n_a": {"total": 2}}, ["n_a"])
        run_state.start("p_del6", "run_halt")
        run_state.halt("run_halt", "n_a", "output-check-failed")
        runs = deliverables.list_runs("p_del6")
        self.assertEqual([r["run_id"] for r in runs][:1], ["run_halt"])
        self.assertEqual({r["run_id"] for r in runs},
                         {"run_halt", "run_new", "run_old"})
        halted = runs[0]
        self.assertEqual(halted["status"], "halted")
        self.assertEqual(halted["halted_at"], "n_a")
        run_state.finish("run_halt")

    def test_pages_cover_every_run_newest_first(self):
        p = self._mk("p_del_pg")
        for i in range(27):
            deliverables.persist(p, f"run_{i:02d}", {"n_a": {"total": i}}, ["n_a"])
            f = deliverables.outputs_dir("p_del_pg") / f"run_{i:02d}.json"
            rec = json.loads(f.read_text())
            rec["ts"] = 1000 + i
            f.write_text(json.dumps(rec))
        pg1 = deliverables.runs_page("p_del_pg", 1)
        self.assertEqual((pg1["page"], pg1["pages"], pg1["total"]), (1, 2, 27))
        self.assertEqual(len(pg1["runs"]), deliverables.RUNS_PER_PAGE)
        self.assertEqual(pg1["runs"][0]["run_id"], "run_26")
        pg2 = deliverables.runs_page("p_del_pg", 2)
        self.assertEqual([r["run_id"] for r in pg2["runs"]], ["run_01", "run_00"])

        seen = [r["run_id"] for r in pg1["runs"] + pg2["runs"]]
        self.assertEqual(len(seen), len(set(seen)), 27)

        self.assertEqual(deliverables.runs_page("p_del_pg", 99)["page"], 2)
        self.assertEqual(deliverables.runs_page("p_del_pg", 0)["page"], 1)

        self.assertEqual(len(deliverables.list_runs("p_del_pg")), 27)
        self.assertEqual(len(deliverables.list_runs("p_del_pg", limit=5, offset=25)), 2)

    def _mk(self, pid):
        p = _workflow(_nodes(), pid=pid)
        p["deliverables"], _ = deliverables.normalise(
            p, [{"node": "extract", "port": "total", "label": "Total"}])
        return p

class ToolTest(unittest.TestCase):
    def test_tool_sets_and_refuses(self):
        p = _workflow(_nodes(), pid="p_del7")
        r = node_tools.set_deliverables(
            p, [{"node": "extract", "port": "total", "label": "Total"}])
        self.assertTrue(r["ok"])
        self.assertEqual(p["deliverables"][0]["node"], "n_a")
        r = node_tools.set_deliverables(p, [{"node": "extract", "port": "nope"}])
        self.assertFalse(r["ok"])
        self.assertEqual(p["deliverables"][0]["port"], "total")

if __name__ == "__main__":
    unittest.main()

class WhatTheRunWasGivenTest(unittest.TestCase):
    def _workflow(self):
        from tests._bootstrap import workflow as _mk
        p = _mk([{"id": "n_a", "name": "Fetch", "type": "connector",
                  "config": {}, "read_only": True,
                  "inputs": [{"name": "search_url", "type": "text"},
                             {"name": "api_key", "type": "secret"},
                             {"name": "max_results", "type": "number"}],
                  "outputs": [{"name": "rows", "type": "list"}]}],
                pid="p_given")
        p["variables"] = [
            {"name": "max_results", "label": "How many", "value": 25},
            {"name": "api_key", "label": "API key", "secret": True},
        ]
        return p

    def test_entry_values_and_settings_ride_the_manifest(self):
        p = self._workflow()
        m = deliverables.persist(p, "run_g1", {"n_a": {"rows": []}}, ["n_a"],
                                 entry_inputs={"search_url": "https://x/y"})
        given = {g["name"]: g for g in m["given"]}
        self.assertEqual(given["search_url"]["value"], "https://x/y")
        self.assertEqual(given["search_url"]["source"], "asked at the start")
        self.assertEqual(given["max_results"]["value"], 25)
        self.assertEqual(given["max_results"]["label"], "How many")

    def test_a_secret_is_named_never_valued(self):
        from storage import secrets_store
        p = self._workflow()
        secrets_store.set_secret("api_key", "sk-live-do-not-store-me",
                                 secrets_store.workflow_owner(p["id"]))
        m = deliverables.persist(p, "run_g2", {"n_a": {"rows": []}}, ["n_a"],
                                 entry_inputs={})
        secret = next(g for g in m["given"] if g["name"] == "api_key")
        self.assertTrue(secret["secret"])
        self.assertNotIn("value", secret)
        self.assertNotIn("sk-live", json.dumps(m))

    def test_a_value_no_step_reads_stays_off_the_record(self):
        p = self._workflow()
        p["variables"].append({"name": "unused_note", "value": "noise"})
        m = deliverables.persist(p, "run_g3", {"n_a": {"rows": []}}, ["n_a"],
                                 entry_inputs={})
        self.assertNotIn("unused_note", [g["name"] for g in m["given"]])

class TracePreviewTest(unittest.TestCase):
    def test_long_text_caps_with_a_counted_marker(self):
        got = deliverables.trace_preview("a" * 5000)
        self.assertIn("shortened - 5,000 characters", got)
        self.assertTrue(got.startswith("a" * 100))
        self.assertTrue(got.endswith("a" * 50))

    def test_lists_and_depth_collapse_with_counts(self):
        rows = [{"name": f"p{i}", "role": "r"} for i in range(30)]
        got = deliverables.trace_preview(rows)
        self.assertEqual(len(got), deliverables.TRACE_LIST_ITEMS + 1)
        self.assertIn("and 25 more items", got[-1])
        self.assertIn("fields:", got[-1])
        deep = {"a": {"b": {"c": {"d": [1, 2, 3]}}}}
        self.assertEqual(deliverables.trace_preview(deep)["a"]["b"]["c"],
                         "a record with 1 field")

    def test_a_blob_ref_shows_as_its_stored_file(self):
        from storage import blobstore
        ref = blobstore.put(b"hello", "text/plain", {"name": "page.html"}, owner=blobstore.OWNER_APP)
        got = deliverables.trace_preview(ref)
        self.assertIn("page.html", got)
        self.assertNotIn("blob:", got)

    def test_the_step_budget_collapses_a_big_step_and_every_step_keeps_its_values(self):
        big = {"node": "n1", "status": "completed",
               "output": {"v": "z" * (deliverables.TRACE_STEP_BYTES + 5000)}}
        got = deliverables.bounded_trace([big])
        self.assertIn("shortened", got[0]["note"])
        self.assertLess(len(json.dumps(got[0])),
                        deliverables.TRACE_STEP_BYTES)

        many = [{"node": f"n{i}", "status": "completed",
                 "output": {"v": "y" * 20000}} for i in range(10)]
        got = deliverables.bounded_trace(many)
        self.assertEqual(len([e for e in got if "output" in e]), 10)
        self.assertFalse(any("not shown" in str(e.get("note") or "") for e in got))
