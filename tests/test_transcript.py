# Tests: tHE TRANSCRIPT - the record of what the user was shown
from __future__ import annotations

import copy
import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from agent import node_tools, transcript

class AppendOnlyTest(unittest.TestCase):
    def test_seq_is_monotonic_and_order_is_seq_not_time(self):
        p = _workflow([], pid="p_tr1")
        a = transcript.append_message(p, "user", "first")
        b = transcript.append_request(p, "ask", {"question": "second?"})
        transcript.append_answer(p, b["iid"], "ok", shown="ok")
        c = transcript.append_message(p, "assistant", "third")
        self.assertEqual([a["seq"], b["seq"], c["seq"]], [1, 2, 4])

        c["at"] = a["at"] - 1000
        self.assertEqual([i["seq"] for i in transcript.items(p)], [1, 2, 3, 4])

    def test_an_item_is_never_rewritten_only_appended_to(self):
        p = _workflow([], pid="p_tr2")
        card = transcript.append_request(p, "ask", {"question": "Which one?",
                                                    "options": ["A", "B"]})
        frozen = copy.deepcopy(card)
        transcript.append_answer(p, card["iid"], "A", shown="A")

        self.assertEqual(transcript.last_request(p, "ask"), frozen)
        self.assertEqual(len(transcript.items(p)), 2)

    def test_replay_is_what_was_shown_in_shown_order(self):
        p = _workflow([], pid="p_tr3")
        transcript.append_message(p, "user", "do the thing")
        req = transcript.append_request(p, "ask", {"question": "Which sheet?",
                                                   "lead": "Nearly there."})
        transcript.append_answer(p, req["iid"], "the second one",
                                 shown="the second one")
        transcript.append_message(p, "assistant", "Done.")
        lines = transcript.replay_lines(p)
        self.assertEqual([l["role"] for l in lines],
                         ["user", "assistant", "user", "assistant"])
        self.assertIn("Which sheet?", lines[1]["text"])
        self.assertIn("Nearly there.", lines[1]["text"])
        self.assertEqual(lines[2]["text"], "the second one")

class AnswerPairingTest(unittest.TestCase):
    def test_open_requests_and_answered(self):
        p = _workflow([], pid="p_tr4")
        a = transcript.append_request(p, "ask", {"question": "one?"})
        b = transcript.append_request(p, "approval", {"title": "do it"})
        self.assertEqual([r["iid"] for r in transcript.open_requests(p)],
                         [a["iid"], b["iid"]])
        transcript.append_answer(p, a["iid"], "yes", shown="yes")
        self.assertEqual([r["iid"] for r in transcript.open_requests(p)],
                         [b["iid"]])
        self.assertEqual([r["iid"] for r in
                          transcript.open_requests(p, "approval")], [b["iid"]])
        self.assertIn(a["iid"], transcript.answered(p))

    def test_a_secret_answer_records_the_marker_never_the_value(self):
        p = _workflow([], pid="p_tr5")
        req = transcript.append_request(p, "ask", {"question": "Key?",
                                                   "secret": True})
        transcript.append_answer(p, req["iid"], "(provided)",
                                 shown="(provided)")
        blob = str(transcript.items(p))
        self.assertIn("(provided)", blob)
        self.assertNotIn("sk-live", blob)

class ContextReadsTheTranscriptTest(unittest.TestCase):
    def test_replay_carries_messages_requests_and_answers(self):
        from agent import context
        p = _workflow([], pid="p_tr6")
        transcript.append_message(p, "user", "build me a thing")
        req = transcript.append_request(p, "ask", {"question": "Which feed?"})
        transcript.append_answer(p, req["iid"], "the news one", shown="the news one")
        body = context.build(p, "carry on")[0]["content"]
        self.assertIn("build me a thing", body)
        self.assertIn("Which feed?", body)
        self.assertIn("the news one", body)
        self.assertIn("(question card)", body)

    def test_setup_card_fields_replay_as_the_values_given(self):
        from agent import context
        p = _workflow([], pid="p_tr7")
        req = transcript.append_request(
            p, "ask", {"question": "Details?",
                       "fields": [{"name": "sheet"}, {"name": "cap"}]})
        transcript.append_answer(p, req["iid"], "given",
                                 shown={"sheet": "https://s/x", "cap": "50"})
        body = context.build(p, "carry on")[0]["content"]
        self.assertIn("the user filled in", body)
        self.assertIn("https://s/x", body)

