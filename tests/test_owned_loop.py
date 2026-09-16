# Tests: the SDK-ONLY builder: the Claude Agent SDK runs its own loop with our restricted, structured tool set
from __future__ import annotations

import json
import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from agent import actions, context, loop, prompt, transport
from agent import turnstate

def _call(name, input=None, cid="c1"):
    return {"id": cid, "name": name, "input": input or {}}

class SchemaTest(unittest.TestCase):
    def test_schemas_derive_from_the_spec_surface_no_say_no_recall(self):
        s = {t["name"]: t for t in actions.schemas()}
        self.assertIn("run_cell", s)
        self.assertIn("ask_user", s)
        self.assertIn("load_skill", s)
        self.assertNotIn("say", s)
        self.assertNotIn("recall", s)

        self.assertEqual(sorted(s["run_cell"]["input_schema"].get("required") or []), [])
        self.assertIn("cells", s["run_cell"]["input_schema"]["properties"])

class EnvelopeTest(unittest.TestCase):
    def test_envelope_is_serialised_in_full_no_truncation(self):
        env = {"action": "read_sample", "ok": True,
               "data": {"content": "z" * 60_000}}
        text = actions.envelope_text(env)
        self.assertEqual(json.loads(text), env)
        self.assertNotIn("truncated", text)

class ExecuteTest(unittest.TestCase):
    def test_dispatch_reuses_the_existing_bodies(self):
        p = _workflow([], pid="p_exec1")
        env = actions.execute(p, _call("list_nodes"), None)
        self.assertTrue(env["ok"])
        self.assertIn("nodes", env["data"])

    def test_bad_arguments_become_a_typed_repair_result(self):
        p = _workflow([], pid="p_exec2")
        env = actions.execute(p, _call("read_sample"), None)
        self.assertFalse(env["ok"])
        self.assertIn("bad arguments", env["error"])

    def test_read_node_with_neither_argument_names_both_forms(self):
        p = _workflow([], pid="p_exec2b")
        env = actions.execute(p, _call("read_node"), None)
        self.assertFalse(env["ok"])
        self.assertIn("node_ids", env["error"])

    def test_turn_over_rail_refuses_after_a_card(self):
        p = _workflow([], pid="p_exec3")
        turnstate.of(p).ask_open = True
        env = actions.execute(p, _call("list_nodes"), None)
        self.assertFalse(env["ok"])
        self.assertIn("OVER", env["error"])

    def test_load_skill_reads_a_connector_guide(self):
        p = _workflow([], pid="p_exec4")
        env = actions.execute(p, _call("load_skill", {"id": "google-sheets"}),
                              None)
        self.assertTrue(env["ok"], env)
        self.assertTrue(env["data"]["content"])

class PromptTest(unittest.TestCase):
    def test_system_carries_policy_and_skill_index(self):
        p = _workflow([], pid="p_prompt")
        s = prompt.system(p)
        self.assertIn("load_skill", s)
        self.assertIn("google-sheets", s)

class ContextTest(unittest.TestCase):
    def test_chat_context_replays_every_entry_once(self):
        p = _workflow([], pid="p_ctx1")
        for i in range(50):
            _bootstrap.said(p, "user", f"m{i}")
        msgs = context.build(p, "the new message")
        body = msgs[0]["content"]
        self.assertIn("the new message", body)
        self.assertIn("m0\n", body)
        self.assertIn("m49", body)
        self.assertEqual(body.count("m49"), 1)

    def test_the_whole_chat_is_replayed(self):
        p = _workflow([], pid="p_ctx1b")
        for i in range(30):
            _bootstrap.said(p, "user", f"m{i} " + "x" * 3000)
        body = context.build(p, "the new message")[0]["content"]
        self.assertNotIn("older entries not replayed", body)
        self.assertIn("m0 ", body)
        self.assertIn("m29 ", body)

    def test_brief_kinds_skip_the_chat_replay(self):
        p = _workflow([], pid="p_ctx2")
        _bootstrap.said(p, "user", "old chatter")
        body = context.build(p, "THE BRIEF", kind="fix")[0]["content"]
        self.assertIn("THE BRIEF", body)
        self.assertNotIn("old chatter", body)

class SecondOpinionIsGoneTest(unittest.TestCase):
    def test_the_tool_is_gone_from_every_surface(self):
        self.assertNotIn("second_opinion",
                         {t["name"] for t in actions.schemas()})
        self.assertFalse(hasattr(loop, "run_guard"))
        self.assertFalse(hasattr(actions, "_GUARD_FACTS"))

    def test_the_ground_truths_live_in_the_policy(self):
        from agent import skills
        policy = skills.policy_text()
        self.assertIn("You can store a value the user gives you", policy)
        self.assertIn("The user's own files are within reach", policy)
        self.assertIn("A line never carries data", policy)
        self.assertIn("shows the site saying it", policy)

class SdkBuilderTest(unittest.TestCase):
    def test_run_turn_always_uses_the_sdk_driver(self):
        import unittest.mock as m
        p = _workflow([], pid="p_dispatch")
        tr = transport.SdkTransport("m", "tok")
        with m.patch.object(loop, "_run_turn_sdk",
                            return_value={"content": "SDK", "kind": "text"}) as sdk:
            r = loop.run_turn(p, "hi", tr=tr)
        self.assertEqual(r["content"], "SDK")
        self.assertTrue(sdk.called)

    def test_sdk_denies_all_builtins_by_default(self):
        tr = transport.SdkTransport("m", "tok")
        builtins, allowed = tr._sdk_config(actions.schemas(),
                                           frozenset())
        self.assertEqual(builtins, [])
        self.assertTrue(all(a.startswith("mcp__cryogram__") for a in allowed))
        self.assertNotIn("ToolSearch", allowed)

    def test_sdk_enables_only_the_requested_native_aid(self):
        tr = transport.SdkTransport("m", "tok")
        builtins, allowed = tr._sdk_config(actions.schemas(),
                                           frozenset({"web_search"}))
        self.assertEqual(builtins, ["WebSearch"])
        self.assertIn("WebSearch", allowed)

    def test_the_one_exploration_aid_is_web_search(self):
        self.assertEqual(actions.EXPLORATION_AIDS, frozenset({"web_search"}))

    def test_terminal_landed_only_on_a_landed_card(self):
        p = _workflow([], pid="p_landed")
        self.assertFalse(loop._terminal_landed(p))
        turnstate.of(p).ask_open = True
        self.assertTrue(loop._terminal_landed(p))
        p2 = _workflow([], pid="p_landed2")
        turnstate.of(p2).built = "Built it."
        self.assertTrue(loop._terminal_landed(p2))

    def test_a_plan_card_does_not_stop_the_sdk_loop(self):
        from agent import node_tools
        p = _workflow([], pid="p_card_build")
        node_tools.append_plan_entry(p, {"summary": "Do.", "nodes": []})
        self.assertFalse(loop._terminal_landed(p))
        self.assertEqual(node_tools.turn_over_error(p, "run_cell"), "")
        turnstate.of(p).built = "Built it."
        self.assertTrue(loop._terminal_landed(p))
        self.assertIn("OVER", node_tools.turn_over_error(p, "run_cell"))

    def test_sdk_tool_server_builds_from_our_tools(self):
        try:
            import claude_agent_sdk
        except Exception:
            self.skipTest("claude-agent-sdk not installed")
        p = _workflow([], pid="p_sdksrv")
        srv = loop._sdk_tool_server(p, lambda e: None, {})
        self.assertIsNotNone(srv)
        self.assertEqual(loop._short_tool("mcp__cryogram__run_cell"), "run_cell")

    def test_every_tool_tells_the_cli_its_own_cut_sits_past_ours(self):
        try:
            import claude_agent_sdk
            from mcp import types
        except Exception:
            self.skipTest("claude-agent-sdk not installed")
        import asyncio
        from agent import previews
        p = _workflow([], pid="p_sdkmeta")
        srv = loop._sdk_tool_server(p, lambda e: None, {})
        inst = srv["instance"]

        table = getattr(inst, "_request_handlers", None) or getattr(inst, "request_handlers", {})
        handler = next(v for k, v in table.items()
                       if "ListTools" in getattr(k, "__name__", str(k)) or str(k) == "tools/list")
        import inspect
        fn = getattr(handler, "handler", handler)
        res = asyncio.run(fn(None, None) if len(inspect.signature(fn).parameters) == 2
                          else fn(types.ListToolsRequest(method="tools/list")))
        listed = getattr(res, "tools", None) or res.root.tools
        self.assertTrue(listed)
        for t in listed:
            self.assertEqual((t.meta or {}).get("anthropic/maxResultSizeChars"),
                             previews.inline_limit("claude") * 2, t.name)

        from agent import actions

        def read_only(a):
            return bool(getattr(a, "read_only_hint", None) or getattr(a, "readOnlyHint", None))
        marked = {t.name for t in listed if t.annotations and read_only(t.annotations)}
        self.assertEqual(marked, set(actions.READ_ONLY_TOOLS))
        self.assertNotIn("run_cell", marked)
        self.assertNotIn("save_plan", marked)

