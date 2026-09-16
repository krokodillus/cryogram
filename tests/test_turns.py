# Tests: the in-flight turn registry: one turn per workflow, text accumulation vs discrete-event buffering, the poll cursor, caps, the failure-path partial text, and the per-workflow pending-interactions filter
from __future__ import annotations

import threading
import unittest

from tests import _bootstrap

from agent import interactions
import turns

class TurnRegistryTest(unittest.TestCase):
    def test_one_turn_per_workflow(self):
        self.assertTrue(turns.begin("p_t1", "chat"))
        self.assertFalse(turns.begin("p_t1", "codify"))
        self.assertTrue(turns.begin("p_t2", "chat"))
        turns.finish("p_t1")
        self.assertTrue(turns.begin("p_t1", "codify"))
        turns.finish("p_t1")
        turns.finish("p_t2")

    def test_begin_race_yields_exactly_one_winner(self):
        results = []
        barrier = threading.Barrier(8)
        def claim():
            barrier.wait()
            results.append(turns.begin("p_race", "chat"))
        threads = [threading.Thread(target=claim) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sum(results), 1)
        turns.finish("p_race")

    def test_text_accumulates_events_buffer_with_seq(self):
        turns.begin("p_t3", "chat")
        turns.record("p_t3", {"type": "delta", "text": "Hello "})
        turns.record("p_t3", {"type": "tool", "text": "reading files"})
        turns.record("p_t3", {"type": "delta", "text": "world"})
        snap = turns.snapshot("p_t3")
        self.assertTrue(snap["active"])
        self.assertEqual(snap["text"], "Hello world")
        self.assertEqual([e["type"] for e in snap["events"]], ["tool"])

        cursor = snap["last_seq"]
        self.assertEqual(turns.snapshot("p_t3", after=cursor)["events"], [])
        turns.record("p_t3", {"type": "ask", "id": "int_x", "question": "?"})
        newer = turns.snapshot("p_t3", after=cursor)["events"]
        self.assertEqual([e["type"] for e in newer], ["ask"])
        self.assertEqual(turns.partial_text("p_t3"), "Hello world")
        turns.finish("p_t3", error="boom")
        end = turns.snapshot("p_t3")
        self.assertFalse(end["active"])
        self.assertEqual(end["error"], "boom")

    def test_done_event_never_buffers_workflow_payload(self):
        turns.begin("p_t4", "codify")
        turns.record("p_t4", {"type": "done", "workflow": {"huge": "x" * 1000},
                              "error": "E"})
        ev = turns.snapshot("p_t4")["events"][-1]
        self.assertEqual(ev["type"], "done")
        self.assertNotIn("workflow", ev)
        self.assertEqual(ev["error"], "E")
        turns.finish("p_t4")

    def test_event_cap_marks_truncated(self):
        turns.begin("p_t5", "chat")
        for i in range(turns._EVENT_CAP + 50):
            turns.record("p_t5", {"type": "tool", "text": f"t{i}"})
        snap = turns.snapshot("p_t5")
        self.assertTrue(snap["truncated"])
        self.assertEqual(len(snap["events"]), turns._EVENT_CAP)
        turns.finish("p_t5")

    def test_done_event_keeps_run_result(self):
        turns.begin("p_rr", "run")
        turns.record("p_rr", {"type": "done", "workflow": {"id": "p_rr"},
                              "result": {"status": "halted",
                                         "reason": "missing-value"}})
        done = [e for e in turns.snapshot("p_rr")["events"]
                if e["type"] == "done"][0]
        self.assertEqual(done["result"]["reason"], "missing-value")
        self.assertNotIn("workflow", done)
        turns.finish("p_rr")

    def test_done_event_keeps_build_and_fix_keys(self):
        turns.begin("p_bk", "chat")
        turns.record("p_bk", {"type": "done", "workflow": {"id": "p_bk"},
                              "build": {"include_proposals": None}})
        snap = turns.snapshot("p_bk")
        done = [e for e in snap["events"] if e["type"] == "done"][0]
        self.assertEqual(done.get("build"), {"include_proposals": None})
        self.assertNotIn("workflow", done)
        turns.finish("p_bk")

    def test_record_after_finish_is_ignored(self):
        turns.begin("p_t6", "chat")
        turns.finish("p_t6")
        turns.record("p_t6", {"type": "delta", "text": "late"})
        self.assertEqual(turns.snapshot("p_t6")["text"], "")

    def test_a_run_and_a_turn_share_the_one_slot(self):
        self.assertTrue(turns.begin("p_t7", "run"))
        self.assertFalse(turns.begin("p_t7", "chat"))
        turns.finish("p_t7")
        self.assertTrue(turns.begin("p_t7", "chat"))
        self.assertFalse(turns.begin("p_t7", "run"))
        turns.finish("p_t7")

    def test_request_stop_and_should_stop(self):
        self.assertFalse(turns.request_stop("p_t8"))
        self.assertFalse(turns.should_stop("p_t8"))
        turns.begin("p_t8", "run")
        self.assertFalse(turns.should_stop("p_t8"))
        self.assertTrue(turns.request_stop("p_t8"))
        self.assertTrue(turns.should_stop("p_t8"))
        turns.finish("p_t8")

        turns.begin("p_t8", "run")
        self.assertFalse(turns.should_stop("p_t8"))
        turns.finish("p_t8")

    def test_request_stop_after_finish_is_a_noop(self):
        turns.begin("p_t9", "run")
        turns.finish("p_t9")
        self.assertFalse(turns.request_stop("p_t9"))
        self.assertFalse(turns.should_stop("p_t9"))

    def test_stop_targets_a_turn_id_never_the_slot(self):
        turns.begin("p_t10", "chat")
        old_id = turns.current_turn_id("p_t10")
        self.assertTrue(old_id)
        turns.finish("p_t10")
        turns.begin("p_t10", "chat")
        new_id = turns.current_turn_id("p_t10")
        self.assertNotEqual(old_id, new_id)

        self.assertFalse(turns.request_stop("p_t10", old_id))
        self.assertFalse(turns.should_stop("p_t10"))

        self.assertTrue(turns.request_stop("p_t10", new_id))
        self.assertTrue(turns.should_stop("p_t10"))
        turns.finish("p_t10")
        turns.begin("p_t10", "chat")
        self.assertTrue(turns.request_stop("p_t10"))
        turns.finish("p_t10")

    def test_current_turn_id_empty_when_idle(self):
        self.assertEqual(turns.current_turn_id("p_t11"), "")
        turns.begin("p_t11", "chat")
        self.assertTrue(turns.current_turn_id("p_t11"))
        turns.finish("p_t11")
        self.assertEqual(turns.current_turn_id("p_t11"), "")

