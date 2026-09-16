# Tests: tHE SHAPE CONTRACT: one JSON Schema per port, derived from the recorded happy path, validated at every boundary at any depth, and consumed unchanged by the AI enforcement
from __future__ import annotations

import unittest

from tests import _bootstrap
from agent import turnstate
from tests._bootstrap import workflow as _workflow

from runtime import capability, port_checks, shape

POSTS = [
    {"title": "a", "url": "http://x", "score": 3,
     "author": {"name": "u", "karma": 10}, "tags": ["p"], "when": "2026-01-01"},
    {"title": "b", "url": "http://y", "score": 5,
     "author": {"name": "v"}, "photo": None, "when": "2026-01-02"},
]

MUST = [{"name": "title", "type": "text", "required": True},
        {"name": "author", "type": "record", "required": True}]

def posts_schema():
    return shape.apply_item_fields(shape.infer(POSTS), MUST)

class InferAndMergeTest(unittest.TestCase):
    def test_a_list_of_records_infers_a_nested_schema_with_variance(self):
        s = shape.infer(POSTS)
        self.assertEqual(s["type"], "array")
        it = s["items"]

        self.assertEqual(it["required"], [])
        self.assertEqual(it["x-expected"], ["author", "score", "title", "url", "when"])
        self.assertIn("tags", it["properties"])
        self.assertNotIn("tags", it["x-expected"])

        self.assertEqual(it["properties"]["author"]["x-expected"], ["name"])

        self.assertEqual(it["properties"]["when"], {"type": "string", "format": "date"})

        self.assertEqual(it["properties"]["photo"], {"x-nullable": True})

    def test_disagreeing_types_go_unconstrained_never_wrong(self):
        s = shape.merge(shape.infer({"n": 1}), shape.infer({"n": "one"}))
        self.assertEqual(s["properties"]["n"], {})
        self.assertEqual(shape.validate(s, {"n": [1, 2]}), [])

    def test_null_then_typed_becomes_nullable_typed(self):
        s = shape.merge(shape.infer({"p": None}), shape.infer({"p": "x"}))
        self.assertEqual(s["properties"]["p"], {"type": "string", "x-nullable": True})
        self.assertEqual(shape.validate(s, {"p": None}), [])
        self.assertTrue(shape.validate(s, {"p": 3}))

    def test_enums_are_never_inferred(self):
        s = shape.infer([{"k": "a"}, {"k": "b"}])
        self.assertNotIn("enum", s["items"]["properties"]["k"])

    def test_infer_is_deterministic(self):
        self.assertEqual(shape.infer({"b": 1, "a": 2}), shape.infer({"a": 2, "b": 1}))