class RefusedBuildDeadlockTest(unittest.TestCase):
    def test_a_refused_build_leaves_the_turn_workable(self):
        from agent import node_tools
        p = _workflow([], pid="p_deadlock")

        env = actions.execute(p, _call("build_workflow"), lambda e: None)
        self.assertFalse(env["ok"], env)

        self.assertEqual(node_tools.turn_over_error(p, "run_cell"), "")
        self.assertEqual(node_tools.turn_over_error(p, "ask_user"), "")

    def test_a_successful_build_still_ends_the_turn(self):
        from agent import node_tools
        p = _workflow([], pid="p_deadlock2")
        turnstate.of(p).built = "Built it."
        self.assertIn("OVER", node_tools.turn_over_error(p, "run_cell"))
        self.assertIn("OVER", node_tools.turn_over_error(p, "ask_user"))

    def test_ask_user_may_follow_a_plan_card(self):
        from agent import node_tools
        p = _workflow([], pid="p_deadlock3")
        node_tools.append_plan_entry(p, {"summary": "Do.", "nodes": []})
        env = actions.execute(p, _call("ask_user", {"question": "Stuck - now?",
                                                    "options": ["Carry on", "Stop"]}),
                              lambda e: None)
        self.assertTrue(env["ok"], env)
        self.assertTrue(turnstate.of(p).ask_open)
        self.assertEqual(_bootstrap.shown_requests(p)[-1]["request"], "ask")

    def test_an_unchanged_summary_lands_no_card(self):
        from agent import node_tools
        p = _workflow([], pid="p_deadlock4")
        plan = {"ts": 1.0, "summary": "Do the thing", "nodes": []}
        self.assertTrue(node_tools.append_plan_entry(p, plan))
        self.assertFalse(node_tools.append_plan_entry(p, dict(plan, ts=2.0)))
        plan2 = {"ts": 3.0, "summary": "Do a different thing", "nodes": []}
        self.assertTrue(node_tools.append_plan_entry(p, plan2))

class ResultContractTest(unittest.TestCase):
    def test_plural_errors_is_a_failure(self):
        self.assertFalse(actions._result_ok({"ok": False, "errors": ["x", "y"]}))
        self.assertIn("x; y", actions._result_error({"errors": ["x", "y"]}))

    def test_singular_error_is_a_failure(self):
        self.assertFalse(actions._result_ok({"error": "nope"}))
        self.assertEqual(actions._result_error({"error": "nope"}), "nope")

    def test_explicit_ok_false_is_a_failure(self):
        self.assertFalse(actions._result_ok({"ok": False}))

    def test_a_clean_result_is_success(self):
        self.assertTrue(actions._result_ok({"ok": True, "note": "saved"}))
        self.assertTrue(actions._result_ok({"data": 1}))

    def test_rejected_save_plan_reports_failure_and_no_card(self):
        p = _workflow([], pid="p_rej")
        p["intent"] = {"summary": "x"}
        bad = {"summary": "do it", "nodes": [
            {"name": "Post", "type": "connector", "inputs": [],
             "external_impact": "posts a message",
             "outputs": [{"type": "text"}]}]}
        env = actions.execute(p, _call("save_plan", {"plan": bad}),
                              lambda e: None)
        self.assertFalse(env["ok"])
        self.assertIn("port needs a name", env["error"])
        self.assertIsNone(p.get("plan"))

    def test_a_content_gap_saves_instead_of_refusing(self):
        from agent import node_tools
        p = _workflow([], pid="p_rej2")
        p.pop("plan_approved_ts", None)
        p["intent"] = {"summary": "x", "instructions": "run it"}
        gapped = {"summary": "do it", "nodes": [
            {"name": "Post", "type": "connector", "inputs": [],
             "outputs": [{"name": "r", "type": "text"}]}]}
        env = actions.execute(p, _call("save_plan", {"plan": gapped}),
                              lambda e: None)
        self.assertTrue(env["ok"], env)
        self.assertTrue(p["plan"]["nodes"])
        self.assertTrue(any("what it changes there" in g
                            for g in p["plan"]["design_gaps"]))
        self.assertIn("design gaps",
                      node_tools.tool_build_workflow(p).get("error", ""))

        cards = _bootstrap.shown_requests(p, "blueprint")
        self.assertEqual([c["payload"]["head"] for c in cards], ["plan"])

class NothingIsLeftForTheBrowserTest(unittest.TestCase):
    def test_the_done_reply_carries_no_build_request(self):
        from agent import node_tools
        p = _workflow([], pid="p_nolisten")
        turnstate.of(p).built = "Done - built and checked."
        self.assertTrue(loop._terminal_landed(p))

        self.assertIsInstance(turnstate.of(p).built, str)
        self.assertIn("OVER", node_tools.turn_over_error(p, "run_cell"))

    def test_the_server_never_puts_a_build_on_the_done_event(self):
        import pathlib as _pl
        src = _pl.Path("backend/server.py").read_text()
        self.assertNotIn('done["build"]', src)
        self.assertNotIn('for k in ("build", "fix")', src)

        self.assertNotIn('parts[3] == "codify"', src)

    def test_the_client_has_no_build_route_left(self):
        import pathlib as _pl
        self.assertNotIn("streamCodify",
                         _pl.Path("frontend/js/api.js").read_text())
        self.assertNotIn("streamCodify",
                         _pl.Path("frontend/js/app.js").read_text())

