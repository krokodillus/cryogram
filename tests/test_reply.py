import threading
import time
import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from agent import interactions, reply, transcript

class EntryTest(unittest.TestCase):
    def test_entry_is_normalised_and_bounded(self):
        e = reply.entry_from({"kind": "nonsense", "text": 5, "cid": "x" * 100,
                              "files": ["a.pdf", 3], "answers": {"k": "v"}})
        self.assertEqual(e["kind"], "typed")
        self.assertEqual(e["text"], "5")
        self.assertEqual(len(e["cid"]), 64)
        self.assertEqual(e["files"], ["a.pdf", "3"])
        self.assertEqual(e["answers"], {"k": "v"})
        self.assertIsNone(reply.entry_from({"text": "x"})["answers"])

class LandAtRestTest(unittest.TestCase):
    def test_a_click_is_an_answer_never_a_message(self):
        p = _workflow([], pid="p_reply_click")
        transcript.append_request(p, "ask", {"question": "Which?",
                                             "options": ["A", "B"]}, iid="ask_1")
        drive, items = reply.land(p, reply.entry_from(
            {"kind": "click", "iid": "ask_1", "text": "B", "cid": "cid-1"}))
        self.assertEqual(drive, "B")
        self.assertEqual([i["kind"] for i in items], ["answer"])
        self.assertEqual(items[0]["shown"], "B")
        self.assertEqual(items[0]["cid"], "cid-1")
        self.assertFalse([i for i in transcript.items(p)
                          if i["kind"] == "message" and i.get("from") == "user"])

    def test_a_stale_click_lands_nothing(self):
        p = _workflow([], pid="p_reply_stale")
        transcript.append_request(p, "ask", {"question": "Q", "options": ["A"]},
                                  iid="ask_s")
        transcript.append_answer(p, "ask_s", "A", shown="A")
        drive, items = reply.land(p, reply.entry_from(
            {"kind": "click", "iid": "ask_s", "text": "A"}))
        self.assertEqual(drive, "")
        self.assertEqual(items, [])

    def test_typed_words_are_a_message_and_settle_what_was_open(self):
        p = _workflow([], pid="p_reply_typed")
        transcript.append_request(p, "ask", {"question": "Q", "options": ["A"]},
                                  iid="ask_t")
        drive, items = reply.land(p, reply.entry_from(
            {"kind": "typed", "text": "something else", "cid": "cid-t"}))
        self.assertEqual(drive, "something else")
        kinds = [i["kind"] for i in items]
        self.assertIn("answer", kinds)
        self.assertIn("message", kinds)
        msg = next(i for i in items if i["kind"] == "message")
        self.assertEqual(msg["cid"], "cid-t")
        ans = next(i for i in items if i["kind"] == "answer")
        self.assertEqual(ans["shown"], {"via_message": True})

    def test_the_words_a_click_stands_for_are_composed_from_the_card(self):
        p = _workflow([], pid="p_reply_words")
        transcript.append_request(p, "approval", {"title": "Send it?", "detail": "d",
                                                  "scope": "s", "step": "Send"},
                                  iid="apr_1")
        drive, _ = reply.land(p, reply.entry_from({"kind": "click", "iid": "apr_1",
                                                   "text": "allow"}))
        self.assertEqual(drive, 'Yes to "Send it?" - go ahead and try that step again.')
        self.assertEqual([g["step"] for g in p["approval_grants"]], ["Send"])
        transcript.append_request(p, "ask", {"question": "Key?", "secret": True,
                                             "secret_name": "api_key"}, iid="sec_1")
        drive, _ = reply.land(p, reply.entry_from({"kind": "click", "iid": "sec_1",
                                                   "text": "(provided)"}))
        self.assertEqual(drive, 'I\'ve provided "api_key" securely.')
        self.assertTrue(next(v for v in p["variables"] if v["name"] == "api_key")["secret"])
        transcript.append_request(p, "ask", {
            "question": "Setup", "fields": [
                {"name": "url", "label": "Sheet link", "type": "text"},
                {"name": "cap", "label": "Cap", "type": "number"}]}, iid="fld_1")
        drive, items = reply.land(p, reply.entry_from(
            {"kind": "click", "iid": "fld_1", "answers": {"url": "https://s"}}))
        self.assertEqual(drive, "Here are the details - Sheet link: https://s. Not provided: Cap.")
        self.assertEqual(items[-1]["shown"], {"url": "https://s"})

    def test_an_upload_click_keeps_its_chips(self):
        p = _workflow([], pid="p_reply_upload")
        transcript.append_request(p, "ask", {"question": "File?", "upload": True},
                                  iid="up_1")
        _, items = reply.land(p, reply.entry_from(
            {"kind": "click", "iid": "up_1", "text": "I uploaded a file: a.csv",
             "files": ["a.csv"], "cid": "c-up"}))
        chips = next(i for i in items if i["kind"] == "message")
        self.assertEqual(chips["files"], ["a.csv"])
        self.assertEqual(chips["cid"], "c-up")
        self.assertEqual(chips["text"], "")