class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.s = posts_schema()

    def test_the_row_not_the_error(self):
        bad = [{"url": "http://z", "score": "9",
                "author": {"karma": 1}, "when": "2026-01-03"}]
        probs = shape.validate(self.s, bad)
        paths = [p["path"] for p in probs]
        self.assertIn("item 1 > title", paths)
        self.assertIn("item 1 > score", paths)
        self.assertIn("should be a number", probs[1]["problem"])

        self.assertNotIn("item 1 > author > name", paths)

    def test_extra_fields_stay_legal_at_the_gate(self):
        ok = dict(POSTS[0], brand_new="fine")
        self.assertEqual(shape.validate(self.s, [ok]), [])

    def test_optional_field_explicitly_null_passes_like_absence(self):
        s = {"type": "object", "required": ["id"],
             "properties": {"id": {"type": "string"},
                            "angle": {"type": "string"}}}
        self.assertEqual(shape.validate(s, {"id": "a", "angle": None}), [])
        self.assertEqual(shape.validate(s, {"id": "a"}), [])

        probs = shape.validate(s, {"id": None, "angle": "x"})
        self.assertIn("missing", probs[0]["problem"])

    def test_counts_and_which(self):
        rows = POSTS + [{"title": "x"}] + [{"title": "y"}]
        self.assertEqual(shape.failing_items(self.s, rows), [2, 3])

    def test_a_vanished_expected_field_is_a_problem_sparse_is_not(self):
        s = shape.infer([{"id": 1, "email": "a"}, {"id": 2, "email": "b"}])
        many = [{"id": i} for i in range(shape.VANISH_MIN_ITEMS + 1)]
        self.assertEqual(shape.validate(s, many), [])
        self.assertEqual(shape.vanished_fields(s, many), ["email"])

        self.assertEqual(shape.vanished_fields(s, many[:-1] + [{"id": 9, "email": "z"}]), [])
        self.assertEqual(shape.vanished_fields(s, [{"id": i, "email": None} for i in range(6)]), [])

        self.assertEqual(shape.vanished_fields(s, many[:shape.VANISH_MIN_ITEMS - 1]), [])

    def test_empty_required_text_is_a_problem_optional_is_not(self):
        s = {"type": "object", "properties": {"a": {"type": "string"},
                                              "b": {"type": "string"}},
             "required": ["a"]}
        self.assertTrue(shape.validate(s, {"a": "", "b": ""}))
        self.assertEqual(shape.validate(s, {"a": "x", "b": ""}), [])

    def test_date_format_and_enum(self):
        s = {"type": "object", "properties": {
            "d": {"type": "string", "format": "date"},
            "k": {"type": "string", "enum": ["a", "b"]}}, "required": ["d", "k"]}
        self.assertEqual(shape.validate(s, {"d": "2026-02-02", "k": "a"}), [])
        probs = shape.validate(s, {"d": "yesterday", "k": "z"})
        self.assertEqual(len(probs), 2)
        self.assertIn("allowed values", probs[1]["problem"])

    def test_the_problem_cap_is_named_and_the_count_stays_exact(self):
        s = {"type": "array", "items": {"type": "object", "properties": {
            "n": {"type": "number"}}, "required": ["n"]}}
        rows = [{"n": "x"}] * (shape.PROBLEMS_CAP + 20)
        self.assertEqual(len(shape.validate(s, rows)), shape.PROBLEMS_CAP)

class PortProjectionTest(unittest.TestCase):
    def test_from_port_projects_item_fields_and_records(self):
        p = {"name": "posts", "type": "any", "item_fields": [
            {"name": "id", "type": "text"},
            {"name": "photo", "type": "text", "optional": True}]}
        s = shape.from_port(p)
        self.assertEqual(s["type"], "array")
        self.assertEqual(s["items"]["required"], [])
        self.assertEqual(s["items"]["x-expected"], ["id"])
        r = shape.from_port({"name": "who", "type": "record",
                             "item_fields": [{"name": "n", "type": "number"}]})
        self.assertEqual(r["type"], "object")

    def test_item_fields_overlay_annotates_never_replaces(self):
        s = shape.infer(POSTS)
        out = shape.apply_item_fields(s, [
            {"name": "url", "type": "text", "optional": True},
            {"name": "kind", "type": "enum", "options": ["x", "y"]}])
        it = out["items"]
        self.assertNotIn("url", it["x-expected"])
        self.assertIn("author", it["properties"])
        self.assertEqual(it["properties"]["kind"]["enum"], ["x", "y"])
        self.assertIn("kind", it["x-expected"])
        self.assertEqual(s["items"]["x-expected"][0], "author")

