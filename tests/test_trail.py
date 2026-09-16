# Tests for the working trail: every turn's words, calls and results kept whole and replayed into the next turn on either engine
from __future__ import annotations

import unittest
from unittest import mock

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from agent import actions, context, loop, trail

class TrailTest(unittest.TestCase):
    def test_a_turn_is_kept_in_order_and_replayed_whole(self):
        p = _workflow([], pid="p_trail1")
        pid = p["id"]
        trail.begin(pid, "t1", "find the feed", "chat")
        n = loop.TurnNarrator(lambda ev: None, pid)
        n.text("Looking at ")
        n.text("the site.")
        n.tool_gap()
        env = actions.execute(p, {"name": "list_samples", "input": {}},
                              lambda ev: None)
        self.assertTrue(env["ok"], env)
        n.text("No samples yet.")
        trail.end(pid)
        turns = trail.turns(pid)
        self.assertEqual([t["turn_id"] for t in turns], ["t1"])
        kinds = [i["kind"] for i in turns[0]["items"]]
        self.assertEqual(kinds, ["user", "text", "tool", "text"])
        self.assertEqual(turns[0]["items"][1]["text"], "Looking at the site.")
        self.assertEqual(turns[0]["items"][2]["name"], "list_samples")
        self.assertEqual(turns[0]["items"][2]["result"], env)
        body = context.build(p, "next")[0]["content"]
        self.assertIn("[working memory]", body)
        self.assertIn("user: find the feed", body)
        self.assertIn("you said: Looking at the site.", body)
        self.assertIn("you called list_samples", body)
        self.assertIn("No samples yet.", body)
        self.assertIn("user: next", body)

    def test_an_earlier_result_is_one_line_and_opens_whole(self):
        from agent import node_tools
        p = _workflow([], pid="p_trail_collapse")
        pid = p["id"]
        big = {"action": "run_cell", "ok": True,
               "data": {"output": {"rows": ["r" * 5000], "count": 1}, "note": "recorded"}}
        trail.begin(pid, "t1", "fetch", "chat")
        trail.tool(pid, "run_cell", {"name": "Fetch rows"}, big)
        trail.tool(pid, "load_skill", {"id": "browser"}, {"ok": True, "content": "GUIDE TEXT"})
        trail.end(pid)
        text, _, _ = trail.replay(pid)
        self.assertNotIn("r" * 5000, text)
        self.assertIn("you called run_cell", text)
        self.assertIn("passed", text)
        self.assertIn("outputs: rows, count", text)
        self.assertIn("GUIDE TEXT", text)
        rid = next(i["id"] for i in trail.turns(pid)[0]["items"] if i.get("name") == "run_cell")
        self.assertIn(f"read_earlier_result({rid})", text)
        got = node_tools.tool_read_earlier_result(p, id=str(rid))
        self.assertEqual(got["results"][0]["result"], {**big, "result_id": rid})
        self.assertIn("error", node_tools.tool_read_earlier_result(p, id="999999"))

    def test_a_result_carries_its_id_and_size_and_opens_by_path(self):
        from agent import node_tools, previews
        p = _workflow([], pid="p_trail_part")
        pid = p["id"]
        trail.begin(pid, "t1", "fetch", "chat")
        try:
            env = actions.execute(p, {"name": "list_samples", "input": {}},
                                  lambda ev: None, engine="codex")
            self.assertTrue(env["ok"], env)
            self.assertIsInstance(env["result_id"], int)
            self.assertEqual(env["size"], previews.size(env["data"]))
            self.assertNotIn("window_note", env["data"])

            with mock.patch.dict(previews.INLINE_CHARS, {"codex": 10}):
                small = actions.execute(p, {"name": "list_samples", "input": {}},
                                        lambda ev: None, engine="codex")
            self.assertIn("window_note", small["data"])
            self.assertGreater(small["size"], 10)
            whole = node_tools.tool_read_earlier_result(p, id=str(small["result_id"]))
            self.assertNotIn("window_note", whole["results"][0]["result"]["data"])
        finally:
            trail.end(pid)

        big = {"action": "run_cell", "ok": True,
               "data": {"output": {"rows": [{"name": "Ann", "city": "Oslo"},
                                            {"name": "Bob", "city": "Bergen"},
                                            {"name": "Cy", "city": "Oslo"}],
                                   "text": "one\ntwo oslo\nthree"}}}
        trail.begin(pid, "t2", "again", "chat")
        rid = trail.tool(pid, "run_cell", {"name": "Fetch"}, big)
        trail.end(pid)
        got = node_tools.tool_read_earlier_result(p, id=str(rid), path="data.output.rows[1].city")
        self.assertEqual(got["results"][0]["value"], "Bergen")
        got = node_tools.tool_read_earlier_result(p, id=str(rid), path="data.output.rows", find="oslo")
        self.assertEqual([r["name"] for r in got["results"][0]["value"]], ["Ann", "Cy"])
        self.assertIn("2 of 3 entries", got["results"][0]["matched"])
        got = node_tools.tool_read_earlier_result(p, id=str(rid), path="data.output.text", find="oslo")
        self.assertEqual(got["results"][0]["value"], ["line 2: two oslo"])
        got = node_tools.tool_read_earlier_result(p, id=str(rid), path="data.output.rows", offset=1, limit=1)
        self.assertEqual(got["results"][0]["value"], [{"name": "Bob", "city": "Bergen"}])
        self.assertIn("items 1-2 of 3", got["results"][0]["slice"])
        got = node_tools.tool_read_earlier_result(p, id=str(rid), path="data.output.nope")
        self.assertIn("rows, text", got["results"][0]["error"])

    def test_the_live_turn_is_never_replayed_into_itself(self):
        p = _workflow([], pid="p_trail2")
        trail.begin(p["id"], "t_live", "hello", "chat")
        try:
            body = context.build(p, "hello")[0]["content"]
            self.assertNotIn("[working memory]", body)
        finally:
            trail.end(p["id"])

    def test_the_window_trims_oldest_first_and_says_so(self):
        p = _workflow([], pid="p_trail3")
        pid = p["id"]
        for k in range(3):
            trail.begin(pid, f"t{k}", f"message {k}", "chat")
            trail.text(pid, "x" * 300)
            trail.end(pid)
        with mock.patch.object(trail, "TRAIL_WINDOW_CHARS", 800):
            text, since, first = trail.replay(pid)
        self.assertIn("message 2", text)
        self.assertIn("message 1", text)
        self.assertNotIn("message 0", text)
        self.assertIn("1 earlier turn not replayed in full", text)
        self.assertEqual(first, "message 1")
        with mock.patch.object(trail, "TRAIL_WINDOW_CHARS", 320):
            text, since, first = trail.replay(pid)

        self.assertIn("x" * 300, text)
        self.assertNotIn("user: message 2", text)
        self.assertIn("the first 1 items of this turn are not shown", text)

    def test_the_transcript_replay_covers_only_what_is_older_than_the_trail(self):
        from agent import transcript
        p = _workflow([], pid="p_trail4")
        pid = p["id"]
        transcript.append_message(p, "user", "OLD MESSAGE")
        transcript.append_message(p, "assistant", "old reply")
        transcript.append_message(p, "user", "second message")
        trail.begin(pid, "t_a", "second message", "chat")
        trail.text(pid, "working on it")
        trail.end(pid)
        body = context.build(p, "third")[0]["content"]
        self.assertIn("OLD MESSAGE", body)
        self.assertIn("old reply", body)

        self.assertEqual(body.count("second message"), 1)
        self.assertIn("working on it", body)

    def test_every_turn_is_kept(self):
        p = _workflow([], pid="p_trail5")
        pid = p["id"]
        for k in range(4):
            trail.begin(pid, f"t{k}", f"m{k}", "chat")
            trail.end(pid)
        self.assertEqual([t["turn_id"] for t in trail.turns(pid)], ["t0", "t1", "t2", "t3"])

    def test_a_pasted_secret_is_scrubbed_from_the_trail(self):
        p = _workflow([], pid="p_trail6")
        pid = p["id"]
        trail.begin(pid, "t_s", "the key is sk-ant-SECRETVALUE123", "chat")
        trail.tool(pid, "declare_variables",
                   {"entries": [{"name": "k", "value": "sk-ant-SECRETVALUE123"}]},
                   {"ok": True})
        trail.end(pid)
        n = trail.redact(pid, "sk-ant-SECRETVALUE123", "(secret k)")
        self.assertEqual(n, 2)
        text, _, _ = trail.replay(pid)
        self.assertNotIn("SECRETVALUE123", text)
        self.assertIn("(secret k)", text)

    def test_run_turn_opens_and_closes_the_trail_on_any_engine(self):
        p = _workflow([], pid="p_trail7")
        with mock.patch.object(loop, "_run_turn_sdk",
                               side_effect=RuntimeError("engine down")):
            with self.assertRaises(RuntimeError):
                loop.run_turn(p, "do the thing", lambda ev: None, "chat",
                              tr=mock.Mock(spec=[]))
        turns = trail.turns(p["id"])
        self.assertEqual(turns[0]["items"][0]["text"], "do the thing")
        self.assertNotIn(p["id"], trail._live)