class CardPauseTest(unittest.TestCase):
    def _resolve_soon(self, pid, answer, delay=0.15):
        import threading
        import time as _t
        from agent import interactions

        def go():
            deadline = _t.time() + 3
            while _t.time() < deadline:
                pend = [e for e in interactions.pending(pid)
                        if e["kind"] == "ask"]
                if pend:
                    interactions.resolve(pend[-1]["id"], answer)
                    return
                _t.sleep(0.02)
        t = threading.Thread(target=go, daemon=True)
        _t = __import__("time")
        threading.Timer(delay, t.start).start()
        return t

    def _patient(self):
        from unittest import mock

        from agent import interactions
        return mock.patch.object(interactions, "CARD_PATIENCE", 5.0)

    def test_click_answer_continues_the_turn(self):
        p = _workflow([], pid="p_pause1")
        with self._patient():
            self._resolve_soon("p_pause1", {"text": "Option A", "via": "click"})
            env = actions.execute(p, _call("ask_user", {
                "question": "Pick one", "options": ["Option A", "Option B"]}),
                lambda ev: None)
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["answer"], "Option A")
        self.assertFalse(turnstate.of(p).ask_open)
        entry = _bootstrap.shown_requests(p)[-1]
        from agent import transcript
        self.assertIn(entry["iid"], transcript.answered(p))
        ans = [i for i in transcript.items(p) if i["kind"] == "answer"][-1]
        self.assertEqual(ans["shown"], "Option A")

        self.assertEqual(entry["payload"]["question"], "Pick one")

    def test_read_node_ships_the_whole_code_always(self):
        code = ("start = read_input('page')\n"
                + "\n".join(f"x{i} = {i}  # middle line" for i in range(120))
                + "\nMIDDLE_MARKER = True\n"
                + "\n".join(f"y{i} = {i}" for i in range(120))
                + "\nwrite_output('out', start)\n")
        p = _workflow([{"id": "n_long", "name": "Long step", "type": "code",
                       "config": {"code": code},
                       "inputs": [{"name": "page", "type": "text"}],
                       "outputs": [{"name": "out", "type": "text"}],
                       "tests": []}], pid="p_fullcode")
        env = actions.execute(p, _call("read_node", {"node_id": "n_long"}),
                              lambda ev: None)
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["config"]["code"], code)

    def test_a_no_is_sticky_and_the_card_says_no_is_fine(self):
        from agent import node_tools, transcript
        p = _workflow([], pid="p_stickyno")
        card = transcript.append_request(
            p, "approval", {"title": "Send it?", "step": "Post it"})
        transcript.append_answer(p, card["iid"], "No.", shown="deny")
        env = actions._cell_approvals(
            p, lambda ev: None, lambda *a, **k: {"ok": True},
            {"name": "Post it"}, {"needs_send_ok": "Post it"})
        self.assertIn("already said no", env["error"])
        self.assertEqual(len([i for i in transcript.items(p)
                              if i.get("kind") == "request"]), 1)

        env2 = actions._cell_approvals(
            p, lambda ev: None, lambda *a, **k: {"ok": True},
            {"name": "Post it", "fresh": True},
            {"needs_send_ok": "Post it"})
        self.assertIn("already said no", env2["error"])
        self.assertEqual(len([i for i in transcript.items(p)
                              if i.get("kind") == "request"]), 1)
        actions._cell_approvals(
            p, lambda ev: None, lambda *a, **k: {"ok": True},
            {"name": "Post it", "ask_again": "the sheet is empty without it"},
            {"needs_send_ok": "Post it"})
        reqs = [i for i in transcript.items(p) if i.get("kind") == "request"]
        self.assertEqual(len(reqs), 2)
        self.assertIn("You said no to this earlier", reqs[-1]["payload"]["detail"])
        self.assertIn("Saying no is fine", reqs[-1]["payload"]["scope"])

    def test_a_send_yes_holds_for_the_step_and_only_a_repeat_asks_again(self):
        from agent import transcript
        from unittest import mock
        p = _workflow([], pid="p_sendhold")
        calls = []

        def fn(workflow, **kw):
            calls.append(kw.get("send_ok"))
            return {"ok": True}
        with mock.patch.object(actions, "_approve", lambda *a, **k: "allow"):
            actions._cell_approvals(p, lambda ev: None, fn, {"name": "Post it"},
                                    {"needs_send_ok": "Post it"})
        self.assertEqual(calls, [True])
        self.assertTrue(any(g.get("kind") == "send" and g.get("step") == "Post it"
                            for g in p.get("approval_grants") or []))

        actions._cell_approvals(p, lambda ev: None, fn, {"name": "Post it"},
                                {"needs_send_ok": "Post it"})
        self.assertEqual(calls, [True, True])
        self.assertEqual([i for i in transcript.items(p) if i.get("kind") == "request"], [])
        self.assertTrue(any(g.get("kind") == "send" for g in p.get("approval_grants") or []))

        actions._cell_approvals(p, lambda ev: None, fn, {"name": "Post it"},
                                {"needs_send_ok": "Post it", "send_repeat": True})
        self.assertEqual(calls, [True, True])
        reqs = [i for i in transcript.items(p) if i.get("kind") == "request"]
        self.assertEqual(len(reqs), 1)
        self.assertIn("again", reqs[0]["payload"]["title"])
        self.assertEqual(reqs[0]["payload"]["holds"], "send")

    def test_a_changed_step_asks_again_and_says_why_unless_the_yes_was_for_good(self):
        from agent import transcript
        from unittest import mock
        p = _workflow([], pid="p_sendchg")
        calls = []

        def fn(workflow, **kw):
            calls.append(kw.get("send_ok"))
            return {"ok": True}
        with mock.patch.object(actions, "_approve", lambda *a, **k: "allow"):
            actions._cell_approvals(p, lambda ev: None, fn, {"name": "Post it"},
                                    {"needs_send_ok": "Post it", "code_key": "k1"})

        actions._cell_approvals(p, lambda ev: None, fn, {"name": "Post it"},
                                {"needs_send_ok": "Post it", "code_key": "k1"})
        self.assertEqual(calls, [True, True])

        actions._cell_approvals(p, lambda ev: None, fn, {"name": "Post it", "reason": "new recipient"},
                                {"needs_send_ok": "Post it", "code_key": "k2"})
        self.assertEqual(calls, [True, True])
        reqs = [i for i in transcript.items(p) if i.get("kind") == "request"]
        self.assertEqual(len(reqs), 1)
        self.assertIn("the step's code has changed since", reqs[0]["payload"]["detail"])
        self.assertIn("new recipient", reqs[0]["payload"]["detail"])

        with mock.patch.object(actions, "_approve", lambda *a, **k: "always"):
            actions._cell_approvals(p, lambda ev: None, fn, {"name": "Post it"},
                                    {"needs_send_ok": "Post it", "code_key": "k2"})
        actions._cell_approvals(p, lambda ev: None, fn, {"name": "Post it"},
                                {"needs_send_ok": "Post it", "code_key": "k3"})
        self.assertEqual(calls, [True, True, True, True])
        self.assertTrue(next(g for g in p["approval_grants"] if g.get("kind") == "send")["always"])

    def test_the_no_line_says_what_a_no_leaves_behind(self):
        from agent import node_tools, transcript
        p = _workflow([], pid="p_noline")
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "Fetch posts", "type": "browser", "read_only": True,
             "external_impact": "reads a site", "code_sketch": "fetches",
             "outputs": [{"name": "posts", "type": "list"}]},
            {"name": "Post it", "type": "connector", "read_only": False,
             "external_impact": "posts", "code_sketch": "posts",
             "outputs": [{"name": "sent", "type": "list"}]}]})
        def card_of(args, envelope):
            actions._cell_approvals(p, lambda ev: None,
                                    lambda *a, **k: {"ok": True}, args, envelope)
            reqs = [i for i in transcript.items(p) if i.get("kind") == "request"]
            return reqs[-1]["payload"]
        read = card_of({"name": "Fetch posts", "reason": "first look"},
                       {"needs_browser_ok": "Fetch posts", "domain": "example.test"})
        self.assertIn("If you say no", read["scope"])
        self.assertNotIn("Saying no is fine", read["scope"])
        self.assertEqual((read.get("holds"), read.get("domain")), ("window", "example.test"))
        self.assertIn("Open example.test in a browser window", read["title"])
        self.assertNotIn("already been tried", read["detail"])
        sends = card_of({"name": "Fetch posts", "reason": "reply"},
                        {"needs_browser_ok": "Fetch posts", "domain": "example.test", "sends": True})
        self.assertIn("ask before each window", sends["scope"])
        self.assertNotIn("holds", sends)
        resend = card_of({"name": "Post it", "reason": "changed code"},
                         {"needs_send_ok": "Post it", "send_repeat": True})
        self.assertIn("earlier send", resend["scope"])
        for c in (read, sends, resend):
            self.assertNotIn("\u2014", c["scope"] + c["title"] + c["detail"])

    def test_dont_ask_again_is_what_the_card_shows_and_what_is_kept(self):
        from agent import reply, transcript
        p = _workflow([], pid="p_always_rest")
        req = {"request": "approval", "iid": "int_always",
               "payload": {"title": "Open example.test in a browser window?",
                           "step": "peek", "holds": "window", "domain": "example.test"}}
        done = reply.settle(p, req, {"text": "always", "via": "click", "cid": "c1"}, at_rest=True)
        self.assertEqual(done["shown"], "always")
        self.assertTrue(done["drive"].startswith("Yes to"))
        self.assertEqual([(g.get("kind"), g.get("domain"), g.get("always"))
                          for g in p["approval_grants"]], [("window", "example.test", True)])
        answers = [i for i in transcript.items(p) if i.get("kind") == "answer"]
        self.assertEqual(answers[-1]["shown"], "always")

    def test_a_kept_yes_for_a_site_opens_its_later_windows(self):
        from unittest import mock
        from agent import interactions
        p = _workflow([], pid="p_sitekeep")
        asked = []

        def _wait(iid, timeout=600, **kw):
            interactions._pending.pop(iid, None)
            asked.append(iid)
            return "always"
        with mock.patch.object(interactions, "wait_sync", _wait):
            actions._cell_approvals(p, lambda ev: None, lambda *a, **k: {"ok": True},
                                    {"name": "peek", "reason": "look"},
                                    {"needs_browser_ok": "peek", "domain": "example.test"})
        self.assertEqual(len(asked), 1)
        self.assertEqual([(g.get("kind"), g.get("domain"), g.get("always"))
                          for g in p["approval_grants"]], [("window", "example.test", True)])
        with mock.patch.object(interactions, "wait_sync", _wait):
            self.assertEqual(actions._approve(p, lambda ev: None, "Open it?", "", "",
                                              step="other", holds="window",
                                              domain="example.test"), "allow")
            self.assertEqual(len(asked), 1)
            actions._approve(p, lambda ev: None, "Open it?", "", "", step="other",
                             holds="window", domain="another.test")
            self.assertEqual(len(asked), 2)

    def test_an_empty_or_stub_question_is_refused(self):
        p = _workflow([], pid="p_pause9")
        env = actions.execute(p, _call("ask_user", {"question": ""}),
                              lambda ev: None)
        self.assertFalse(env["ok"])
        self.assertIn("empty", env["error"])
        env2 = actions.execute(p, _call("ask_user", {"question": "Go?"}),
                               lambda ev: None)
        self.assertFalse(env2["ok"])

        self.assertIn("no way to answer on the card", env2["error"])

    def test_typed_answer_lands_a_user_bubble(self):
        p = _workflow([], pid="p_pause2")
        with self._patient():
            self._resolve_soon("p_pause2", {"text": "something else entirely",
                                            "via": "typed"})
            env = actions.execute(p, _call("ask_user",
                                           {"question": "Shall I go ahead?",
                                            "options": ["Yes", "No"]}),
                                  lambda ev: None)
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["answer"], "something else entirely")
        from agent import transcript
        req = _bootstrap.shown_requests(p, "ask")[-1]
        self.assertIn(req["iid"], transcript.answered(p))

        msgs = [i for i in transcript.items(p) if i["kind"] == "message"]
        self.assertEqual(msgs[-1]["from"], "user")
        self.assertEqual(msgs[-1]["text"], "something else entirely")

    def _open_plan(self, pid):
        from agent import node_tools
        p = _workflow([], pid=pid)
        p.pop("plan_approved_ts", None)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        return p, {"summary": "Do the thing.", "nodes": [
            {"name": "Extract", "type": "code", "code": "write_output('y', 1)",
             "outputs": [{"name": "y", "type": "text"}]}]}

    def test_the_opening_plan_card_pauses_and_a_click_approves(self):
        from agent import node_tools, transcript
        p, plan = self._open_plan("p_plancard1")
        with self._patient():
            self._resolve_soon("p_plancard1",
                               {"text": actions.PLAN_OK_LABEL, "via": "click"})
            env = actions.execute(p, _call("save_plan", {"plan": plan}),
                                  lambda ev: None)
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["plan_shown"], "approved")
        self.assertTrue(node_tools.plan_approved(p))
        card = _bootstrap.shown_requests(p, "blueprint")[-1]
        self.assertEqual(card["payload"]["head"], "plan")
        self.assertEqual(card["payload"]["options"], [actions.PLAN_OK_LABEL])
        self.assertIn(card["iid"], transcript.answered(p))

        self.assertIn("build_workflow", env["data"]["note"])

    def test_typing_at_the_plan_card_is_a_revision_not_approval(self):
        from agent import node_tools
        p, plan = self._open_plan("p_plancard2")
        with self._patient():
            self._resolve_soon("p_plancard2", {"text": "also file the result",
                                               "via": "typed"})
            env = actions.execute(p, _call("save_plan", {"plan": plan}),
                                  lambda ev: None)
        self.assertEqual(env["data"]["plan_shown"], "revised")
        self.assertEqual(env["data"]["answer"], "also file the result")
        self.assertFalse(node_tools.plan_approved(p))

    def test_an_unanswered_plan_card_parks_like_any_other(self):
        from agent import node_tools, transcript
        p, plan = self._open_plan("p_plancard3")
        env = actions.execute(p, _call("save_plan", {"plan": plan}),
                              lambda ev: None)
        self.assertEqual(env["data"]["plan_shown"], "waiting")
        self.assertTrue(turnstate.of(p).ask_open)
        self.assertFalse(node_tools.plan_approved(p))
        self.assertTrue(transcript.open_requests(p, "blueprint"))

    def test_secret_answer_writes_the_marker_and_masks(self):
        p = _workflow([], pid="p_pause3")
        with self._patient():
            self._resolve_soon("p_pause3", {"text": "(provided)",
                                            "via": "click"})
            env = actions.execute(p, _call("ask_user", {
                "question": "Key?", "secret": True, "secret_name": "API_K"}),
                lambda ev: None)
        self.assertTrue(env["ok"], env)
        self.assertEqual(env["data"]["answer"], "(provided)")
        var = next(v for v in p["variables"] if v["name"] == "API_K")
        self.assertTrue(var["secret"])

    def test_no_answer_parks_the_card_at_rest(self):
        p = _workflow([], pid="p_pause4")
        env = actions.execute(p, _call("ask_user", {"question": "Still there?",
                                                    "options": ["Yes"]}),
                              lambda ev: None)
        self.assertTrue(env["ok"], env)
        self.assertIn("asked", env["data"])
        self.assertTrue(turnstate.of(p).ask_open)
        from agent import transcript
        self.assertTrue(transcript.open_requests(p, "ask"))

    def test_approval_entry_persists_and_settles(self):
        import threading

        from agent import interactions
        p = _workflow([], pid="p_pause5")
        with self._patient():
            def allow():
                import time as _t
                deadline = _t.time() + 3
                while _t.time() < deadline:
                    pend = interactions.pending("p_pause5")
                    if pend:
                        interactions.resolve(pend[-1]["id"], "allow")
                        return
                    _t.sleep(0.02)
            threading.Thread(target=allow, daemon=True).start()
            decision = actions._approve(p, lambda ev: None, "Do the thing",
                                        "why", "scope")
        self.assertEqual(decision, "allow")
        from agent import transcript
        req = _bootstrap.shown_requests(p, "approval")[-1]
        ans = [i for i in transcript.items(p)
               if i["kind"] == "answer" and i["to"] == req["iid"]]
        self.assertEqual(ans[-1]["shown"], "allow")

    def test_parked_approval_yes_is_the_grant(self):
        import time as _t
        p = _workflow([], pid="p_grant1")
        p["approval_grants"] = [{"step": "Fetch posts", "title": "Open?",
                                 "ts": _t.time()}]
        decision = actions._approve(p, lambda ev: None,
                                    'Open a browser window for "Fetch posts"?',
                                    "why", "scope", step="Fetch posts")
        self.assertEqual(decision, "allow")
        self.assertEqual(p["approval_grants"], [])
        self.assertEqual(_bootstrap.shown_requests(p, "approval"), [])

        p2 = _workflow([], pid="p_grant2")
        p2["approval_grants"] = [{"step": "Fetch posts", "title": "Open?",
                                  "ts": _t.time() - 7200}]
        decision = actions._approve(p2, lambda ev: None, "Open?",
                                    "why", "scope", step="Fetch posts")
        self.assertEqual(decision, "allow")
        self.assertEqual(p2["approval_grants"], [])

    def test_parked_approval_settles_on_the_next_message(self):
        from agent import node_tools
        p = _workflow([], pid="p_pause6")
        decision = actions._approve(p, lambda ev: None, "Do the thing",
                                    "why", "scope")
        self.assertIsNone(decision)
        from agent import transcript
        req = _bootstrap.shown_requests(p, "approval")[-1]
        self.assertNotIn(req["iid"], transcript.answered(p))
        node_tools.settle_open_asks(p)
        self.assertIn(req["iid"], transcript.answered(p))

        self.assertEqual(req, _bootstrap.shown_requests(p, "approval")[-1])

    def test_stop_wakes_the_wait_early(self):
        import time as _t
        import turns

        from agent import interactions
        from unittest import mock
        turns.begin("p_pause7", "chat")
        try:
            turns.request_stop("p_pause7")
            iid = interactions.create_sync("ask", {"workflow_id": "p_pause7"})
            t0 = _t.monotonic()
            with mock.patch.object(interactions, "CARD_PATIENCE", 30.0):
                got = interactions.wait_sync(iid)
            self.assertIsNone(got)
            self.assertLess(_t.monotonic() - t0, 5.0)
        finally:
            turns.finish("p_pause7")