class AiLayerSharesTheSchemaTest(unittest.TestCase):
    def test_schema_from_ports_uses_the_stored_nested_schema(self):
        ports = [{"name": "posts", "type": "any", "schema": posts_schema()},
                 {"name": "note", "type": "text"}]
        s = capability.schema_from_ports(ports)
        posts = s["properties"]["posts"]
        self.assertEqual(posts["type"], "array")
        self.assertEqual(posts["items"]["properties"]["author"]["type"], "object")
        self.assertFalse(posts["items"]["additionalProperties"])
        self.assertNotIn("x-nullable", str(s))
        self.assertNotIn("x-expected", str(s))

        it = posts["items"]
        self.assertEqual(it["properties"]["title"]["minLength"], 1)
        self.assertEqual(it["properties"]["url"]["type"], ["string", "null"])
        self.assertIn("url", it["required"])
        self.assertNotIn("tags", it["required"])

        self.assertEqual(s["properties"]["note"]["minLength"], 1)

    def test_a_nullable_field_stays_sayable_at_the_ai_layer(self):
        recorded = [{"id": "a", "photo": None}, {"id": "b", "photo": "http://p"}]
        ports = [{"name": "rows", "type": "any", "schema": shape.infer(recorded)}]
        s = capability.schema_from_ports(ports)
        photo = s["properties"]["rows"]["items"]["properties"]["photo"]
        self.assertEqual(photo["type"], ["string", "null"])
        self.assertIn("photo", s["properties"]["rows"]["items"]["required"])

        self.assertEqual(shape.validate(shape.infer(recorded), [{"id": "c", "photo": None}]), [])
        self.assertNotIn("x-nullable", str(s))

class GateHoldsNestedShapeTest(unittest.TestCase):
    def test_a_record_port_with_a_schema_is_checked_inside(self):
        p = {"name": "who", "type": "record",
             "schema": shape.infer({"name": "u", "karma": 1})}
        probs = port_checks.check_ports([p], {"who": {"name": "u", "karma": "lots"}},
                                        require_all=True)
        self.assertEqual(len(probs), 1)
        self.assertIn("karma", probs[0]["problem"])
        self.assertIn("should be a number", probs[0]["problem"])

    def test_a_list_leads_with_the_count_and_names_the_rows(self):
        p = {"name": "posts", "label": "Posts", "type": "any",
             "schema": posts_schema()}
        rows = POSTS + [{"title": "x"}, {"title": "y"}, {"title": "z"}, {"title": "w"}]
        probs = port_checks.check_ports([p], {"posts": rows}, require_all=True)
        line = probs[0]["problem"]
        self.assertIn("4 of 6 items", line)
        self.assertIn("items 3, 4, 5 and 1 more", line)
        self.assertEqual(probs[0]["failing_items"], [2, 3, 4, 5])
        self.assertTrue(probs[0]["detail"])

    def test_empty_required_text_output_halts_optional_passes(self):
        probs = port_checks.check_ports([{"name": "note", "type": "text"}],
                                        {"note": ""}, require_all=True)
        self.assertEqual(len(probs), 1)
        self.assertIn("empty", probs[0]["problem"])
        probs = port_checks.check_ports([{"name": "note", "type": "text",
                                          "optional": True}],
                                        {"note": ""}, require_all=True)
        self.assertEqual(probs, [])

        self.assertEqual(port_checks.check_ports([{"name": "note", "type": "text"}],
                                                 {"note": ""}), [])