class DeliverTest(unittest.TestCase):
    def test_a_paused_card_is_resolved_with_the_cid(self):
        pid = "p_reply_paused"
        _workflow([], pid=pid)
        iid = interactions.create_sync("ask", {"workflow_id": pid, "question": "Q"},
                                       iid="ask_p")
        got = {}

        def waiter():
            got["answer"] = interactions.wait_sync(iid, timeout=5)
        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.05)
        out = reply.deliver(pid, reply.entry_from(
            {"kind": "click", "iid": "ask_p", "text": "A", "cid": "c-p"}))
        t.join(5)
        self.assertEqual(out, {"landed": "resolved"})
        self.assertEqual(got["answer"]["text"], "A")
        self.assertEqual(got["answer"]["via"], "click")
        self.assertEqual(got["answer"]["cid"], "c-p")

    def test_typed_words_answer_the_waiting_question_but_not_an_approval(self):
        pid = "p_reply_typed_pause"
        _workflow([], pid=pid)
        apr = interactions.create_sync("approval", {"workflow_id": pid, "title": "T"})

        self.assertIsNone(reply.deliver(pid, reply.entry_from({"kind": "typed", "text": "hi"})))
        interactions.park_workflow(pid)
        ask = interactions.create_sync("ask", {"workflow_id": pid, "question": "Q"})
        got = {}
        t = threading.Thread(target=lambda: got.update(
            a=interactions.wait_sync(ask, timeout=5)), daemon=True)
        t.start()
        time.sleep(0.05)
        self.assertEqual(reply.deliver(pid, reply.entry_from(
            {"kind": "typed", "text": "my answer"})), {"landed": "resolved"})
        t.join(5)
        self.assertEqual(got["a"]["via"], "typed")
        interactions.park_workflow(pid)
        self.assertIsNotNone(apr)

    def test_a_running_turn_parks_the_entry_and_a_typed_one_stops_the_turn(self):
        import turns
        pid = "p_reply_running"
        _workflow([], pid=pid)
        self.assertTrue(turns.begin(pid, "chat"))
        try:
            self.assertFalse(turns.should_stop(pid))
            out = reply.deliver(pid, reply.entry_from({"kind": "click", "iid": "int_none", "text": "allow"}))
            self.assertEqual(out, {"landed": "parked"})
            self.assertFalse(turns.should_stop(pid))
            out = reply.deliver(pid, reply.entry_from({"kind": "typed", "text": "later"}))
            self.assertEqual(out, {"landed": "parked"})
            self.assertIn("later", turns.unread_interjections(pid))
            self.assertTrue(turns.should_stop(pid))
        finally:
            turns.finish(pid)

class ShownThroughOneSeamTest(unittest.TestCase):
    def test_every_append_is_shown_while_a_turn_runs(self):
        from agent import node_tools
        p = _workflow([], pid="p_reply_shown")
        events = []
        with _bootstrap.shown_through(p["id"], events.append):
            node_tools._share_bytes(p, "out.csv", b"a,b\n1,2\n")
            transcript.append_message(p, "assistant", "here you go")
            transcript.append_request(p, "ask", {"question": "ok?"}, iid="ask_sh")
        kinds = [(e["item"]["kind"], (e["item"].get("files") or [{}])[0].get("name"))
                 for e in events if e.get("type") == "shown"]
        self.assertEqual(kinds, [("message", "out.csv"), ("message", None),
                                 ("request", None)])

        transcript.append_message(p, "user", "later")
        self.assertEqual(len(events), 3)

if __name__ == "__main__":
    unittest.main()