if __name__ == "__main__":
    unittest.main()

class TransientTransportRetryTest(unittest.TestCase):
    def test_overloaded_retries_and_the_retry_sees_the_first_attempt(self):
        from agent import transport
        p = _workflow([], pid="p_retry1")
        calls = {"n": 0}
        def fake(workflow, msg, emit, kind, tr, max_turns=0):
            calls["n"] += 1
            trail.tool(workflow["id"], "load_skill", {"id": "browser"}, {"ok": True, "content": "GUIDE"})
            if calls["n"] < 3:
                raise transport.TransportError("the subscription builder failed: API Error: Overloaded")
            return {"content": "done", "kind": "text"}
        seen = []
        with mock.patch.object(loop, "_run_turn_sdk", side_effect=fake), \
                mock.patch.object(loop, "TURN_RETRY_WAITS", (0, 0, 0)):
            reply = loop.run_turn(p, "go", seen.append, "chat", tr=mock.Mock(spec=[]))
        self.assertEqual(reply["content"], "done")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(sum(1 for e in seen if "busy" in str(e.get("text"))), 2)
        ids = [t["turn_id"] for t in trail.turns(p["id"])]
        self.assertEqual(len(ids), 3)
        self.assertTrue(ids[1].endswith("-retry1"))

        text, _, _ = trail.replay(p["id"])
        self.assertIn("GUIDE", text)

    def test_a_setup_error_fails_at_once(self):
        from agent import transport
        p = _workflow([], pid="p_retry2")
        with mock.patch.object(loop, "_run_turn_sdk",
                               side_effect=transport.TransportError("no builder model is set up")), \
                mock.patch.object(loop, "TURN_RETRY_WAITS", (0, 0, 0)):
            with self.assertRaises(transport.TransportError):
                loop.run_turn(p, "go", lambda e: None, "chat", tr=mock.Mock(spec=[]))
        self.assertEqual(len(trail.turns(p["id"])), 1)

    def test_the_last_attempt_still_raises(self):
        from agent import transport
        p = _workflow([], pid="p_retry3")
        with mock.patch.object(loop, "_run_turn_sdk",
                               side_effect=transport.TransportError("API Error: Overloaded")), \
                mock.patch.object(loop, "TURN_RETRY_WAITS", (0, 0)):
            with self.assertRaises(transport.TransportError):
                loop.run_turn(p, "go", lambda e: None, "chat", tr=mock.Mock(spec=[]))
        self.assertEqual(len(trail.turns(p["id"])), 3)