class DerivationTest(unittest.TestCase):
    def test_derive_port_schema_rules(self):
        from agent import node_tools
        port = {"name": "posts", "type": "any"}
        self.assertEqual(node_tools.derive_port_schema(port, POSTS), "derived")
        self.assertIn("author", port["schema"]["items"]["properties"])

        self.assertIsNone(node_tools.derive_port_schema(port, [POSTS[0]]))

        self.assertEqual(node_tools.derive_port_schema(
            port, [dict(POSTS[0], score="9", extra=1)]), "widened")
        self.assertEqual(port["schema"]["items"]["properties"]["score"], {})
        self.assertNotIn("extra", port["schema"]["items"]["required"])

        self.assertIsNone(node_tools.derive_port_schema({"name": "n", "type": "number"}, 3))

    def test_nonempty_expectation_derives_and_may_be_empty_clears_it(self):
        from agent import node_tools
        port = {"name": "posts", "type": "any"}
        node_tools.derive_port_schema(port, POSTS)
        self.assertTrue(port["schema"].get("x-nonempty"))
        quiet = {"name": "posts", "type": "any", "may_be_empty": True}
        node_tools.derive_port_schema(quiet, POSTS)
        self.assertNotIn("x-nonempty", quiet["schema"])

    def test_empty_output_flags_at_the_producer_only(self):
        port = {"name": "posts", "type": "any"}
        from agent import node_tools
        node_tools.derive_port_schema(port, POSTS)
        probs = port_checks.check_ports([port], {"posts": []}, require_all=True)
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].get("empty_output"), probs)

        self.assertEqual(port_checks.check_ports([port], {"posts": []}), [])

    def test_vanished_fields_is_output_side_only(self):
        from agent import node_tools
        port = {"name": "posts", "type": "any"}
        node_tools.derive_port_schema(port, POSTS)
        rows = [{"title": f"t{i}", "url": "u", "score": 1,
                 "author": {"name": "a", "karma": 1}, "when": "2026-01-01"}
                for i in range(6)]
        for r in rows:
            r.pop("url")
        out = port_checks.check_ports([port], {"posts": rows}, require_all=True)
        self.assertTrue(any("every" in p["problem"] for p in out), out)
        self.assertEqual(port_checks.check_ports([port], {"posts": rows}), [])

    def test_fill_from_cells_derives_on_both_seam_ends(self):
        from agent import cells, node_tools
        pid = "p_shape_fill"
        _workflow([], pid=pid)
        cells.record(pid, "Fetch posts", "write_output('posts', [...])", {},
                     {"posts": POSTS}, True, 0.1, [])
        plan = {"nodes": [
            {"name": "Fetch posts", "type": "code",
             "outputs": [{"name": "posts", "type": "any"}]},
            {"name": "Score posts", "type": "code",
             "inputs": [{"name": "posts", "type": "any"}],
             "outputs": [{"name": "scored", "type": "any"}]}],
            "edges": [{"src": "Fetch posts", "dst": "Score posts"}]}
        node_tools.fill_from_cells(pid, plan["nodes"])
        prod = plan["nodes"][0]["outputs"][0]
        self.assertEqual(prod["schema"]["type"], "array")
        node_tools.derive_seam_types(pid, plan)
        cons = plan["nodes"][1]["inputs"][0]
        self.assertEqual(cons.get("schema"), prod["schema"])

class TheRowNotTheErrorTest(unittest.TestCase):
    def _run(self, output, pid):
        from unittest import mock

        from runtime import env_checks, executor, run_state
        p = _workflow([{"id": "n_c", "name": "Fetch", "type": "code",
                       "config": {"code": "x"},
                       "inputs": [],
                       "outputs": [{"name": "posts", "type": "any",
                                    "schema": posts_schema()}],
                       "tests": []}], pid=pid)
        with mock.patch.object(executor, "_execute", return_value=(output, False)), \
             mock.patch.object(env_checks, "check_all_nodes", return_value=[]), \
             mock.patch.object(env_checks, "check_node", return_value=[]):
            res = executor.run_workflow(p, {})
        if res.get("run_id"):
            run_state.finish(res["run_id"])
        return res

    def test_the_halt_carries_every_offending_row(self):
        bad = 15
        rows = POSTS + [{"title": f"bad {i}"} for i in range(bad)]
        res = self._run({"posts": rows}, "p_offend1")
        self.assertEqual(res["reason"], "output-check-failed (standard)")
        off = res["offending"]
        self.assertEqual(off[0]["port"], "posts")
        self.assertEqual(off[0]["total"], bad)
        self.assertEqual(off[0]["of"], len(rows))
        self.assertEqual(len(off[0]["rows"]), bad)
        first = off[0]["rows"][0]
        self.assertEqual(first["item"], 3)
        self.assertEqual(first["value"], {"title": "bad 0"})
        self.assertTrue(any("author" in q for q in first["problems"]))

        self.assertIn(f"{bad} of {len(rows)} items",
                      " ".join(res["verdict"]["standard_output_check"]))

    def test_the_brief_renders_them(self):
        from agent import orchestrator
        brief = orchestrator._offending_brief({"offending": [
            {"port": "posts", "total": 2, "of": 5,
             "rows": [{"item": 3, "value": {"title": "bad"},
                       "problems": ["item 3 > author is missing"]}]}]})
        self.assertIn("2 of 5 items", brief)
        self.assertIn('{"title": "bad"}', brief)
        self.assertEqual(orchestrator._offending_brief({}), "")