class ApprovalFlushTest(unittest.TestCase):
    def _patient(self):
        from unittest import mock

        from agent import interactions
        return mock.patch.object(interactions, "CARD_PATIENCE", 5.0)

    def _allow_soon(self, pid):
        import threading

        from agent import interactions

        def allow():
            import time as _t
            deadline = _t.time() + 3
            while _t.time() < deadline:
                pend = interactions.pending(pid)
                if pend:
                    interactions.resolve(pend[-1]["id"], "allow")
                    return
                _t.sleep(0.02)
        threading.Thread(target=allow, daemon=True).start()

    def test_approval_flushes_narration_into_the_message_above(self):
        import turns
        pid = "p_aflush1"
        p = _workflow([], pid=pid)
        turns.begin(pid, "chat")
        try:
            turns.record(pid, {"type": "delta", "text": "checking the login"})
            evs = []
            turns.bind_emit(pid, evs.append)
            with self._patient():
                self._allow_soon(pid)
                decision = actions._approve(p, evs.append, "Open a window?",
                                            "why", "scope")
            self.assertEqual(decision, "allow")
            from agent import transcript
            items = transcript.items(p)
            card_at = next(k for k, i in enumerate(items)
                           if i.get("request") == "approval")

            self.assertEqual(items[card_at - 1]["kind"], "message")
            self.assertEqual(items[card_at - 1]["text"], "checking the login")
            self.assertNotIn("lead", items[card_at]["payload"])
            shown = [e["item"] for e in evs if e.get("type") == "shown"]
            self.assertEqual([i.get("kind") for i in shown][:2], ["message", "request"])

            self.assertEqual(turns.partial_text(pid), "")
        finally:
            turns.finish(pid)

    def test_second_approval_same_turn_lands_no_extra_message(self):
        import turns
        pid = "p_aflush2"
        p = _workflow([], pid=pid)
        turns.begin(pid, "chat")
        try:
            turns.record(pid, {"type": "delta", "text": "first tail"})
            evs = []
            turns.bind_emit(pid, evs.append)
            with self._patient():
                self._allow_soon(pid)
                actions._approve(p, evs.append, "One?", "d", "s")
            with self._patient():
                self._allow_soon(pid)
                actions._approve(p, evs.append, "Two?", "d", "s")
            from agent import transcript
            kinds = [(i["kind"], i.get("text")) for i in transcript.items(p)
                     if i["kind"] == "message" and i.get("from") == "assistant"]
            self.assertEqual(kinds, [("message", "first tail")])
            entry2 = _bootstrap.shown_requests(p, "approval")[1]
            self.assertNotIn("lead", entry2["payload"])
        finally:
            turns.finish(pid)

    def test_an_ask_lands_its_narration_above_the_card_too(self):
        import turns
        from agent import transcript
        pid = "p_askflush1"
        p = _workflow([], pid=pid)
        turns.begin(pid, "chat")
        try:
            turns.record(pid, {"type": "delta", "text": "checking what the sheet holds"})
            env = actions.execute(p, _call("ask_user", {"question": "Which tab?",
                                                          "options": ["A", "B"]}),
                                  lambda e: None)
            self.assertTrue(env["ok"], env)
            items = transcript.items(p)
            card_at = next(k for k, i in enumerate(items) if i.get("request") == "ask")
            self.assertEqual(items[card_at - 1]["kind"], "message")
            self.assertEqual(items[card_at - 1]["text"], "checking what the sheet holds")
            self.assertNotIn("lead", items[card_at]["payload"])
            self.assertEqual(turns.partial_text(pid), "")
        finally:
            turns.finish(pid)

    def test_parked_approval_keeps_its_message_and_says_parked(self):
        import turns
        pid = "p_aflush3"
        p = _workflow([], pid=pid)
        turns.begin(pid, "chat")
        try:
            turns.record(pid, {"type": "delta", "text": "about to send"})
            evs = []
            decision = actions._approve(p, evs.append, "Send it?", "d", "s")
            self.assertIsNone(decision)
            entry = _bootstrap.shown_requests(p, "approval")[-1]
            from agent import transcript
            self.assertNotIn(entry["iid"], transcript.answered(p))
            self.assertNotIn("lead", entry["payload"])
            self.assertEqual([i["text"] for i in transcript.items(p)
                              if i["kind"] == "message"], ["about to send"])
            done = next(e for e in evs if e.get("type") == "approval-done")
            self.assertEqual(done["decision"], "parked")
        finally:
            turns.finish(pid)

class SkillResolverTest(unittest.TestCase):
    def test_one_resolver_serves_load_skill(self):
        got = actions.read_skill_or_learning("google-sheets")
        self.assertTrue((got or {}).get("content"))
        self.assertIsNone(actions.read_skill_or_learning("no-such-guide"))

