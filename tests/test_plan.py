# Tests: plan seams: save_plan validation, draft persistence, opt-in proposals
from __future__ import annotations

import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from agent import orchestrator
from agent import node_tools
from agent import turnstate

def _with_intent(p):
    node_tools.tool_save_intent(p, "Test workflow purpose.", ["fact one"], ["chat"])
    return p

class IntentTest(unittest.TestCase):
    def test_save_intent_requires_summary(self):
        p = _workflow()
        self.assertIn("error", node_tools.tool_save_intent(p, "  "))

    def test_save_intent_persists(self):
        p = _with_intent(_workflow())
        self.assertEqual(p["intent"]["facts"], ["fact one"])
        self.assertTrue(p["intent"]["updated"] > 0)

    def test_save_plan_refused_without_intent(self):
        p = _workflow()
        r = node_tools.tool_save_plan(p, {"summary": "Do it.", "nodes": [
            {"name": "parse", "type": "code"}]})
        self.assertFalse(r["ok"])
        self.assertTrue(any("save_intent" in e for e in r["errors"]))

class PlanTest(unittest.TestCase):
    def test_save_plan_refuses_only_what_it_cannot_store(self):
        p = _with_intent(_workflow())
        bad = node_tools.tool_save_plan(p, {"summary": "", "nodes": [
            {"name": "send", "type": "connector"}],
            "edges": [{"src": "send", "dst": "ghost"}]})
        self.assertFalse(bad["ok"])
        self.assertEqual(len(bad["errors"]), 2)
        ok = node_tools.tool_save_plan(p, {"summary": "Do it.", "nodes": [
            {"name": "send", "type": "connector",
             "outputs": [{"name": "sent", "type": "boolean"}]}]})
        self.assertTrue(ok["ok"], ok)
        self.assertTrue(any("what it changes there" in g
                            for g in ok["design_gaps"]), ok)
        self.assertIn("design gaps",
                      node_tools.tool_build_workflow(p).get("error", ""))

    def test_save_plan_persists_draft_with_proposal_ids(self):
        p = _with_intent(_workflow())
        r = node_tools.tool_save_plan(p, {
            "summary": "Do the thing.",
            "nodes": [{"name": "parse", "type": "code",
                       "outputs": [{"name": "x", "type": "number"}]}],
            "proposals": [{"kind": "observation", "suggestion": "tidy up"}]})
        self.assertTrue(r["ok"])
        self.assertEqual(p["plan"]["status"], "draft")
        self.assertEqual(p["plan"]["proposals"][0]["id"], "prop_0")

    def test_an_input_nothing_produces_becomes_a_setting(self):
        p = _with_intent(_workflow())
        r = node_tools.tool_save_plan(p, {
            "summary": "Do the thing.",
            "nodes": [{"name": "parse", "type": "code", "code_sketch": "parse it",
                       "inputs": [{"name": "raw", "type": "text"}],
                       "outputs": [{"name": "x", "type": "text"}]}]})
        self.assertTrue(r["ok"])
        self.assertNotIn("design_gaps", r)
        self.assertIn("raw", [v["name"] for v in p.get("variables") or []])

    def test_save_plan_surfaces_design_gaps(self):
        p = _with_intent(_workflow())
        r = node_tools.tool_save_plan(p, {
            "summary": "Do the thing.",
            "nodes": [{"name": "parse", "type": "code", "code_sketch": "parse it",
                       "inputs": [{"name": "raw", "type": "text"}],
                       "outputs": []}]})
        self.assertTrue(r["ok"])
        self.assertIn("design_gaps", r)
        self.assertTrue(any("produces no output" in g for g in r["design_gaps"]))

    def test_coherent_plan_has_no_design_gaps(self):
        p = _with_intent(_workflow())
        r = node_tools.tool_save_plan(p, {
            "summary": "Do the thing.",
            "nodes": [{"name": "parse", "type": "code", "code_sketch": "make x",
                       "outputs": [{"name": "x", "type": "text"}]}]})
        self.assertTrue(r["ok"])
        self.assertNotIn("design_gaps", r)

    def test_proposals_are_opt_in(self):
        plan = {"summary": "s", "proposals": [{"id": "prop_0", "suggestion": "x"}]}
        self.assertNotIn("proposals", orchestrator._filter_proposals(plan, []))
        kept = orchestrator._filter_proposals(plan, ["prop_0"])
        self.assertEqual(len(kept["proposals"]), 1)