class ThreeExitsTest(unittest.TestCase):
    def _workflow_with_issue(self, pid):
        p = _workflow([{"id": "n_c", "name": "Fetch", "type": "code",
                       "config": {"code": "x"}, "inputs": [],
                       "outputs": [{"name": "posts", "type": "any",
                                    "schema": posts_schema()}],
                       "tests": []}], pid=pid)
        p["tickets"] = [{"id": "tkt_5ae001", "status": "open", "node_id": "n_c",
                         "run_id": "run_s", "reason": "output-check-failed (standard)",
                         "verdict": {}, "notes": "",
                         "offending": [{"port": "posts", "total": 1, "of": 3, "rows": [
                             {"item": 3, "value": {"title": "t", "url": "u",
                                                   "score": 1, "when": "2026-01-01"},
                              "problems": ["item 3 > author is missing"]}]}]}]
        return p

    def test_close_issue_bad_data_closes_with_a_note_and_changes_nothing(self):
        from agent import node_tools
        p = self._workflow_with_issue("p_exit1")
        p["plan"] = {"nodes": [], "ticket_id": "tkt_5ae001"}
        turnstate.of(p).fix_issue_id = "tkt_5ae001"
        r = node_tools.tool_close_issue(p, "tkt_5ae001", "bad-data",
                                        "the source sent a row with no author")
        self.assertTrue(r["ok"], r)
        t = p["tickets"][0]
        self.assertEqual((t["status"], t["resolution"]), ("closed", "bad-data"))
        self.assertIn("no author", t["resolution_note"])
        self.assertNotIn("ticket_id", p["plan"])
        self.assertIsNone(turnstate.of(p).fix_issue_id)

        p2 = self._workflow_with_issue("p_exit1b")
        self.assertIn("error", node_tools.tool_close_issue(p2, "tkt_5ae001", "meh", "x"))
        self.assertIn("error", node_tools.tool_close_issue(p2, "tkt_5ae001", "bad-data", ""))
        p2["tickets"][0]["status"] = "ready"
        self.assertIn("error", node_tools.tool_close_issue(p2, "tkt_5ae001", "bad-data", "x"))

    def test_the_brief_names_the_three_exits(self):
        from agent import orchestrator
        p = self._workflow_with_issue("p_exit2")
        brief = orchestrator.issue_brief_for(p, "Fix it. (issue tkt_5ae001)")

        self.assertIn("SAME THREE EXITS", brief)
        self.assertIn("close_issue(resolution='bad-data'", brief)
        self.assertIn("BAD DATA (reject at the edge)", brief)
        self.assertIn("CHANGE THE WORKFLOW (widen the contract)", brief)
        self.assertIn("FIXED ELSEWHERE (not this workflow's job)", brief)
        self.assertIn("1 of 3 items", brief)

        self.assertIn("FIX WHAT THE ERROR NAMES", brief)

        self.assertIn("CHANGE EVERYTHING FIRST, PROVE ONCE", brief)

    def test_a_widened_shape_is_stamped_on_the_fix_plan(self):
        from agent import node_tools
        p = self._workflow_with_issue("p_exit3")

        plan = {"ticket_id": "tkt_5ae001", "nodes": [
            {"id": "n_c", "name": "Fetch", "type": "code",
             "outputs": [{"name": "posts", "type": "any",
                          "schema": posts_schema(),
                          "item_fields": [{"name": "title", "type": "text", "required": True},
                                          {"name": "author", "type": "record",
                                           "optional": True}]}]}]}
        node_tools.derive_port_schema(plan["nodes"][0]["outputs"][0], POSTS)
        w = node_tools.contract_widened(p, plan)
        self.assertEqual(w, [{"step": "Fetch", "port": "posts", "accepted": 1, "of": 1}])

        plan["nodes"][0]["outputs"][0].pop("item_fields")
        plan["nodes"][0]["outputs"][0]["schema"] = posts_schema()
        self.assertEqual(node_tools.contract_widened(p, plan), [])