class AskFieldsRailTest(unittest.TestCase):
    _LISTY = ("Almost ready. A few things:\n"
              "1. LinkedIn search URL - paste one here.\n"
              "2. Fit criteria - describe who counts.\n"
              "3. Sheet link - share it as Editor first.")

    def test_numbered_value_list_without_fields_is_refused(self):
        p = _workflow([], pid="p_fields1")
        env = actions.execute(p, _call("ask_user", {"question": self._LISTY}),
                              lambda ev: None)
        self.assertFalse(env["ok"])
        self.assertIn("a field for each value", env["error"])

        self.assertFalse(_bootstrap.shown_requests(p, "ask"))
        self.assertFalse(turnstate.of(p).ask_open)

    def test_the_same_question_with_fields_passes(self):
        p = _workflow([], pid="p_fields2")
        env = actions.execute(p, _call("ask_user", {
            "question": self._LISTY,
            "fields": [{"name": "search_url", "label": "LinkedIn search URL"},
                       {"name": "fit_criteria", "label": "Fit criteria"},
                       {"name": "sheet_url", "label": "Sheet link"}]}),
            lambda ev: None)
        self.assertTrue(env["ok"], env)

    def test_numbered_instructions_with_options_pass(self):
        p = _workflow([], pid="p_fields3")
        env = actions.execute(p, _call("ask_user", {
            "question": "Two ways in:\n"
                        "1. The API route.\n"
                        "2. The browser route.\n"
                        "Which one?",
            "options": ["API", "Browser"]}), lambda ev: None)
        self.assertTrue(env["ok"], env)

class EgressDomainFoldTest(unittest.TestCase):
    def test_allow_folds_the_plan_declared_set(self):
        from unittest import mock

        from agent import interactions
        p = _workflow([], pid="p_fold1")
        p["plan"] = {"status": "draft", "nodes": [
            {"name": "Save to sheet", "type": "connector",
             "domains": ["oauth2.googleapis.com", "sheets.googleapis.com"]}]}
        blocked = {"ok": False, "error": "<urlopen error egress blocked: ('oauth2.googleapis.com', 443) is not on the allowlist>"}
        calls = {"n": 0}

        def cell(workflow, **kw):
            calls["n"] += 1
            return blocked if calls["n"] == 1 else {"ok": True}
        stub = dict(actions._fns(), run_cell=cell)
        cards = []

        def _wait(iid, timeout=None, **kw):
            interactions._pending.pop(iid, None)
            return "allow"

        def emit(ev):
            if ev.get("type") == "shown" \
                    and (ev.get("item") or {}).get("request") == "approval":
                cards.append(ev)
        with mock.patch.object(actions, "_fns", lambda: stub), \
             mock.patch.object(interactions, "wait_sync", _wait), \
             _bootstrap.shown_through(p["id"], emit):
            env = actions.execute(
                p, _call("run_cell", {"name": "save_to_sheet", "code": "x=1"}),
                emit)
        self.assertTrue(env["ok"], env)
        self.assertEqual(len(cards), 1)
        self.assertIn("sheets.googleapis.com", cards[0]["item"]["payload"]["detail"])
        self.assertEqual(p["egress_allowlist"],
                         ["oauth2.googleapis.com", "sheets.googleapis.com"])

class EgressApprovalExpiryTest(unittest.TestCase):
    def _run(self, decision):
        from unittest import mock

        from agent import interactions
        p = _workflow([], pid="p_appr")
        blocked = {"ok": False, "error": "<urlopen error egress blocked: ('oauth2.googleapis.com', 443) is not on the allowlist>"}
        events = []

        stub = dict(actions._fns(),
                    run_cell=lambda workflow, **kw: blocked)

        def _wait(iid, timeout=600, **kw):
            interactions._pending.pop(iid, None)
            return decision

        with mock.patch.object(actions, "_fns", lambda: stub), \
             mock.patch.object(interactions, "wait_sync", _wait):
            env = actions.execute(
                p, _call("run_cell", {"name": "Fetch", "code": "x=1"}),
                events.append)
        return p, env, events

    def test_no_answer_ends_the_turn_and_tells_the_agent_nothing(self):
        from agent import turnstate
        p, env, events = self._run(None)
        self.assertTrue(env["ok"])
        self.assertEqual(env.get("data"), {"ok": True, "parked": True})
        self.assertNotIn("error", env)
        self.assertNotIn("note", env)
        self.assertTrue(turnstate.of(p).ask_open)
        self.assertFalse(p.get("egress_allowlist"))
        self.assertEqual(
            [e["decision"] for e in events if e["type"] == "approval-done"],
            ["parked"])

    def test_a_denial_still_returns_the_blocked_result(self):
        p, env, _ = self._run("deny")
        self.assertFalse(env["ok"])
        self.assertIn("egress blocked", env["error"])
        self.assertFalse(p.get("egress_allowlist"))

    def test_a_yes_allows_the_domain_and_re_runs(self):
        p, env, _ = self._run("allow")
        self.assertIn("oauth2.googleapis.com", p.get("egress_allowlist") or [])

class TurnEndingTest(unittest.TestCase):
    def test_built_is_silent_and_the_tail_is_dropped_but_logged(self):
        e = loop.turn_ending(["Every step is ready - building it now."],
                             built="Built and checked.")
        self.assertEqual(e["content"], "")
        self.assertIn("building it now", e["dropped"])

    def test_a_fix_chain_is_silent(self):
        e = loop.turn_ending(["narration"], fix={"ticket_id": "t1"})
        self.assertEqual(e["content"], "")
        self.assertEqual(e["dropped"], "narration")

    def test_a_parked_question_is_the_last_word(self):
        e = loop.turn_ending(["lead-ish tail"], asked=True)
        self.assertEqual(e["content"], "")
        self.assertEqual(e["dropped"], "lead-ish tail")

    def test_stop_keeps_the_text_and_appends_the_note(self):
        e = loop.turn_ending(["partial"], stopped=True)
        self.assertIn("partial", e["content"])
        self.assertIn("Stopped", e["content"])
        self.assertEqual(e["dropped"], "")

    def test_a_takeover_stop_keeps_what_was_shown(self):
        e = loop.turn_ending(["partial"], stopped=True, interrupted=True)
        self.assertEqual(e["content"], "partial")
        self.assertEqual(e["dropped"], "")

    def test_the_narrator_goes_quiet_at_a_card_and_wakes_at_its_answer(self):
        seen = []
        n = loop.TurnNarrator(seen.append)
        n.text("before the card")
        n.landed()
        n.text(" words under an open card")
        self.assertEqual(n.tail(), [""])
        self.assertFalse(any(e.get("type") == "delta" and "under" in e.get("text", "")
                             for e in seen))
        n.resumed()
        n.text("the reply to the answer")
        self.assertEqual(n.tail(), ["the reply to the answer"])
        self.assertTrue(any(e.get("type") == "delta" and "reply" in e.get("text", "")
                            for e in seen))

        m = loop.TurnNarrator(lambda e: None, workflow_id="p_narr")
        loop.landed("p_narr"); m.text("x"); self.assertEqual(m.tail(), [""])
        loop.resumed("p_narr"); m.text("y"); self.assertEqual(m.tail(), ["y"])
        loop._NARRATORS.pop("p_narr", None)

    def test_plain_text_is_the_reply(self):
        e = loop.turn_ending(["hello ", "there"])
        self.assertEqual(e["content"], "hello there")

    def test_empty_after_a_failed_tool_surfaces_a_message(self):
        out = {"last_tool": "save_plan", "last_ok": False,
               "last_error": "node 'x' type must be from [...]"}
        msg = loop.turn_ending([], outcome=out)["content"]
        self.assertIn("couldn't finish", msg)
        self.assertNotIn("save_plan", msg)
        self.assertNotIn("type must be", msg)

    def test_empty_after_a_successful_tool_stays_empty(self):
        out = {"last_tool": "list_nodes", "last_ok": True}
        self.assertEqual(loop.turn_ending([], outcome=out)["content"], "")

    def test_the_order_is_the_written_one(self):
        e = loop.turn_ending(["t"], built="Done.", fix={"x": 1}, asked=True,
                             stopped=True)
        self.assertEqual(e, {"content": "", "dropped": "t"})

class UsageLimitIsTheTurnsMessageTest(unittest.TestCase):
    def test_codex_words_stand_and_are_not_retried(self):
        from agent import codex_engine, transport
        text = ("You've hit your usage limit. Upgrade to Pro, or try again at 8:24 PM.")
        msg = loop.usage_limit_message(text)
        self.assertTrue(msg.startswith("The AI service stopped this turn: You've hit"))
        self.assertIn("8:24 PM.", msg)
        err = codex_engine._fail("p_limit", text)
        self.assertEqual(str(err), msg)
        self.assertFalse(loop.is_transient_transport(err))

    def test_the_sdk_reset_time_is_readable(self):
        msg = loop.usage_limit_message("Claude AI usage limit reached|1789424640")
        self.assertIn("usage limit reached - it resets at", msg)
        self.assertNotIn("1789424640", msg)
        self.assertEqual(loop.usage_limit_message("API Error: Overloaded"), "")

class ThinkingLineTest(unittest.TestCase):
    def test_a_tool_result_ends_with_a_thinking_event(self):
        from agent import actions
        p = _workflow([], pid="p_thinking")
        events = []
        actions.execute(p, {"name": "list_samples", "input": {}}, events.append)
        self.assertEqual(events[-1], {"type": "thinking"})