class BuildProgressTest(unittest.TestCase):
    def test_phase_of_extracts_the_step(self):
        for txt in ('adding a step: "Extract emails"',
                    'writing the logic for "Extract emails"',
                    'testing "Extract emails"',
                    'trying "Extract emails" on a sample'):
            self.assertEqual(orchestrator._phase_of({"type": "tool", "text": txt}),
                             "Extract emails", txt)

    def test_phase_of_none_for_noise(self):
        for ev in ({"type": "tool", "text": "looking over the workflow"},
                   {"type": "tool", "text": "purpose audit: checking the build"},
                   {"type": "tool", "text": 'saving reference data: "cats.csv"'},
                   {"type": "validation", "status": "pass"},
                   {"type": "delta", "text": 'has a "quote" in prose'}):
            self.assertIsNone(orchestrator._phase_of(ev), ev)

    def test_emitter_rolls_up_and_collects_steps(self):
        seen = []
        emit0, steps = orchestrator._build_progress_emitter(seen.append)
        for txt in ('adding a step: "Paste text"',
                    'defining what "Paste text" takes in and gives out',
                    'writing the logic for "Extract emails"',
                    'testing "Extract emails"'):
            emit0({"type": "tool", "text": txt})

        phases = [e["text"] for e in seen if e.get("type") == "phase"]
        self.assertEqual(phases, ['Finishing "Paste text"', 'Finishing "Extract emails"'])
        self.assertEqual(steps, ["Paste text", "Extract emails"])
        self.assertEqual(sum(1 for e in seen if e.get("type") == "tool"), 4)

    def test_tool_node_targets_a_single_step(self):
        proj = {"nodes": [{"id": "n1", "name": "Parse"}, {"id": "n2", "name": "Send"}]}
        tn = lambda name, inp: orchestrator._tool_node(proj, name, inp)

        self.assertEqual(tn("mcp__cryogram__validate_node", {"node_id": "n2"}), "Send")

        self.assertEqual(tn("mcp__cryogram__create_node", {"name": "New step"}), "New step")

        self.assertIsNone(tn("mcp__cryogram__connect", {"src": "n1", "dst": "n2"}))
        self.assertIsNone(tn("mcp__cryogram__validate_node", {"node_ids": ["n1", "n2"]}))
        self.assertIsNone(tn("Bash", {"command": "ls"}))

    def test_emitter_prefers_the_structured_node(self):
        seen = []
        emit0, steps = orchestrator._build_progress_emitter(seen.append)
        emit0({"type": "tool", "text": 'linking "A" and "B"', "node": None})
        emit0({"type": "tool", "text": "writing the logic", "node": "A"})
        phases = [e["text"] for e in seen if e.get("type") == "phase"]
        self.assertEqual(phases, ['Finishing "A"'])
        self.assertEqual(steps, ["A"])

class InvestigateMessageTest(unittest.TestCase):
    def test_includes_node_code_and_successes(self):
        node = {"name": "X", "type": "code",
                "inputs": [{"name": "a"}], "outputs": [{"name": "b"}],
                "config": {"code": "def f(a):\n    return a + 1\n"}}
        ticket = {"id": "t1", "node_id": "n1", "reason": "boom", "verdict": {}}
        case = {"case_id": "c1", "inputs": {"a": 1}}
        successes = [{"case_id": "s1", "inputs": {"a": 2}, "output": {"b": 3}}]
        msg = orchestrator._investigate_message(ticket, node, case, successes)
        self.assertIn("return a + 1", msg)
        self.assertIn("s1", msg)
        self.assertIn("no need to read_node", msg)
        self.assertIn("boom", msg)

class PlainLanguageToolLinesTest(unittest.TestCase):
    _SHELL = ("bash", "shell", "cli", "terminal", "command", "running:", "script")

    def test_builtin_activity_lines_never_leak_shell_or_tool_names(self):
        proj = {"nodes": []}
        inp = {"command": "ls -la /tmp", "url": "http://x", "query": "q",
               "file_path": "/tmp/f"}
        for t in ("Bash", "Read", "Glob", "Grep", "Write", "Edit",
                  "WebFetch", "WebSearch", "Task", "Skill"):
            line = orchestrator._describe_tool(proj, t, inp).lower()
            for bad in self._SHELL:
                self.assertNotIn(bad, line, f"{t}: {line!r}")
            self.assertNotIn(t.lower(), line)

    def test_scratch_cell_names_never_reach_the_dock(self):
        proj = {"nodes": [{"id": "n1", "name": "Fetch posts"}],
                "plan": {"nodes": [{"name": "Triage each post"}]}}
        for tool in ("mcp__cryogram__run_cell", "mcp__cryogram__run_ai_step"):
            line = orchestrator._describe_tool(proj, tool,
                                            {"name": "probe model list"})
            self.assertEqual(line, "double-checking a detail")

        line = orchestrator._describe_tool(proj, "mcp__cryogram__run_cell",
                                        {"name": "fetch_posts"})
        self.assertIn("Fetch posts", line)
        line2 = orchestrator._describe_tool(proj, "mcp__cryogram__run_ai_step",
                                         {"name": "Triage each post"})
        self.assertIn("Triage each post", line2)

    def test_the_batch_read_names_the_steps_not_a_step(self):
        proj = {"nodes": [{"id": "n1", "name": "Fetch posts"},
                          {"id": "n2", "name": "Triage each post"}]}
        line = orchestrator._describe_tool(proj, "mcp__cryogram__read_node",
                                        {"node_ids": ["n1", "n2"]})
        self.assertNotIn("a step", line)
        self.assertIn("Fetch posts", line)
        self.assertIn("Triage each post", line)

        many = orchestrator._describe_tool(
            proj, "mcp__cryogram__read_node",
            {"node_ids": ["n1", "n2", "n3", "n4", "n5"]})
        self.assertEqual(many, "reading 5 steps")
        self.assertNotIn(
            "a step",
            orchestrator._describe_tool(proj, "mcp__cryogram__read_node",
                                     {"node_ids": ["nope"]}))

    def test_load_skill_lines_are_plain(self):
        proj = {"nodes": []}
        cases = {
            "google-sheets": "reading up on Google Sheets",
            "browser": "reading up on browser-run websites",
            "browser-login": "reading up on website logins",
            "microsoft-teams": "reading up on Microsoft Teams",
            "02-a-message-arrives": "checking my notes on the approach",
            "62-ai-practice": "checking my notes on the approach",
            "learned-feed-quirks": "checking my notes on the approach",
        }
        for sid, want in cases.items():
            line = orchestrator._describe_tool(proj, "mcp__cryogram__load_skill",
                                            {"id": sid})
            self.assertEqual(line, want, sid)

    def test_activity_lines_never_name_internal_roles(self):
        proj = {"nodes": []}
        names = (list(orchestrator._TOOL_LINES.keys())
                 + ["build_workflow", "resolve_issue"]
                 + [f"mcp__cryogram__{n}" for n in ("build_workflow", "resolve_issue",
                                                    "save_plan", "report_audit")]
                 + ["Bash", "Read", "WebFetch", "Task", "Skill"])
        for t in names:
            low = orchestrator._describe_tool(proj, t, {}).lower()
            for leak in ("freeze", "auditor", "plan card", "build this"):
                self.assertNotIn(leak, low, f"{t}: {low!r}")