class PendingFilterTest(unittest.TestCase):
    def test_pending_filters_by_workflow(self):
        a = interactions.create_sync("ask", {"workflow_id": "p_A",
                                             "question": "?"})
        b = interactions.create_sync("approval", {"workflow_id": "p_B",
                                                  "tool": "x"})
        self.assertEqual([p["id"] for p in interactions.pending("p_A")], [a])
        self.assertEqual([p["id"] for p in interactions.pending("p_B")], [b])
        self.assertEqual(len(interactions.pending()), 2)
        interactions.resolve(a, "x")
        interactions.resolve(b, "allow")

        self.assertEqual(interactions.wait_sync(a, timeout=1), "x")
        self.assertEqual(interactions.wait_sync(b, timeout=1), "allow")
        self.assertEqual(interactions.pending(), [])

class NoReorderTest(unittest.TestCase):
    def test_card_last_stays_retired(self):
        import server
        self.assertFalse(hasattr(server, "_card_last"))

class FlushTextTest(unittest.TestCase):
    def test_flush_hands_over_tail_and_resets(self):
        turns.begin("p_flush", "chat")
        turns.record("p_flush", {"type": "delta", "text": "before the card"})
        self.assertEqual(turns.flush_text("p_flush"), "before the card")
        self.assertEqual(turns.partial_text("p_flush"), "")
        self.assertEqual(turns.snapshot("p_flush")["text"], "")
        turns.record("p_flush", {"type": "delta", "text": "the tail"})
        self.assertEqual(turns.snapshot("p_flush")["text"], "the tail")
        self.assertEqual(turns.flush_text("p_flush"), "the tail")

        self.assertEqual(turns.flush_text("p_flush"), "")
        turns.finish("p_flush")

    def test_unknown_workflow_flushes_empty(self):
        self.assertEqual(turns.flush_text("p_nope"), "")

class WaitSyncAnswerTest(unittest.TestCase):
    def test_answer_before_the_deadline_is_returned(self):
        iid = interactions.create_sync("approval", {"workflow_id": "p_ws1"})
        t = threading.Timer(0.05, lambda: interactions.resolve(iid, "allow"))
        t.start()
        self.assertEqual(interactions.wait_sync(iid, timeout=2), "allow")
        t.join()

    def test_no_answer_times_out_to_none(self):
        iid = interactions.create_sync("approval", {"workflow_id": "p_ws2"})
        self.assertIsNone(interactions.wait_sync(iid, timeout=0.05))

    def test_late_resolve_reports_expired_never_silently_dropped(self):
        iid = interactions.create_sync("approval", {"workflow_id": "p_ws3"})
        self.assertIsNone(interactions.wait_sync(iid, timeout=0.05))
        self.assertFalse(interactions.resolve(iid, "allow"))

    def test_answer_racing_the_deadline_is_never_lost(self):
        for _ in range(25):
            iid = interactions.create_sync("approval", {"workflow_id": "p_ws4"})
            got: list = []
            t = threading.Timer(0.05, lambda i=iid:
                                got.append(interactions.resolve(i, "allow")))
            t.start()
            answer = interactions.wait_sync(iid, timeout=0.05)
            t.join()
            if got and got[0]:
                self.assertEqual(answer, "allow")
            else:
                self.assertIsNone(answer)

if __name__ == "__main__":
    unittest.main()