class PortTypeNormalisationTest(unittest.TestCase):
    def test_synonyms_map_to_the_vocabulary(self):
        from agent import node_tools as nt
        self.assertEqual(nt.canon_port_type("list"), "list")
        self.assertEqual(nt.canon_port_type("List[str]"), "list")
        self.assertEqual(nt.canon_port_type("array"), "list")
        self.assertEqual(nt.canon_port_type("string"), "text")
        self.assertEqual(nt.canon_port_type("int"), "number")
        self.assertEqual(nt.canon_port_type("float"), "number")
        self.assertEqual(nt.canon_port_type("bool"), "boolean")
        self.assertEqual(nt.canon_port_type("object"), "record")
        self.assertEqual(nt.canon_port_type("json"), "record")

    def test_valid_types_pass_through_and_nonsense_is_untouched(self):
        from agent import node_tools as nt
        self.assertEqual(nt.canon_port_type("text"), "text")
        self.assertEqual(nt.canon_port_type("record"), "record")
        self.assertEqual(nt.canon_port_type("bogus"), "bogus")

    def test_save_plan_persists_a_list_typed_plan(self):
        from agent import node_tools as nt
        p = _workflow([], pid="p_listplan")
        p["intent"] = {"summary": "Fetch and post."}
        plan = {"summary": "Fetch headlines, post them.",
                "nodes": [
                    {"name": "Fetch headlines", "type": "code", "inputs": [],
                     "outputs": [{"name": "headlines", "type": "list"}]},
                    {"name": "Post it", "type": "connector",
                     "external_impact": "Posts a message",
                     "inputs": [{"name": "headlines", "type": "list"}],
                     "outputs": [{"name": "result", "type": "object"}]}]}
        r = nt.tool_save_plan(p, plan)
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(p.get("plan"))
        self.assertEqual(p["plan"]["nodes"][0]["outputs"][0]["type"], "list")
        self.assertEqual(p["plan"]["nodes"][1]["outputs"][0]["type"], "record")

class TransportSelectionTest(unittest.TestCase):
    def test_subscription_builder_selects_the_sdk_transport(self):
        from storage import settings
        from storage import secrets_store
        settings.update({"master_ai": {"model": "m-sub"},
                         "providers": [{"adapter": "anthropic",
                                        "auth": "claude-subscription",
                                        "use": "builder", "key_name": "k_sub",
                                        "models": [{"name": "m-sub"}]}]})
        try:
            tr = transport.for_settings()
            self.assertIsInstance(tr, transport.SdkTransport)
            self.assertEqual(tr.auth, "claude-subscription")
            self.assertEqual(tr.credential, "")
        finally:
            settings.update({"master_ai": {"model": ""}, "providers": []})

    def test_anthropic_api_key_builder_selects_the_sdk_transport(self):
        from storage import settings
        from storage import secrets_store
        settings.update({"master_ai": {"model": "m-api"},
                         "providers": [{"adapter": "anthropic",
                                        "auth": "api-key", "use": "builder",
                                        "key_name": "k_api",
                                        "models": [{"name": "m-api"}]}]})
        secrets_store.set_secret("k_api", "sk-x", secrets_store.OWNER_APP)
        try:
            tr = transport.for_settings()
            self.assertIsInstance(tr, transport.SdkTransport)
            self.assertEqual(tr.auth, "api-key")
            self.assertEqual(tr.credential, "sk-x")
        finally:
            settings.update({"master_ai": {"model": ""}, "providers": []})

    def test_non_anthropic_api_key_builder_is_refused(self):
        from storage import settings
        from storage import secrets_store
        settings.update({"master_ai": {"model": "m-oai"},
                         "providers": [{"adapter": "openai",
                                        "auth": "api-key", "use": "builder",
                                        "key_name": "k_oai",
                                        "models": [{"name": "m-oai"}]}]})
        secrets_store.set_secret("k_oai", "sk-o", secrets_store.OWNER_APP)
        try:
            with self.assertRaises(transport.TransportError):
                transport.for_settings()
        finally:
            settings.update({"master_ai": {"model": ""}, "providers": []})

    def test_sdk_env_carries_the_right_credential(self):
        from agent import loop
        api = loop._sdk_env(transport.SdkTransport("m", "sk-x", auth="api-key"))
        self.assertEqual(api["ANTHROPIC_API_KEY"], "sk-x")
        self.assertEqual(api["CLAUDE_CODE_OAUTH_TOKEN"], "")
        sub = loop._sdk_env(
            transport.SdkTransport("m", "", auth="claude-subscription"))
        self.assertEqual(sub["CLAUDE_CODE_OAUTH_TOKEN"], "")
        self.assertEqual(sub["ANTHROPIC_API_KEY"], "")

    def test_the_engine_env_is_sealed(self):
        import os
        from unittest import mock
        import config
        from agent import loop
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://elsewhere",
                                          "CLAUDE_CODE_USE_BEDROCK": "1",
                                          "OPENAI_API_KEY": "sk-theirs",
                                          "CODEX_HOME": "/their/codex",
                                          "CLAUDE_CODE_ENTRYPOINT": "keep",
                                          "HTTPS_PROXY": "http://proxy:3128"}):
            env = loop._sdk_env(transport.SdkTransport("m", "sk-x", auth="api-key"))
        for k in ("ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                  "OPENAI_API_KEY", "CODEX_HOME"):
            self.assertEqual(env[k], "", k)
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", env)
        self.assertNotIn("HTTPS_PROXY", env)
        for k, v in loop.QUIET_SWITCHES.items():
            self.assertEqual(env[k], v)
        cfg = env["CLAUDE_CONFIG_DIR"]
        self.assertTrue(cfg.startswith(str(config.DATA_DIR)), cfg)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-x")

class DrivingMessageFullLengthTest(unittest.TestCase):
    def test_long_entries_are_replayed_whole(self):
        long_msg = "RULE " + ("x" * 6000) + " END-OF-RULES"
        p = _workflow([], pid="p_fullmsg")
        _bootstrap.said(p, "user", "old " + ("y" * 6000))
        _bootstrap.said(p, "user", long_msg)
        body = context.build(p, long_msg)[0]["content"]
        self.assertIn("END-OF-RULES", body)
        self.assertNotIn("older detail trimmed", body)
        self.assertIn("y" * 6000, body)

class AskNeverReAsksAnEnvironmentValueTest(unittest.TestCase):
    def test_attached_value_is_already_stored(self):
        from storage import environments
        environments.save({"id": "env_ask1", "name": "Google Drive", "description": "",
                           "group": "", "variables": [
                               {"name": "service_account_email_address", "secret": False,
                                "value": "svc@example.iam.gserviceaccount.com"}]})
        p = _workflow([], pid="p_ask_env1")
        p["environment_ids"] = ["env_ask1"]
        env = actions.execute(p, _call("ask_user", {
            "question": "Confirming the address to share with.",
            "fields": [{"name": "service_account_email_address",
                        "label": "Service account email", "type": "text"}]}),
            lambda e: None)
        self.assertFalse(env["ok"])
        self.assertIn("already stored", env["error"])

    def test_unattached_holder_means_attach_it(self):
        from storage import environments
        environments.save({"id": "env_ask2", "name": "Sheets creds", "description": "",
                           "group": "", "variables": [
                               {"name": "sheet_owner_email", "secret": False,
                                "value": "owner@example.com"}]})
        p = _workflow([], pid="p_ask_env2")
        env = actions.execute(p, _call("ask_user", {
            "question": "What is the sheet owner's email?",
            "fields": [{"name": "sheet_owner_email", "label": "Owner email",
                        "type": "text"}]}), lambda e: None)
        self.assertFalse(env["ok"])
        self.assertIn("attach", env["error"])
        self.assertIn("Sheets creds", env["error"])
        self.assertNotIn("owner@example.com", json.dumps(env))

if __name__ == "__main__":
    unittest.main()

class CellApprovalLoopTest(unittest.TestCase):
    def _blocked(self, host):
        return {"ok": False, "error": f"<urlopen error egress blocked: "
                                      f"('{host}', 443) is not on the "
                                      "allowlist>"}

    def _run(self, results, decision="allow"):
        from unittest import mock

        from agent import interactions
        p = _workflow([], pid="p_apprloop")
        seq = list(results)
        calls = []

        def _cell(workflow, **kw):
            calls.append(kw)
            return seq.pop(0) if seq else {"ok": True, "cell": "Save"}

        stub = dict(actions._fns(), run_cell=_cell)

        def _wait(iid, timeout=600, **kw):
            interactions._pending.pop(iid, None)
            return decision

        events = []
        with mock.patch.object(actions, "_fns", lambda: stub), \
             mock.patch.object(interactions, "wait_sync", _wait), \
             _bootstrap.shown_through(p["id"], events.append):
            env = actions.execute(
                p, _call("run_cell", {"name": "Save", "code": "x=1"}),
                events.append)
        return p, env, events, calls

    def test_two_hosts_resolve_in_one_tool_call(self):
        p, env, events, calls = self._run(
            [self._blocked("oauth2.googleapis.com"),
             self._blocked("sheets.googleapis.com")])
        self.assertTrue(env["ok"], env)
        self.assertEqual(sorted(p.get("egress_allowlist") or []),
                         ["oauth2.googleapis.com", "sheets.googleapis.com"])
        self.assertEqual(len([e for e in events if e["type"] == "shown"
                          and e["item"].get("request") == "approval"]), 2)
        self.assertEqual(len(calls), 3)

    def test_every_blocked_host_gets_its_own_card(self):
        hosts = [self._blocked(f"h{i}.example.com") for i in range(10)]
        p, env, events, _ = self._run(hosts)
        self.assertEqual(len([e for e in events if e["type"] == "shown"
                          and e["item"].get("request") == "approval"]), 10)

    def test_the_card_names_its_step_so_a_decline_can_be_read_back(self):
        from agent import node_tools
        p, env, events, _ = self._run([self._blocked("x.example.com")],
                                      decision="deny")
        card = [e["item"] for e in events if e["type"] == "shown"
                and e["item"].get("request") == "approval"][0]
        self.assertEqual(card["payload"]["step"], "Save")
        self.assertEqual(node_tools.declined_steps(p), {"Save"})

    def test_an_allowed_card_records_no_decline(self):
        from agent import node_tools
        p, _, _, _ = self._run([self._blocked("x.example.com")])
        self.assertEqual(node_tools.declined_steps(p), set())

class BrowserRelaunchApprovalTest(unittest.TestCase):
    def _run(self, decision):
        from unittest import mock

        from agent import interactions
        p = _workflow([], pid="p_browser_appr")
        seen = []

        def _cell(workflow, **kw):
            seen.append(kw)
            if kw.get("browser_ok"):
                return {"ok": True, "cell": "Fetch posts", "output": {"n": 1}}
            return {"error": "opening the real browser again needs the user's go-ahead",
                    "needs_browser_ok": "Fetch posts"}

        stub = dict(actions._fns(), run_cell=_cell)

        def _wait(iid, timeout=600, **kw):
            interactions._pending.pop(iid, None)
            return decision

        events = []
        with mock.patch.object(actions, "_fns", lambda: stub), \
             mock.patch.object(interactions, "wait_sync", _wait), \
             _bootstrap.shown_through(p["id"], events.append):
            env = actions.execute(
                p, _call("run_cell", {"name": "Fetch posts", "code": "x=1"}),
                events.append)
        return env, events, seen

    def test_a_yes_re_runs_with_permission(self):
        env, events, seen = self._run("allow")
        self.assertTrue(env["ok"], env)
        self.assertTrue(seen[-1].get("browser_ok"))
        card = [e for e in events if e["type"] == "shown"
                          and e["item"].get("request") == "approval"][0]
        self.assertIn("Fetch posts", card["item"]["payload"]["title"])
        self.assertNotIn("browser_ok", card["item"]["payload"]["title"])

    def test_a_no_keeps_the_browser_shut(self):
        env, events, seen = self._run("deny")
        self.assertFalse(env["ok"])
        self.assertIn("recorded run", env["error"])
        self.assertFalse(any(k.get("browser_ok") for k in seen))

    def test_no_answer_ends_the_turn_and_tells_the_agent_nothing(self):
        env, _, seen = self._run(None)
        self.assertTrue(env["ok"])
        self.assertEqual(env.get("data"), {"ok": True, "parked": True})
        self.assertNotIn("error", env)
        self.assertFalse(any(k.get("browser_ok") for k in seen))

class ProvenContextTest(unittest.TestCase):
    def test_recorded_cells_and_their_output_names_reach_the_turn(self):
        from agent import cells
        p = _workflow([], pid="p_proven")
        cells.record("p_proven", "Fetch Reddit posts", "c", {},
                     {"posts": [{"t": "a"}]}, True, 0.1)
        cells.record("p_proven", "Judge posts", "prompt", {},
                     {"verdicts": [], "note": "x"}, True, 0.1, kind="ai")
        body = context.build(p, "carry on")[0]["content"]
        self.assertIn("already tested", body)
        self.assertIn("Fetch Reddit posts", body)
        self.assertIn("posts", body)
        self.assertIn("[AI]", body)
        self.assertIn("$recorded", body)

    def test_values_never_ride_along(self):
        from agent import cells
        p = _workflow([], pid="p_proven2")
        cells.record("p_proven2", "Fetch", "c", {},
                     {"secret_looking_payload": "hunter2-and-a-long-body"},
                     True, 0.1)
        body = context.build(p, "go")[0]["content"]
        self.assertIn("secret_looking_payload", body)
        self.assertNotIn("hunter2", body)

    def test_a_failed_only_cell_is_not_claimed_as_proven(self):
        from agent import cells
        p = _workflow([], pid="p_proven3")
        cells.record("p_proven3", "Broken", "c", {}, None, False, 0.1)
        body = context.build(p, "go")[0]["content"]
        self.assertNotIn("already proven", body)

    def test_no_cells_adds_nothing(self):
        p = _workflow([], pid="p_proven4")
        self.assertNotIn("already proven",
                         context.build(p, "go")[0]["content"])

def _log_rows(pid, kind):
    import config
    path = config.DATA_DIR / "logs" / f"{pid}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") == kind:
            rows.append(r)
    return rows