class BatchPolicyTest(unittest.TestCase):
    ROWS = POSTS + [{"title": "no author"}]

    def _run(self, policy, output, pid, extra_out=None):
        from unittest import mock

        from runtime import env_checks, executor, run_state
        from storage import deliverables
        outs = [{"name": "posts", "type": "any", "schema": posts_schema()}]
        if extra_out:
            outs.append(extra_out)
        cfg = {"code": "x"}
        if policy:
            cfg["on_invalid_items"] = policy
        p = _workflow([{"id": "n_c", "name": "Fetch", "type": "code",
                       "config": cfg, "inputs": [], "outputs": outs, "tests": []},
                      {"id": "n_d", "name": "Count", "type": "code",
                       "config": {"code": "y"},
                       "inputs": [{"name": "posts", "type": "any"}],
                       "outputs": [{"name": "n", "type": "number"}], "tests": []}],
                     pid=pid)
        p["edges"] = [{"src": "n_c", "dst": "n_d"}]
        seen = {}
        def fake_exec(node, inputs, *a, **k):
            if node["id"] == "n_c":
                return (dict(output), False)
            seen["posts"] = inputs.get("posts")
            return ({"n": len(inputs.get("posts") or [])}, False)
        with mock.patch.object(executor, "_execute", side_effect=fake_exec), \
             mock.patch.object(env_checks, "check_all_nodes", return_value=[]), \
             mock.patch.object(env_checks, "check_node", return_value=[]):
            res = executor.run_workflow(p, {})
        manifest = None
        if res.get("status") == "completed":
            manifest = next((m for m in deliverables.list_runs(pid)
                             if m["run_id"] == res["run_id"]), None)
        if res.get("run_id"):
            run_state.finish(res["run_id"])
        return res, seen, manifest

    def test_stop_is_the_default_and_halts(self):
        res, seen, _ = self._run(None, {"posts": self.ROWS}, "p_pol1")
        self.assertEqual(res["status"], "halted")
        self.assertNotIn("posts", seen)

    def test_proceed_sets_the_bad_rows_aside_and_the_rest_cross(self):
        res, seen, manifest = self._run("proceed", {"posts": self.ROWS}, "p_pol2")
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(len(seen["posts"]), 2)
        self.assertTrue(all("author" in r for r in seen["posts"]))
        sa = res["set_aside"]["n_c"]
        self.assertEqual(sa["policy"], "proceed")
        self.assertEqual((sa["ports"][0]["total"], sa["ports"][0]["of"]), (1, 3))
        self.assertEqual(sa["ports"][0]["rows"][0]["value"], {"title": "no author"})

        self.assertEqual(manifest["set_aside"][0]["name"], "Fetch")
        self.assertEqual(manifest["set_aside"][0]["ports"][0]["total"], 1)

    def test_log_is_the_same_without_an_issue_owed(self):
        res, seen, _ = self._run("log", {"posts": self.ROWS}, "p_pol3")
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(res["set_aside"]["n_c"]["policy"], "log")

    def test_a_missing_port_still_halts_under_proceed(self):
        res, _, _ = self._run("proceed", {"posts": self.ROWS}, "p_pol4",
                              extra_out={"name": "note", "type": "text"})
        self.assertEqual(res["status"], "halted")
        self.assertIn("not produced", " ".join(res["verdict"]["standard_output_check"]))

    def test_the_freeze_syncs_the_policy_and_the_gap_catches_a_typo(self):
        from agent import plan_logic
        gaps = plan_logic._step_content_gaps("Fetch", {"type": "code",
                                                       "on_invalid_items": "procede"})
        self.assertTrue(any("on_invalid_items" in g for g in gaps))
        self.assertEqual(plan_logic._step_content_gaps("Fetch", {"type": "code",
                                                                  "on_invalid_items": "log"}), [])