class OneSettleTest(unittest.TestCase):
    def test_the_exact_words_of_a_button_are_the_click(self):
        p = _workflow([], pid="p_settle_words")
        transcript.append_request(p, "ask", {"question": "Which?",
                                             "options": ["Test them for real now",
                                                         "Skip testing now"]}, iid="ask_w")
        drive, items = reply.land(p, reply.entry_from(
            {"kind": "typed", "text": " Skip testing now ", "cid": "c1"}))
        self.assertEqual(drive, "Skip testing now")
        self.assertEqual([i["kind"] for i in items], ["answer"])
        self.assertEqual(items[0]["shown"], "Skip testing now")
        self.assertFalse([i for i in transcript.items(p)
                          if i["kind"] == "message" and i.get("from") == "user"])

    def test_typed_words_matching_an_option_resolve_a_pause_as_a_click(self):
        pid = "p_settle_pause"
        _workflow([], pid=pid)
        ask = interactions.create_sync("ask", {"workflow_id": pid, "question": "Q",
                                               "options": ["Yes please", "No thanks"]})
        got = {}
        t = threading.Thread(target=lambda: got.update(
            a=interactions.wait_sync(ask, timeout=5)), daemon=True)
        t.start()
        time.sleep(0.05)
        self.assertEqual(reply.deliver(pid, reply.entry_from(
            {"kind": "typed", "text": "No thanks"})), {"landed": "resolved"})
        t.join(5)
        self.assertEqual(got["a"]["via"], "click")

    def test_live_and_parked_settles_write_the_same_item(self):
        p1 = _workflow([], pid="p_settle_live")
        req1 = transcript.append_request(p1, "approval", {"title": "Send it"}, iid="apr_1")
        live = reply.settle(p1, req1, {"text": "allow", "via": "click", "cid": "a"})
        p2 = _workflow([], pid="p_settle_rest")
        transcript.append_request(p2, "approval", {"title": "Send it"}, iid="apr_2")
        rest, items = reply.land(p2, reply.entry_from(
            {"kind": "click", "iid": "apr_2", "text": "allow", "cid": "b"}))
        self.assertEqual(live["drive"], rest)
        self.assertEqual(items[0]["shown"], "allow")
        self.assertIn('Yes to "Send it"', rest)

        p3 = _workflow([], pid="p_settle_order")
        transcript.append_request(p3, "ask", {"question": "Q", "options": ["A"]}, iid="ask_o")
        _, items = reply.land(p3, reply.entry_from({"kind": "typed", "text": "something else", "cid": "c"}))
        self.assertEqual([i["kind"] for i in items], ["message", "answer"])
        self.assertEqual(items[1]["cid"], "c")

    def test_a_message_typed_during_a_pause_lands_at_once(self):
        import turns
        pid = "p_settle_land"
        p = _workflow([], pid=pid)
        self.assertTrue(turns.begin(pid, "chat"))
        try:
            apr = interactions.create_sync("approval", {"workflow_id": pid, "title": "T"})
            got = {}
            t = threading.Thread(target=lambda: got.update(
                a=interactions.wait_sync(apr, timeout=3,
                                         on_tick=lambda: reply.land_parked(p))), daemon=True)
            t.start()
            time.sleep(0.05)
            self.assertEqual(reply.deliver(pid, reply.entry_from(
                {"kind": "typed", "text": "wait, not that one", "cid": "m1"})), {"landed": "parked"})
            deadline = time.time() + 3
            while time.time() < deadline and not [i for i in transcript.items(p)
                                                    if i["kind"] == "message" and i.get("from") == "user"]:
                time.sleep(0.1)
            msgs = [i for i in transcript.items(p) if i["kind"] == "message" and i.get("from") == "user"]
            self.assertEqual([m["text"] for m in msgs], ["wait, not that one"])
            self.assertEqual(msgs[0]["cid"], "m1")
            interactions.park_workflow(pid)
            t.join(3)
        finally:
            turns.finish(pid)

    def test_a_run_never_parks_words(self):
        import turns
        pid = "p_settle_run"
        _workflow([], pid=pid)
        self.assertTrue(turns.begin(pid, "run"))
        try:
            self.assertIsNone(reply.deliver(pid, reply.entry_from({"kind": "typed", "text": "x"})))
            self.assertFalse(turns.interject(pid, {"kind": "typed", "text": "x"}))
            self.assertEqual(turns.running_kind(pid), "run")
        finally:
            turns.finish(pid)

    def test_the_issue_rides_the_entry_not_the_words(self):
        from agent import turnstate
        p = _workflow([], pid="p_settle_issue")
        p["tickets"] = [{"id": "tkt_abc12345", "status": "open", "node_id": "n"}]
        drive, items = reply.land(p, reply.entry_from(
            {"kind": "typed", "text": "Please fix the problem with \"Send\".",
             "issue": "tkt_abc12345", "cid": "i1"}))
        self.assertNotIn("tkt_", drive)
        self.assertEqual(turnstate.of(p).fix_issue_id, "tkt_abc12345")
        self.assertEqual(p["tickets"][0]["status"], "in-progress")