class TurnTelemetryTest(unittest.TestCase):
    def test_message_start_is_the_call_boundary(self):
        from agent import transport
        self.assertIsNone(transport._call_start_usage(
            {"type": "content_block_delta"}))
        u = transport._call_start_usage(
            {"type": "message_start",
             "message": {"usage": {"input_tokens": 3,
                                   "cache_read_input_tokens": 120_000}}})
        self.assertEqual(u["cache_read_input_tokens"], 120_000)

        self.assertEqual(transport._call_start_usage(
            {"type": "message_start"}), {})

    def test_the_audited_build_would_now_count_correctly(self):
        from agent import loop
        stats: dict = {}
        c = loop.CallCounter(stats)
        c.call_started({"input_tokens": 4, "cache_read_input_tokens": 24_000})
        for _ in range(3):
            c.tools_called(["load_skill"])
        c.call_started({"cache_read_input_tokens": 172_000})
        c.tools_called(["run_cell"])
        self.assertEqual(stats["model_calls"], 2)
        self.assertEqual(stats["batched_calls"], 1)
        self.assertEqual(stats["tool_calls"],
                         {"load_skill": 3, "run_cell": 1})
        self.assertEqual((stats["ctx_first"], stats["ctx_last"]),
                         (24_004, 172_000))

    def test_a_single_tool_call_is_not_batched(self):
        from agent import loop
        stats: dict = {}
        c = loop.CallCounter(stats)
        c.call_started({})
        c.tools_called(["run_cell"])
        c.call_started({})
        c.tools_called(["save_plan"])
        self.assertEqual(stats["model_calls"], 2)
        self.assertEqual(stats["batched_calls"], 0)

    def test_parallel_blocks_in_one_response_count_once(self):
        from agent import loop
        a, b = {}, {}
        c1, c2 = loop.CallCounter(a), loop.CallCounter(b)
        c1.call_started({})
        c1.tools_called(["x", "y", "z"])
        c2.call_started({})
        for n in ("x", "y", "z"):
            c2.tools_called([n])
        self.assertEqual(a["batched_calls"], b["batched_calls"], 1)
        self.assertEqual(a["tool_calls"], b["tool_calls"])

    def test_the_wire_total_survives_a_turn_that_never_gets_a_result(self):
        from agent import loop, transport
        stats: dict = {}
        c = loop.CallCounter(stats)
        c.call_started({"input_tokens": 4, "cache_creation_input_tokens": 20_000,
                        "cache_read_input_tokens": 24_000})
        c.call_ended({"output_tokens": 900})
        c.call_started({"input_tokens": 2, "cache_read_input_tokens": 46_000})
        c.call_ended({"output_tokens": 1_500})
        self.assertEqual(c.wire_usage, {
            "input_tokens": 6, "cache_creation_input_tokens": 20_000,
            "cache_read_input_tokens": 70_000, "output_tokens": 2_400})

        self.assertIsNone(transport._call_end_usage({"type": "message_start"}))
        self.assertIsNone(transport._call_start_usage({"type": "message_delta"}))
        self.assertEqual(
            transport._call_end_usage({"type": "message_delta",
                                       "usage": {"output_tokens": 7}}),
            {"output_tokens": 7})

    def test_a_turn_with_no_calls_at_all_still_reads_as_unknown(self):
        from agent import loop
        c = loop.CallCounter({})
        c.call_started(None)
        c.call_ended(None)
        self.assertEqual(c.wire_usage, {})

    def test_no_call_boundary_ever_reads_as_zero(self):
        from agent import loop
        stats: dict = {}
        loop.CallCounter(stats)
        self.assertIsNone(stats["model_calls"])
        self.assertIsNone(stats["batched_calls"])

    def test_tool_weight_ledger_accumulates_per_tool(self):
        from agent import loop
        bucket: dict = {}
        loop.note_tool_bytes(bucket, "run_cell",
                             loop.measure_args({"code": "x" * 100}),
                             {"ok": True, "output": {"y": "z" * 200}})
        loop.note_tool_bytes(bucket, "run_cell",
                             loop.measure_args({"code": "x" * 50}),
                             {"ok": True, "output": {}})
        c_in, c_out = bucket["tool_bytes"]["run_cell"]
        self.assertGreater(c_in, 150)
        self.assertGreater(c_out, 200)
        self.assertEqual(bucket["last_chars_in"], len('{"code": "'
                                                      + "x" * 50 + '"}'))

    def test_arguments_are_weighed_before_the_tool_rewrites_them(self):
        from agent import loop
        args = {"plan": {"nodes": [{"name": "Fetch"}]}}
        sent = loop.measure_args(args)
        args["plan"]["nodes"][0]["code"] = "x" * 50_000
        bucket: dict = {}
        loop.note_tool_bytes(bucket, "save_plan", sent, {"ok": True})
        self.assertEqual(bucket["last_chars_in"], sent)
        self.assertLess(bucket["last_chars_in"], 1_000)
        self.assertLess(bucket["tool_bytes"]["save_plan"][0], 1_000)

    def test_summary_reports_unknown_counts_as_none_never_zero(self):
        from agent import loop
        p = _workflow([], pid="p_tel1")
        loop._turn_summary_log(p, "chat",
                               {"rounds": 4, "t0": loop._time_now(),
                                "tool_calls": {"run_cell": 2}},
                               asked=False, built=False, stopped=False)
        rows = _log_rows(p["id"], "turn-summary")
        self.assertIsNone(rows[-1]["model_calls"])
        self.assertIsNone(rows[-1]["batched_calls"])
        self.assertNotIn("multi_rounds", rows[-1])

    def test_a_turn_with_no_usage_records_the_gap(self):
        from agent import loop
        p = _workflow([], pid="p_tel2")
        loop._usage_missing_log(p, "chat")
        rows = _log_rows(p["id"], "usage-missing")
        self.assertEqual(rows[-1]["turn_kind"], "chat")

    def test_heaviest_tools_are_named_by_weight(self):
        from agent import loop
        p = _workflow([], pid="p_tel3")
        loop._turn_summary_log(
            p, "chat",
            {"rounds": 2, "t0": loop._time_now(), "model_calls": 2,
             "batched_calls": 1, "ctx_first": 24_000, "ctx_last": 172_000,
             "tool_chars_in": 30, "tool_chars_out": 900,
             "tool_bytes": {"run_cell": (10, 800), "list_nodes": (20, 100)}},
            asked=False, built=False, stopped=False)
        row = _log_rows(p["id"], "turn-summary")[-1]
        self.assertEqual(row["heaviest_tools"][0]["tool"], "run_cell")
        self.assertEqual(row["ctx_last"], 172_000)
        self.assertEqual(row["batched_calls"], 1)