class FieldTiersTest(unittest.TestCase):
    def test_the_gate_flags_a_vanished_expected_field_and_no_policy_sets_it_aside(self):
        p = {"name": "rows", "label": "Rows", "type": "any",
             "schema": shape.infer([{"id": 1, "email": "a"}, {"id": 2, "email": "b"}])}
        many = [{"id": i} for i in range(shape.VANISH_MIN_ITEMS + 2)]
        probs = port_checks.check_ports([p], {"rows": many}, require_all=True)
        self.assertEqual(len(probs), 1)
        self.assertIn("missing from every one of the", probs[0]["problem"])
        self.assertIn("'email'", probs[0]["problem"])
        self.assertNotIn("failing_items", probs[0])

    def test_must_have_summary_only_when_there_is_a_split(self):
        from agent import node_tools
        split = shape.apply_item_fields(
            shape.infer([{"name": "a", "email": "e", "title": "t"}]),
            [{"name": "name", "type": "text", "required": True},
             {"name": "email", "type": "text", "required": True}])
        p = _workflow([{"id": "n1", "name": "Find prospects", "type": "code",
                       "config": {}, "inputs": [],
                       "outputs": [{"name": "prospects", "type": "any", "schema": split},
                                   {"name": "count", "type": "number"}],
                       "tests": []}], pid="p_tiers1")
        p["deliverables"] = [{"node": "n1", "port": "prospects", "label": "Prospects"},
                             {"node": "n1", "port": "count", "label": "Count"}]
        out = node_tools.must_have_summary(p)
        self.assertEqual(out, [{"label": "Prospects", "must_have": ["email", "name"],
                                "may_be_missing": ["title"]}])

        p["nodes"][0]["outputs"][0]["schema"] = shape.apply_item_fields(
            shape.infer([{"name": "a"}]), [{"name": "name", "type": "text", "required": True}])
        self.assertEqual(node_tools.must_have_summary(p), [])
        p["nodes"][0]["outputs"][0]["schema"] = shape.infer([{"name": "a", "title": "t"}])
        self.assertEqual(node_tools.must_have_summary(p), [])

    def test_the_built_card_says_what_must_be_there_only_when_it_splits(self):
        from agent import steps
        split = shape.apply_item_fields(
            shape.infer([{"name": "a", "title": "t"}]),
            [{"name": "name", "type": "text", "required": True}])
        p = _workflow([{"id": "n1", "name": "Find prospects", "type": "code",
                       "config": {}, "inputs": [],
                       "outputs": [{"name": "prospects", "type": "any", "schema": split}],
                       "tests": []}], pid="p_tiers2")
        p["deliverables"] = [{"node": "n1", "port": "prospects", "label": "Prospects"}]
        p["plan"] = {"summary": "Finds prospects.",
                     "_must_have": steps.must_have_summary(p)}
        card = steps.opening_card_payload(p, p["plan"], "built")
        self.assertEqual(card["must_have"][0]["must_have"], ["name"])
        p["deliverables"] = []
        self.assertEqual(steps.must_have_summary(p), [])

if __name__ == "__main__":
    unittest.main()