if __name__ == "__main__":
    unittest.main()

class InformedRetryTest(unittest.TestCase):
    def test_plain_findings_translate_the_finding_classes(self):
        from agent import orchestrator
        plain = orchestrator.plain_findings([
            'planned edge "A" -> "B" was not wired',
            'edge "A" -> "B": routing condition differs from the plan',
            "no step's output is kept for the user - declare the run's results with set_deliverables (guided by the intent)",
            'node "X" was built but is not in the plan',
        ])
        self.assertIn("the connections between steps don't match the plan yet", plain)
        self.assertIn("which results each run keeps for you isn't set yet", plain)
        self.assertIn("a leftover step needs removing", plain)
        self.assertEqual(len(plain), 3)

class StuckPlanTest(unittest.TestCase):
    def test_the_gates_pass_and_the_build_is_armed(self):
        p = _workflow([], pid="p_stuck")
        p["plan_approved_ts"] = 1.0
        p["plan"] = {"status": "draft", "summary": "s",
                     "nodes": [{"name": "x", "type": "code",
                                "code": "write_output('y', 1)"}]}
        r = node_tools.tool_build_workflow(p)
        self.assertTrue(r["ok"], r)
        self.assertIsNotNone(turnstate.of(p).build_now)

class AssertLanguageTest(unittest.TestCase):
    def test_save_plan_rejects_what_set_tests_rejects(self):
        p = _with_intent(_workflow())
        for bad in ("output.rss_text contains '<rss'",
                    "output.headlines[0]['title'] == 'x'"):
            r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
                {"name": "Extract", "type": "code", "code_sketch": "extracts",
                 "outputs": [{"name": "headlines", "type": "list"}],
                 "tests": [{"name": "t", "inputs": {}, "expect": "ok",
                            "asserts": [bad]}]}]})
            self.assertTrue(r["ok"], bad)
            self.assertTrue(r.get("design_gaps"), bad)

        self.assertTrue(any("bare names" in g for g in r["design_gaps"]),
                        r["design_gaps"])
        self.assertIn("design gaps",
                      node_tools.tool_build_workflow(p).get("error", ""))

    def test_bare_name_asserts_pass_both_gates(self):
        p = _with_intent(_workflow())
        r = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Extract", "type": "code",
             "code": "write_output('headlines', [1, 2, 3])",
             "outputs": [{"name": "headlines", "type": "list"}],
             "tests": [{"name": "t", "inputs": {}, "expect": "ok",
                        "asserts": ["len(headlines) == 3"]}]}]})
        self.assertTrue(r["ok"], r)

class DescriptionAutoFillTest(unittest.TestCase):
    def test_blank_fills_and_set_never_overwrites(self):
        p = _workflow()
        node_tools.tool_save_intent(
            p, "Checks contact emails and splits good from bad. Runs on a CSV.",
            description="Sorts a contact list into valid and invalid emails")
        self.assertEqual(p["description"],
                         "Sorts a contact list into valid and invalid emails")
        node_tools.tool_save_intent(p, "A totally different purpose now.",
                                    description="Something else")
        self.assertEqual(p["description"],
                         "Sorts a contact list into valid and invalid emails")

        p2 = _workflow()
        node_tools.tool_save_intent(
            p2, "Checks contact emails and splits good from bad. Runs on a CSV.")
        self.assertEqual(p2["description"],
                         "Checks contact emails and splits good from bad")

        p3 = _workflow()
        p3["description"] = "The user's own words"
        node_tools.tool_save_intent(p3, "Some intent.")
        self.assertEqual(p3["description"], "The user's own words")

class HarnessRobustnessTest(unittest.TestCase):
    def test_regression_replays_are_capped(self):
        from agent import receipts
        self.assertEqual(receipts._REPLAY_CAP, 5)

class InteractionExpiryTest(unittest.TestCase):
    def test_resolve_after_expiry_reports_false_not_500(self):
        from agent import interactions
        iid = interactions.create_sync("ask", {"workflow_id": "p_x",
                                               "question": "?"})
        self.assertIsNone(interactions.wait_sync(iid, timeout=0.01))
        self.assertFalse(interactions.resolve(iid, "yes"))
        self.assertEqual(interactions.pending("p_x"), [])

class ChangeRailsTest(unittest.TestCase):
    def test_investigate_brief_carries_the_change_rails(self):
        msg = orchestrator._investigate_message(
            {"id": "t1", "reason": "x", "verdict": {}}, None, None)
        self.assertIn("DELTA", msg)
        self.assertIn("amend:true", msg)
        self.assertIn("never ask HOW", msg)
        self.assertIn("never re-ask", msg.lower())
        self.assertIn("FINISH", msg)

    def test_the_rails_are_shared_verbatim(self):
        self.assertIn(orchestrator._CHANGE_RAILS,
                      orchestrator._investigate_message(
                          {"id": "t", "reason": "r", "verdict": {}}, None, None))