class BlueprintCardTest(unittest.TestCase):
    def _proposed(self, pid):
        p = _workflow([], pid=pid)
        node_tools.tool_save_intent(p, "Purpose.", instructions="Run it.")
        node_tools.tool_save_plan(p, {"summary": "Do the thing.", "nodes": [
            {"name": "Make it", "type": "code", "code_sketch": "x",
             "outputs": [{"name": "value", "type": "text"}]}]})
        node_tools.append_plan_entry(p, p["plan"])
        return p

    def test_the_card_freezes_what_it_showed(self):
        p = self._proposed("p_tr8")
        card = copy.deepcopy(transcript.last_request(p, "blueprint"))
        self.assertEqual(card["payload"]["summary"], "Do the thing.")
        self.assertEqual([s["name"] for s in card["payload"]["steps"]],
                         ["Make it"])

        p["plan"]["summary"] = "Something else entirely."
        p["plan"]["ts"] = (p["plan"].get("ts") or 0) + 100
        self.assertEqual(transcript.last_request(p, "blueprint"), card)

    def test_the_same_unanswered_question_is_not_shown_twice(self):
        p = self._proposed("p_tr9")
        self.assertFalse(node_tools.append_plan_entry(p, p["plan"]))
        self.assertEqual(len(_bootstrap.shown_requests(p, "blueprint")), 1)

    def test_a_reply_answers_the_card_and_a_revision_shows_a_fresh_one(self):
        p = self._proposed("p_tr10")
        first = copy.deepcopy(transcript.last_request(p, "blueprint"))
        node_tools.supersede_open_plan(p)
        self.assertIn(first["iid"], transcript.answered(p))

        self.assertFalse(node_tools.append_plan_entry(p, p["plan"]))
        p["plan"]["summary"] = "Do a different thing."
        self.assertTrue(node_tools.append_plan_entry(p, p["plan"]))
        cards = _bootstrap.shown_requests(p, "blueprint")
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0], first)

class BlueprintSettlesOnTheRecordTest(unittest.TestCase):
    def test_an_unstarted_build_leaves_the_card_open(self):
        p = _workflow([], pid="p_bpsettle")
        req = transcript.append_request(
            p, "blueprint", {"summary": "Do the thing."}, iid="blu_1")
        self.assertIn(req["iid"], [r["iid"] for r in transcript.open_requests(p)])
        self.assertNotIn(req["iid"], transcript.answered(p))

    def test_the_recorded_build_is_what_settles_it(self):
        p = _workflow([], pid="p_bpsettle2")
        transcript.append_request(p, "blueprint",
                                  {"summary": "Do the thing."}, iid="blu_2")
        transcript.append_answer(p, "blu_2", "Build it.", shown="build")
        self.assertIn("blu_2", transcript.answered(p))
        self.assertEqual(transcript.open_requests(p), [])

        req = [i for i in transcript.items(p) if i.get("iid") == "blu_2"
               and i["kind"] == "request"][0]
        self.assertNotIn("answer", req)
        self.assertNotIn("shown", req)

class NothingUnderAnOpenQuestionTest(unittest.TestCase):
    def test_an_assistant_message_is_refused_while_a_question_waits(self):
        p = _workflow([], pid="p_await1")
        card = transcript.append_request(p, "ask", {"question": "Which one?"})
        self.assertTrue(transcript.awaiting_user(p))
        self.assertEqual(transcript.append_message(p, "assistant", "meanwhile"),
                         {})
        self.assertEqual(len(transcript.items(p)), 1)

        transcript.append_message(p, "user", "the second one")
        transcript.append_answer(p, card["iid"], "the second one",
                                 shown={"via_message": True})
        self.assertFalse(transcript.awaiting_user(p))
        self.assertTrue(transcript.append_message(p, "assistant", "right then"))

    def test_an_approval_and_the_opening_plan_card_count_too(self):
        for kind, payload in (("approval", {"title": "Connect to x.com"}),
                              ("blueprint", {"head": "plan", "summary": "Do."})):
            p = _workflow([], pid=f"p_await_{kind}")
            transcript.append_request(p, kind, payload)
            self.assertTrue(transcript.awaiting_user(p), kind)

    def test_the_built_record_asks_nothing_so_the_done_message_lands(self):
        p = _workflow([], pid="p_await3")
        transcript.append_request(p, "blueprint",
                                  {"head": "built", "summary": "Done."})
        self.assertFalse(transcript.awaiting_user(p))
        self.assertTrue(transcript.append_message(p, "assistant",
                                                  "Here's how to run it."))

if __name__ == "__main__":
    unittest.main()