class ChronologicalMemoryTest(unittest.TestCase):
    def test_older_conversation_comes_before_the_trail_and_the_message_last(self):
        from agent import transcript
        p = _workflow([], pid="p_trail_order")
        pid = p["id"]
        transcript.append_message(p, "user", "OLD FIX REQUEST")
        transcript.append_message(p, "assistant", "OLD FIX DONE")
        transcript.append_message(p, "user", "split the step")
        trail.begin(pid, "t_x", "split the step", "chat")
        trail.text(pid, "SPLITTING NOW")
        trail.end(pid)
        body = context.build(p, "skip testing")[0]["content"]
        self.assertLess(body.index("OLD FIX DONE"), body.index("SPLITTING NOW"))
        self.assertLess(body.index("SPLITTING NOW"), body.rindex("user: skip testing"))
        self.assertEqual(body.count("skip testing"), 1)

    def test_every_failed_turn_ends_in_the_one_wording(self):
        from agent import orchestrator, transport
        p = _workflow([], pid="p_trail_ending")
        a = orchestrator._turn_failed(p, RuntimeError("boom"))
        b = orchestrator._turn_failed(p, transport.TransportError("the key is not set"))
        c = loop.failed_ending("", "what it had said")
        for text in (a, b, c):
            self.assertIn("couldn't finish", text)
        self.assertNotIn("boom", a)
        self.assertIn("the key is not set", b)
        self.assertTrue(c.startswith("what it had said"))