class LabelUnifyTest(unittest.TestCase):
    def test_a_keys_label_is_unified_and_fills_empties(self):
        from agent import node_tools
        p = {"id": "p_uni", "nodes": [
            {"id": "a", "inputs": [],
             "outputs": [{"name": "vg", "label": "VG headlines"}]},
            {"id": "b", "inputs": [{"name": "vg", "label": "VG headlines"}],
             "outputs": []},
            {"id": "c", "inputs": [{"name": "vg", "label": "Headlines from VG"}],
             "outputs": []},
            {"id": "d", "inputs": [{"name": "vg", "label": ""}], "outputs": []}],
            "variables": [{"name": "teams_webhook_url", "label": "Teams webhook"}]}
        changed = node_tools.unify_field_labels(p)
        self.assertGreater(changed, 0)
        labs = {p["nodes"][0]["outputs"][0]["label"],
                p["nodes"][1]["inputs"][0]["label"],
                p["nodes"][2]["inputs"][0]["label"],
                p["nodes"][3]["inputs"][0]["label"]}
        self.assertEqual(labs, {"VG headlines"})
        self.assertEqual(p["variables"][0]["label"], "Teams webhook")

    def test_no_labels_left_alone(self):
        from agent import node_tools
        p = {"id": "p_uni2", "nodes": [
            {"id": "a", "inputs": [], "outputs": [{"name": "x", "label": ""}]}],
            "variables": []}
        self.assertEqual(node_tools.unify_field_labels(p), 0)

class AutofixTest(unittest.TestCase):
    def test_missing_deliverables_autofixed_from_terminal_nodes(self):
        p = _workflow([
            {"id": "n1", "name": "Fetch", "type": "code",
             "config": {"code": "write_output('rate', 1)"}, "inputs": [],
             "outputs": [{"name": "rate", "type": "number"}], "tests": []},
            {"id": "n2", "name": "Store", "type": "code",
             "config": {"code": "write_output('saved_row', read_input('rate'))"},
             "inputs": [{"name": "rate", "type": "number"}],
             "outputs": [{"name": "saved_row", "type": "text"}], "tests": []}],
            pid="p_autofix")
        p["edges"] = [{"src": "n1", "dst": "n2", "when": ""}]
        changed = orchestrator._autofix_findings(
            p, {}, ["no step's output is kept for the user - declare..."],
            lambda ev: None)
        self.assertTrue(changed)
        kept = {(d.get("node"), d.get("port")) for d in p.get("deliverables") or []}
        self.assertIn(("n2", "saved_row"), kept)
        self.assertNotIn(("n1", "rate"), kept)

class RunSurfaceTest(unittest.TestCase):
    def test_digest_names_gates_inputs_and_secrets(self):
        p = {"id": "p_rs", "variables": [
                {"name": "teams_webhook_url", "secret": True, "value": True}],
             "edges": [{"src": "n_in", "dst": "n_send", "when": ""}],
             "nodes": [
                {"id": "n_in", "name": "Type your message", "type": "user-input",
                 "outputs": [{"name": "message", "type": "text"}], "inputs": []},
                {"id": "n_send", "name": "Send message to Teams",
                 "type": "connector", "read_only": False,
                 "inputs": [{"name": "message", "type": "text"}],
                 "outputs": [{"name": "ok", "type": "boolean"}]}]}
        d = orchestrator._run_surface(p)
        self.assertIn("never ask the user about these", d)
        self.assertIn('"Type your message"', d)
        self.assertIn("asks approval each run", d)
        self.assertIn("never re-collect: teams_webhook_url", d)

class ExplorerTurnTest(unittest.TestCase):
    def test_agent_turn_is_a_thin_pass_through(self):
        import unittest.mock as m
        from agent import loop
        p = _workflow([], pid="p_thin")
        with m.patch.object(loop, "run_turn",
                            return_value={"content": "hi", "kind": "text"}) as rt:
            reply = orchestrator._agent_turn(p, "hello", emit=lambda e: None)
        self.assertEqual(reply["content"], "hi")
        self.assertTrue(rt.called)

class BuiltStepsAreFinishedTest(unittest.TestCase):
    def test_the_context_carries_no_proof_marker(self):
        from agent import cells, orchestrator
        from tests._bootstrap import workflow as _workflow
        code = "def f():\n    return 1\n"
        n1 = {"id": "n_p1", "name": "Tried step", "type": "code",
              "config": {"code": code}, "inputs": [],
              "outputs": [{"name": "v", "type": "number"}], "tests": []}
        n2 = {"id": "n_p2", "name": "Untried step", "type": "code",
              "config": {"code": "def g():\n    return 2\n"}, "inputs": [],
              "outputs": [{"name": "w", "type": "number"}], "tests": []}
        p = _workflow([n1, n2], pid="p_proofctx")
        cells.record("p_proofctx", "Tried step", code, {}, {"v": 1}, True, 0.1, [])
        ctx = orchestrator._workflow_context(p)
        self.assertIn("Tried step (n_p1) [code] - -> v", ctx)
        self.assertIn("Untried step (n_p2) [code] - -> w", ctx)
        for word in ("proven", "untested", "re-prove"):
            self.assertNotIn(word, ctx)

    def test_a_built_step_whose_code_drifted_is_still_finished(self):
        from agent import cells, orchestrator, node_tools
        from tests._bootstrap import workflow as _workflow
        code = "def f():\n    return 'new'\n"
        n = {"id": "n_p3", "name": "Drifted step", "type": "code",
             "config": {"code": code}, "inputs": [],
             "outputs": [{"name": "v", "type": "text"}], "tests": []}
        p = _workflow([n], pid="p_proofctx2")
        cells.record("p_proofctx2", "Drifted step", "def f():\n    return 'old'\n",
                     {}, {"v": "old"}, True, 0.1, [])
        self.assertNotIn("untested", orchestrator._workflow_context(p))

        steps = [{"name": "Drifted step", "type": "code", "code": code,
                  "inputs": [], "outputs": [{"name": "v", "type": "text"}],
                  "tests": []}]
        self.assertEqual(node_tools.untested_steps(p, steps), [])