class StaticPrefixTest(unittest.TestCase):
    def _workflows(self):
        a = _workflow([], pid="p_prefix_a")
        b = _workflow([{"id": "n1", "name": "Fetch", "type": "code",
                       "config": {"code": "write_output('y', 1)"},
                       "outputs": [{"name": "y", "type": "number"}]}],
                     pid="p_prefix_b")
        b["intent"] = {"summary": "something else entirely",
                       "facts": ["a fact that must not reach the prompt"]}
        b["loaded_skills"] = ["connectors/browser"]
        b["samples"] = [{"name": "x.txt", "ref": "blob:" + "a" * 64}]
        return a, b

    def test_system_prompt_is_identical_across_workflows(self):
        a, b = self._workflows()
        self.assertEqual(prompt.system(a), prompt.system(b),
                         "the system prompt varies by workflow - it would stop caching")

    def test_system_prompt_does_not_move_as_a_workflow_changes(self):
        a, _ = self._workflows()
        before = prompt.system(a)
        a["nodes"] = [{"id": "n9", "name": "Later", "type": "code"}]
        a["chat"] = [{"role": "user", "text": "hello"}]
        a.setdefault("loaded_skills", []).append("connectors/slack")
        self.assertEqual(before, prompt.system(a))

    def test_tool_schemas_carry_nothing_installation_specific(self):
        import config
        blob = json.dumps(actions.schemas())
        self.assertNotIn(str(config.DATA_DIR), blob)
        self.assertNotIn(str(config.ROOT), blob)
        self.assertNotIn("proj_", blob)

class AskUserOptionsTest(unittest.TestCase):
    def test_an_ordinary_question_still_asks(self):
        from agent import interactions
        p = _workflow([], pid="p_force_opt2")
        old = interactions.CARD_PATIENCE
        interactions.CARD_PATIENCE = 0.05
        try:
            env = actions.execute(p, _call("ask_user", {
                "question": "Which sheet should I write to?",
                "options": ["The tracker", "A new one"]}), lambda e: None)
        finally:
            interactions.CARD_PATIENCE = old
        self.assertTrue(env["ok"], env)

class ReplayDietAfterGreenTest(unittest.TestCase):
    def _talky(self, pid, status):
        from agent import transcript
        p = _workflow([], pid=pid)
        p["plan"] = {"status": status, "summary": "Do.", "nodes": []}
        for i in range(30):
            transcript.append_message(p, "user", f"message {i} " + "x" * 40)
            transcript.append_message(p, "assistant", f"reply {i} " + "y" * 40)
        return p

    def test_a_built_workflow_replays_the_whole_chat_too(self):
        p = self._talky("p_diet1", "built")
        body = context.build(p, "add a step")[0]["content"]
        self.assertIn("message 20", body)
        self.assertIn("message 25", body)
        self.assertNotIn("older entries not replayed", body)

    def test_exploration_keeps_the_full_window(self):
        p = self._talky("p_diet2", "draft")
        body = context.build(p, "carry on")[0]["content"]
        self.assertIn("message 20", body)

class BuilderCwdTest(unittest.TestCase):
    def test_the_sdk_turn_runs_in_isolation_mode(self):
        from agent import loop
        kw = loop.sdk_option_kwargs("sys", object(), [], [], None, {}, None)
        self.assertEqual(kw["setting_sources"], [])
        self.assertEqual(kw["skills"], [])
        self.assertEqual(kw["cwd"], str(loop.sdk_cwd()))
        self.assertEqual(kw["tools"], [])

        self.assertEqual(kw["extra_args"], {"no-session-persistence": None})

    def test_sdk_cwd_is_outside_the_checkout(self):
        import config
        from agent import loop
        d = loop.sdk_cwd()
        self.assertTrue(d.is_dir())
        self.assertFalse(str(d.resolve()).startswith(str(config.ROOT.resolve())))
        for parent in [d.resolve(), *d.resolve().parents]:
            self.assertFalse((parent / "CLAUDE.md").exists(), parent)

class StoredSecretNeverReAskedTest(unittest.TestCase):
    def test_refused_then_escaped(self):
        from storage import secrets_store
        p = _workflow([], pid="p_secret_reask")
        p["variables"] = [{"name": "api_secret", "secret": True, "value": True,
                           "persistent": True}]
        secrets_store.set_secret("api_secret", "sk-live-REASK", secrets_store.workflow_owner("p_secret_reask"))
        try:
            env = actions.execute(p, _call("ask_user", {
                "question": "Paste it again just to be sure:",
                "secret": True, "secret_name": "api_secret"}), lambda e: None)
            self.assertFalse(env["ok"])
            self.assertIn("already in the secret store", env["error"])
            self.assertNotIn("REASK", json.dumps(env))
            env = actions.execute(p, _call("ask_user", {
                "question": "You said it changed - paste the new one:",
                "secret": True, "secret_name": "api_secret",
                "allow_stored": True}), lambda e: None)
            self.assertTrue(env["ok"], env)
        finally:
            secrets_store.delete_secret("api_secret", secrets_store.workflow_owner("p_secret_reask"))

    def test_an_unset_secret_still_asks(self):
        p = _workflow([], pid="p_secret_fresh")
        env = actions.execute(p, _call("ask_user", {
            "question": "Paste the key:", "secret": True,
            "secret_name": "never_stored_key"}), lambda e: None)
        self.assertTrue(env["ok"], env)

class FolderApprovalTest(unittest.TestCase):
    def _run(self, decision):
        from unittest import mock

        from agent import interactions
        p = _workflow([], pid=f"p_fold_{decision or 'none'}")
        blocked = {"ok": False, "error": "PermissionError: path blocked: '/Users/me/Documents/reports/q3.csv' is outside this workflow's allowed folders"}
        calls = {"n": 0}

        def cell(workflow, **kw):
            calls["n"] += 1
            return blocked if calls["n"] == 1 else {"ok": True}
        stub = dict(actions._fns(), run_cell=cell)
        cards = []

        def _wait(iid, timeout=None, **kw):
            interactions._pending.pop(iid, None)
            return decision

        def emit(ev):
            if ev.get("type") == "shown" \
                    and (ev.get("item") or {}).get("request") == "approval":
                cards.append(ev)
        with mock.patch.object(actions, "_fns", lambda: stub), \
             mock.patch.object(interactions, "wait_sync", _wait), \
             _bootstrap.shown_through(p["id"], emit):
            env = actions.execute(
                p, _call("run_cell", {"name": "read_it", "code": "x=1",
                                      "reason": "to read the quarter's file"}),
                emit)
        return p, env, cards

    def test_a_yes_allows_the_folder_and_re_runs(self):
        p, env, cards = self._run("allow")
        self.assertTrue(env["ok"], env)
        self.assertEqual(len(cards), 1)
        title = cards[0]["item"]["payload"]["title"]
        self.assertIn("/Users/me/Documents/reports", title)
        self.assertNotIn("q3.csv", title)
        self.assertEqual(p["path_allowlist"], ["/Users/me/Documents/reports"])

    def test_a_no_hands_back_the_refusal(self):
        p, env, _ = self._run("deny")
        self.assertFalse(env["ok"])
        self.assertIn("path blocked", env["error"])
        self.assertFalse(p.get("path_allowlist"))

    def test_no_answer_ends_the_turn_and_tells_the_agent_nothing(self):
        from agent import turnstate
        p, env, _ = self._run(None)
        self.assertTrue(env["ok"])
        self.assertEqual(env.get("data"), {"ok": True, "parked": True})
        self.assertNotIn("error", env)
        self.assertTrue(turnstate.of(p).ask_open)

class ClosingMessageRepeatsNothingTest(unittest.TestCase):
    def test_the_tail_starts_after_the_last_card(self):
        n = loop.TurnNarrator(lambda ev: None, "p_tail1")
        n.text("First I look at the site.")
        loop.landed("p_tail1")
        loop.resumed("p_tail1")
        n.tool_gap()
        n.text("Then the sheet.")
        loop.landed("p_tail1")
        loop.resumed("p_tail1")
        n.tool_gap()
        n.text("All done.")
        self.assertEqual(loop.turn_ending(n.tail())["content"], "All done.")
        self.assertNotIn("First I look", "".join(n.tail()))

        m = loop.TurnNarrator(lambda ev: None, "p_tail2")
        m.text("Only this.")
        self.assertEqual(loop.turn_ending(m.tail())["content"], "Only this.")
        loop._NARRATORS.pop("p_tail1", None); loop._NARRATORS.pop("p_tail2", None)