class IssueBriefTest(unittest.TestCase):
    def _workflow(self):
        from tests._bootstrap import workflow as _mk
        p = _mk([{"id": "n_a", "name": "Save rows", "type": "connector",
                  "config": {}, "read_only": False, "inputs": [],
                  "outputs": [{"name": "status", "type": "number"}]},
                 {"id": "n_b", "name": "Email summary", "type": "connector",
                  "config": {}, "read_only": False, "inputs": [],
                  "outputs": []}], pid="p_brief")
        p["tickets"] = [
            {"id": "tkt_aaa111", "status": "closed", "node_id": "n_a",
             "run_id": "run_x", "reason": "send-unverified", "verdict": {},
             "notes": "", "user_outputs": {"given": ["status"],
                                           "unsure": ["updated_range"]}},
            {"id": "tkt_bbb222", "status": "open", "node_id": "n_b",
             "run_id": "run_x", "reason": "node threw: KeyError",
             "verdict": {}, "notes": ""},
        ]
        return p

    def test_the_brief_threads_sibling_issues_and_user_typed_outputs(self):
        from agent import orchestrator
        brief = orchestrator.issue_brief_for(
            self._workflow(), "Please fix it. (issue tkt_bbb222)")
        self.assertIn("this run's other issues", brief)
        self.assertIn("Save rows", brief)
        self.assertIn("TYPED BY THE USER", brief)
        self.assertIn("updated_range", brief)

    def test_a_lone_issue_gets_no_sibling_section(self):
        from agent import orchestrator
        p = self._workflow()
        p["tickets"] = [p["tickets"][1]]
        brief = orchestrator.issue_brief_for(p, "Fix it. (issue tkt_bbb222)")
        self.assertNotIn("other issues", brief)
        self.assertTrue(brief)

    def test_an_ai_failure_block_renders_its_facts_and_remedies(self):
        from agent import orchestrator
        p = self._workflow()
        p["tickets"] = [
            {"id": "tkt_ccc333", "status": "open", "node_id": "n_a",
             "run_id": "run_y", "reason": "ai-timed-out", "verdict": {},
             "notes": "", "ai_failure": {
                 "mode": "timed-out", "elapsed_s": 300.2, "timeout_s": 300,
                 "max_tokens": 32768, "input_chars": 48210,
                 "batch_items": 120, "model": "claude-haiku-4-5"}}]
        brief = orchestrator.issue_brief_for(p, "Fix it. (issue tkt_ccc333)")
        self.assertIn("captured at the failure", brief)
        self.assertIn("300.2s", brief)
        self.assertIn("120 items", brief)
        self.assertIn("fewer items per call", brief)
        self.assertIn("timeout_seconds", brief)

    def test_a_ticket_without_the_block_renders_no_ai_section(self):
        from agent import orchestrator
        p = self._workflow()
        p["tickets"] = [p["tickets"][1]]
        brief = orchestrator.issue_brief_for(p, "Fix it. (issue tkt_bbb222)")
        self.assertNotIn("captured at the failure", brief)

class FlowInversionTest(unittest.TestCase):
    def _plan(self, code=True):
        step = {"name": "Extract", "type": "code",
                "outputs": [{"name": "y", "type": "text"}]}
        if code:
            step["code"] = "write_output('y', 1)"
        else:
            step["code_sketch"] = "reads it"
        return {"summary": "Do the thing.", "nodes": [step]}

    def test_proven_code_style_notes_are_suppressed(self):
        from tests._bootstrap import workflow as _mk
        p = _mk([], pid="p_fi_gate")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        SWALLOW = ("try:\n"
                   "    x = int(read_input('n'))\n"
                   "except Exception:\n"
                   "    pass\n"
                   "write_output('y', 1)\n")
        plan = {"summary": "Do.", "nodes": [
            {"name": "Fetch", "type": "code", "code": SWALLOW,
             "inputs": [{"name": "n", "type": "number"}],
             "outputs": [{"name": "y", "type": "number"}]}]}
        node_tools.tool_save_plan(p, dict(plan))
        self.assertTrue(any("swallowed" in g
                            for g in p["plan"].get("design_gaps") or []))
        node_tools.tool_run_cell(p, "Fetch", SWALLOW, {"n": 1})
        node_tools.tool_save_plan(p, dict(plan))
        self.assertEqual(p["plan"].get("design_gaps"), None)

        self.assertTrue(node_tools.build_step(p, "Fetch")["ok"])

    def test_an_untried_step_is_refused_and_names_the_way_out(self):
        from tests._bootstrap import workflow as _mk
        p = _mk([], pid="p_fi_dec")
        p["plan_approved_ts"] = 1.0
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(
            p, {"summary": "Send it.", "nodes": [
                {"name": "Post", "type": "connector",
                 "external_impact": "posts a message",
                 "code": "write_output('sent', bool(read_input('x')))",
                 "inputs": [{"name": "x", "type": "text"}],
                 "outputs": [{"name": "sent", "type": "boolean"}]}]})
        r = node_tools.tool_build_workflow(p)
        self.assertIn("not been tested", r.get("error", ""))
        self.assertIn("approval card", r.get("error", ""))
        self.assertNotIn("_pending_build", p)

    def test_the_built_card_is_the_record(self):
        from tests._bootstrap import workflow as _mk
        p = _mk([], pid="p_fi_built")
        p["plan"] = {"summary": "Do.", "nodes": [
            {"name": "Extract", "type": "code"}]}
        card = node_tools.append_plan_entry(p, p["plan"], force_new=True,
                                             head="built")
        self.assertEqual(card["payload"]["head"], "built")

class UnplacedStepTest(unittest.TestCase):
    def test_a_step_with_no_edges_is_a_design_gap(self):
        from tests._bootstrap import workflow as _mk
        p = _mk([], pid="p_unplaced")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Enter search URL", "type": "user-input",
             "per_run_reason": "a different search each run",
             "outputs": [{"name": "search_url", "type": "text"}]},
            {"name": "Fetch results", "type": "code", "code_sketch": "gets",
             "inputs": [{"name": "search_url", "type": "text"}],
             "outputs": [{"name": "raw", "type": "list"}]},
            {"name": "Parse results", "type": "code", "code_sketch": "parses",
             "inputs": [{"name": "raw", "type": "list"}],
             "outputs": [{"name": "rows", "type": "list"}]}],
            "edges": [{"src": "Fetch results", "dst": "Parse results"}]})
        gaps = p["plan"].get("design_gaps") or []
        self.assertTrue(any("not placed in the order" in g
                            and "Enter search URL" in g for g in gaps), gaps)

        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Enter search URL", "type": "user-input",
             "per_run_reason": "a different search each run",
             "outputs": [{"name": "search_url", "type": "text"}]},
            {"name": "Fetch results", "type": "code", "code_sketch": "gets",
             "inputs": [{"name": "search_url", "type": "text"}],
             "outputs": [{"name": "raw", "type": "list"}]},
            {"name": "Parse results", "type": "code", "code_sketch": "parses",
             "inputs": [{"name": "raw", "type": "list"}],
             "outputs": [{"name": "rows", "type": "list"}]}],
            "edges": [{"src": "Enter search URL", "dst": "Fetch results"},
                      {"src": "Fetch results", "dst": "Parse results"}]})
        self.assertFalse(any("not placed" in g
                             for g in p["plan"].get("design_gaps") or []))

    def test_a_single_step_plan_is_exempt(self):
        from tests._bootstrap import workflow as _mk
        p = _mk([], pid="p_unplaced2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Only step", "type": "code", "code_sketch": "does",
             "outputs": [{"name": "y", "type": "text"}]}], "edges": []})
        self.assertFalse(any("not placed" in g
                             for g in p["plan"].get("design_gaps") or []))

class UnreachableServiceMessageTest(unittest.TestCase):
    def test_a_refused_connection_is_named_not_generic(self):
        from agent import transport
        p = {"id": "p_conn_msg"}
        out = orchestrator._turn_failed(p, transport.TransportError(
            "the subscription builder failed: API Error: Unable to connect to API (ConnectionRefused...)"))
        self.assertIn("couldn't reach the AI service", out)
        self.assertIn("connection", out)

        self.assertIn("I ran into a problem",
                      orchestrator._turn_failed(p, RuntimeError("boom")))
        self.assertIn("I ran into a problem",
                      orchestrator._turn_failed(p, RuntimeError("Unable to connect")))

    def test_every_transport_error_speaks_its_own_sentence(self):
        from agent import transport
        p = {"id": "p_tr_msg"}
        out = orchestrator._turn_failed(p, transport.TransportError(
            "the Claude Agent SDK is not installed: No module named 'x'"))
        self.assertIn("Claude Agent SDK is not installed", out)
        self.assertNotIn("I ran into a problem", out)
        out = orchestrator._turn_failed(p, transport.TransportError(
            "No Builder Agent is set. Pick one in Admin, then send your message again."))
        self.assertIn("Pick one in Admin", out)
        self.assertNotIn("couldn't finish", out)

class PlanIntakeShapesTest(unittest.TestCase):
    def _plan(self, **patch):
        base = {"summary": "Do it.", "nodes": [
            {"name": "Receive CSV", "type": "user-input",
             "outputs": [{"name": "expense_csv", "type": "file"}]},
            {"name": "Filter rows", "type": "code", "code": "write_output('rows', [])",
             "inputs": [{"name": "expense_csv", "type": "file"}],
             "outputs": [{"name": "rows", "type": "list"}]}],
            "edges": [{"src": "Receive CSV", "dst": "Filter rows"}]}
        base.update(patch)
        return base

    def test_string_shapes_are_coerced(self):
        p = _with_intent(_workflow(pid="p_intake1"))
        plan = self._plan(edges=["Receive CSV -> Filter rows"])
        plan["nodes"][1]["outputs"] = ["rows"]
        plan["nodes"][1]["inputs"] = ["expense_csv"]
        plan["nodes"][1]["criteria"] = ["len(rows) >= 0"]
        plan["nodes"][1]["packages"] = "pandas, requests"
        r = node_tools.tool_save_plan(p, plan)
        self.assertTrue(r["ok"], r)
        step = p["plan"]["nodes"][1]
        self.assertEqual(step["outputs"][0]["name"], "rows")
        self.assertEqual(p["plan"]["edges"][0]["dst"], p["plan"]["nodes"][1]["id"])
        self.assertEqual(step["criteria"][0]["expr"], "len(rows) >= 0")
        self.assertEqual(step["packages"], ["pandas", "requests"])

    def test_port_qualified_edge_ends_and_empty_criteria_are_coerced(self):
        p = _with_intent(_workflow(pid="p_intake3"))
        plan = self._plan(edges=[{"src": "Receive CSV.expense_csv",
                                  "dst": "Filter rows.expense_csv"}])
        plan["nodes"][1]["criteria"] = [{"label": "rows kept", "expr": ""},
                                        {"label": "n", "expr": "len(rows) >= 0"}]
        r = node_tools.tool_save_plan(p, plan)
        self.assertTrue(r["ok"], r)
        ids = {n["name"]: n["id"] for n in p["plan"]["nodes"]}
        self.assertEqual(p["plan"]["edges"][0],
                         {"src": ids["Receive CSV"], "dst": ids["Filter rows"]})
        self.assertEqual([c["expr"] for c in p["plan"]["nodes"][1]["criteria"]],
                         ["len(rows) >= 0"])

    def test_the_unreadable_shapes_are_refused_not_crashed(self):
        p = _with_intent(_workflow(pid="p_intake2"))
        plan = self._plan()
        plan["nodes"][1]["tests"] = ["ok"]
        r = node_tools.tool_save_plan(p, plan)
        self.assertFalse(r["ok"])
        self.assertTrue(any("a test is an object" in e for e in r["errors"]))
        r = node_tools.tool_save_plan(p, self._plan(nodes="x"))
        self.assertFalse(r["ok"])
        r = node_tools.tool_save_plan(p, self._plan(edges=["Receive CSV, Filter rows"]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("src" in e for e in r["errors"]))

class DeadPathwaysTest(unittest.TestCase):
    def _workflow(self, pid):
        nodes = [
            {"id": "n_p", "name": "Parse panel", "type": "code",
             "inputs": [], "outputs": [{"name": "providers_result", "type": "list"}]},
            {"id": "n_o1", "name": "Extract under 400", "type": "code",
             "inputs": [{"name": "providers_result", "type": "list"}],
             "outputs": [{"name": "providers_under_400", "type": "list"}]},
            {"id": "n_o2", "name": "Parse rooms", "type": "code",
             "inputs": [{"name": "providers_under_400", "type": "list"}],
             "outputs": [{"name": "rooms_summary", "type": "text"}]},
            {"id": "n_n1", "name": "Extract threshold", "type": "code",
             "inputs": [{"name": "providers_result", "type": "list"}],
             "outputs": [{"name": "providers_under_threshold", "type": "list"}]},
            {"id": "n_m", "name": "Merge", "type": "code",
             "inputs": [{"name": "providers_under_threshold", "type": "list"}],
             "outputs": [{"name": "final_providers", "type": "list"}]},
            {"id": "n_e", "name": "Send email", "type": "connector",
             "read_only": False, "external_impact": "sends an email",
             "inputs": [{"name": "final_providers", "type": "list"}],
             "outputs": [{"name": "sent", "type": "boolean"}]},
        ]
        p = _workflow(nodes, pid=pid)
        p["edges"] = [{"src": "n_p", "dst": "n_o1"}, {"src": "n_o1", "dst": "n_o2"},
                      {"src": "n_p", "dst": "n_n1"}, {"src": "n_n1", "dst": "n_m"},
                      {"src": "n_m", "dst": "n_e"}]
        p["deliverables"] = [{"node": "n_e", "port": "sent", "label": "Email sent"}]
        return p

    def _plan(self):
        return {"summary": "s", "nodes": [
            {"name": "Merge", "type": "code", "code_sketch": "m",
             "inputs": [{"name": "providers_under_threshold", "type": "list"}],
             "outputs": [{"name": "final_providers", "type": "list"}]}],
            "edges": []}

    def test_the_whole_superseded_branch_flags_in_one_finding(self):
        from agent import plan_logic
        p = self._workflow("p_dead1")
        dead = [f for f in plan_logic.check(p, self._plan())["findings"]
                if "nothing uses" in f]
        self.assertEqual(len(dead), 1, dead)
        self.assertIn('"Extract under 400"', dead[0])
        self.assertIn('"Parse rooms"', dead[0])
        self.assertIn("changes.removed", dead[0])
        for kept in ("Parse panel", "Merge", "Send email", "Extract threshold"):
            self.assertNotIn(f'"{kept}"', dead[0])

    def test_a_when_condition_into_a_live_step_keeps_its_source_alive(self):
        from agent import plan_logic
        p = self._workflow("p_dead2")

        for e in p["edges"]:
            if e["dst"] == "n_m":
                e["when"] = "providers_under_400 == providers_under_400"
        dead = [f for f in plan_logic.check(p, self._plan())["findings"]
                if "nothing uses" in f]
        self.assertEqual(len(dead), 1, dead)
        self.assertNotIn('"Extract under 400"', dead[0])
        self.assertIn('"Parse rooms"', dead[0])

    def test_no_deliverables_means_no_check(self):
        from agent import plan_logic
        p = self._workflow("p_dead3")
        p["deliverables"] = []
        self.assertFalse([f for f in plan_logic.check(p, self._plan())["findings"]
                          if "nothing uses" in f])

    def test_a_write_connector_with_unread_outputs_is_exempt(self):
        from agent import plan_logic
        p = self._workflow("p_dead4")
        p["deliverables"] = [{"node": "n_m", "port": "final_providers",
                              "label": "Providers"}]
        dead = [f for f in plan_logic.check(p, self._plan())["findings"]
                if "nothing uses" in f]
        self.assertEqual(len(dead), 1, dead)
        self.assertNotIn('"Send email"', dead[0])

    def test_a_declared_removal_clears_the_gap(self):
        from agent import plan_logic
        p = self._workflow("p_dead5")
        plan = self._plan()
        plan["changes"] = {"removed": ["Extract under 400",
                                       {"id": "n_o2", "name": "Parse rooms"}]}
        self.assertFalse([f for f in plan_logic.check(p, plan)["findings"]
                          if "nothing uses" in f])

        plan["changes"] = {"removed": ["n_o1", "n_o2"]}
        self.assertFalse([f for f in plan_logic.check(p, plan)["findings"]
                          if "nothing uses" in f])

    def test_a_partial_removal_still_flags_what_remains_dead(self):
        from agent import plan_logic
        p = self._workflow("p_dead6")
        plan = self._plan()
        plan["changes"] = {"removed": ["Parse rooms"]}
        dead = [f for f in plan_logic.check(p, plan)["findings"]
                if "nothing uses" in f]
        self.assertEqual(len(dead), 1, dead)
        self.assertIn('"Extract under 400"', dead[0])
        self.assertNotIn('"Parse rooms"', dead[0])

class BuiltOutputNeverASettingTest(unittest.TestCase):
    def test_gutted_fragment_mints_no_setting(self):
        from agent import plan_logic
        p = _workflow([
            {"id": "n_ai", "name": "Score posts", "type": "ai",
             "inputs": [{"name": "posts", "type": "list"}],
             "outputs": [{"name": "verdicts", "type": "list"}]},
            {"id": "n_b", "name": "Build rows", "type": "code",
             "inputs": [{"name": "verdicts", "type": "list"}],
             "outputs": [{"name": "rows", "type": "list"}]}], pid="p_mint1")
        p["edges"] = [{"src": "n_ai", "dst": "n_b"}]

        r = plan_logic.check(p, {"summary": "s", "nodes": [
            {"name": "Score posts", "type": "ai", "prompt": "judge",
             "inputs": [{"name": "posts", "type": "list"}], "outputs": []},
            {"name": "Build rows", "type": "code", "code_sketch": "b",
             "inputs": [{"name": "verdicts", "type": "list"}],
             "outputs": [{"name": "rows", "type": "list"}]}],
            "edges": [{"src": "Score posts", "dst": "Build rows"}]})
        self.assertNotIn("verdicts", r["settings"], r)

class OrphanSeamTest(unittest.TestCase):
    def _steps(self):
        fetch = {"name": "Fetch the page", "type": "connector",
                 "read_only": True, "external_impact": "reads",
                 "code_sketch": "f", "inputs": [],
                 "outputs": [{"name": "main_panel_html", "type": "text"}]}
        parse = {"name": "Parse the panel", "type": "code", "code_sketch": "p",
                 "inputs": [{"name": "main_html", "type": "text"}],
                 "outputs": [{"name": "rows", "type": "list"}]}
        email = {"name": "Send summary", "type": "connector",
                 "read_only": False, "external_impact": "sends",
                 "code_sketch": "s",
                 "inputs": [{"name": "rows", "type": "list"},
                            {"name": "summary_email_address", "type": "text"}],
                 "outputs": [{"name": "sent", "type": "boolean"}]}
        edges = [{"src": "Fetch the page", "dst": "Parse the panel"},
                 {"src": "Parse the panel", "dst": "Send summary"}]
        return fetch, parse, email, edges

    def test_the_orphan_pair_is_a_gap_and_never_a_setting(self):
        from agent import plan_logic
        fetch, parse, email, edges = self._steps()
        p = _workflow([], pid="p_orphan1")
        r = plan_logic.check(p, {"summary": "s",
                                 "nodes": [fetch, parse, email], "edges": edges})
        pair = [f for f in r["findings"] if "main_html" in f]
        self.assertTrue(pair, r["findings"])
        self.assertIn("main_panel_html", pair[0])
        self.assertIn("nothing reads", pair[0])
        self.assertNotIn("main_html", r["settings"])

        self.assertIn("summary_email_address", r["settings"])

    def test_save_plan_creates_no_variable_for_the_broken_seam(self):
        fetch, parse, email, edges = self._steps()
        p = _workflow([], pid="p_orphan2")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        r = node_tools.tool_save_plan(
            p, {"summary": "s", "nodes": [fetch, parse, email], "edges": edges})
        self.assertTrue(r.get("ok"), r)
        names = {v["name"] for v in p.get("variables", [])}
        self.assertNotIn("main_html", names)
        self.assertIn("summary_email_address", names)

    def test_a_consistent_plan_stays_clean(self):
        from agent import plan_logic
        fetch, parse, email, edges = self._steps()
        parse["inputs"][0]["name"] = "main_panel_html"
        p = _workflow([], pid="p_orphan3")
        r = plan_logic.check(p, {"summary": "s",
                                 "nodes": [fetch, parse, email], "edges": edges})
        self.assertFalse([f for f in r["findings"] if "nothing reads" in f],
                         r["findings"])
        self.assertEqual(r["settings"], ["summary_email_address"])

class EmptyEdgeListTest(unittest.TestCase):
    def test_an_empty_edge_list_keeps_the_lines_and_a_full_one_replaces_them(self):
        from agent import steps
        prev = {"summary": "s", "nodes": [{"name": "A", "type": "code"}, {"name": "B", "type": "code"}],
                "edges": [{"src": "A", "dst": "B"}]}
        kept = steps.merge_amend(prev, {"nodes": [{"name": "A"}]})
        self.assertEqual(kept["edges"], [{"src": "A", "dst": "B"}])
        still = steps.merge_amend(prev, {"nodes": [{"name": "A"}], "edges": []})
        self.assertEqual(still["edges"], [{"src": "A", "dst": "B"}])
        new = steps.merge_amend(prev, {"nodes": [{"name": "A"}], "edges": [{"src": "B", "dst": "A"}]})
        self.assertEqual(new["edges"], [{"src": "B", "dst": "A"}])
