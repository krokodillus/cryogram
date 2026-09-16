# Tests: lEARNINGS - what the assistant found out about how things work (replacing per-workflow "learned skills")
from __future__ import annotations

import unittest

from tests import _bootstrap

import config
from storage import learnings, secrets_store

def _clear():
    for p in learnings.learnings_dir().glob("*.md"):
        p.unlink()

class LearningsStoreTest(unittest.TestCase):
    def setUp(self):
        _clear()

    def test_roundtrip_title_becomes_the_trigger_id(self):
        r = learnings.save("LinkedIn sessions expire when the window closes",
                       "When a site's session cookie has no expiry, capture the session while the window is still open.")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["id"],
                         "linkedin-sessions-expire-when-the-window-closes")
        got = learnings.read(r["id"])
        self.assertIn("capture", got["content"])
        self.assertEqual(got["title"],
                         "LinkedIn sessions expire when the window closes")

    def test_same_title_overwrites_never_accumulates(self):
        learnings.save("Cursor pagination on this API", "first version")
        learnings.save("Cursor pagination on this API", "second version")
        idx = learnings.index()
        self.assertEqual(len(idx), 1)
        self.assertIn("second version", learnings.read(idx[0]["id"])["content"])

    def test_learnings_are_global_no_workflow_scoping(self):
        learnings.save("Sheets writes need the token endpoint first", "body")
        self.assertEqual(len(learnings.index()), 1)
        self.assertTrue(str(learnings.learnings_dir()).startswith(str(config.DATA_DIR)))
        self.assertNotIn("workflows", str(learnings.learnings_dir()))

    def test_blank_title_or_content_refused(self):
        self.assertIn("error", learnings.save("", "body"))
        self.assertIn("error", learnings.save("A title", "   "))

class LearningsAnonymityTest(unittest.TestCase):
    def setUp(self):
        _clear()
        self.workflow = {
            "id": "proj_abc123", "name": "CV Management Tool",
            "variables": [
                {"name": "sheet_url",
                 "value": "https://docs.google.com/spreadsheets/d/1Ab9"},
                {"name": "max_people", "value": "100"},
                {"name": "api_key", "value": True, "secret": True},
            ]}

    def test_refuses_the_workflow_name_and_id(self):
        r = learnings.save("Sheets quirk", "While building CV Management Tool I found the token endpoint is needed.",
                       self.workflow)
        self.assertIn("error", r)
        self.assertIn("name or id", r["error"])
        self.assertIn("error", learnings.save("Quirk", "see proj_abc123",
                                          self.workflow))

    def test_refuses_a_stored_value_and_never_quotes_it(self):
        secret_ish = "https://docs.google.com/spreadsheets/d/1Ab9"
        r = learnings.save("Sheets quirk", f"Write to {secret_ish} last.",
                       self.workflow)
        self.assertIn("error", r)
        self.assertNotIn(secret_ish, r["error"])
        self.assertIn("settings", r["error"])

    def test_refuses_a_credential_value(self):
        secrets_store.set_secret("NOTES_KEY", "sk-live-abcdefgh", secrets_store.OWNER_APP)
        r = learnings.save("Auth quirk", "the key sk-live-abcdefgh works",
                       self.workflow)
        self.assertIn("error", r)
        self.assertNotIn("sk-live-abcdefgh", r["error"])

    def test_short_values_and_public_endpoints_are_allowed(self):
        r = learnings.save("Sheets needs two hosts",
                       "When writing to Google Sheets: get a token from oauth2.googleapis.com first, then sheets.googleapis.com. Caps like 100 rows are per-request limits.",
                       self.workflow)
        self.assertTrue(r.get("ok"), r)

class LearningsIndexTest(unittest.TestCase):
    def setUp(self):
        _clear()

    def test_index_is_capped_and_says_how_many_were_left_out(self):
        for i in range(learnings.INDEX_CAP + 3):
            learnings.save(f"Situation number {i}", f"body {i}")
        shown, dropped = learnings.index_for(None)
        self.assertEqual(len(shown), learnings.INDEX_CAP)
        self.assertEqual(dropped, 3)

    def test_prompt_index_lists_learnings_and_marks_the_remainder(self):
        from agent import prompt as prompt_mod
        for i in range(learnings.INDEX_CAP + 2):
            learnings.save(f"Situation number {i}", f"body {i}")
        idx = prompt_mod._action_index({"id": "p_notes"})
        self.assertIn("learning-situation-number-", idx)
        self.assertIn("and 2 older learnings not listed", idx)

class JustInTimeSelectionTest(unittest.TestCase):
    def setUp(self):
        _clear()
        learnings.save(
            "Google Sheets writes need a token from the oauth endpoint first",
            "When writing to a Google Sheet: POST oauth2.googleapis.com for a token, then sheets.googleapis.com. Two hosts, one flow.")
        learnings.save(
            "LinkedIn sessions end when the browser window closes",
            "When a site's session cookie carries no expiry, capture the session while the window is still open and reinject it.")
        learnings.save(
            "PDF text extraction loses column order in two-column layouts",
            "When parsing a PDF laid out in columns, read blocks by position.")

    def _sheets_step(self):
        return {"name": "Save results to Google Sheet", "type": "connector",
                "domains": ["sheets.googleapis.com"]}

    def test_the_matching_learning_is_inlined_and_others_are_not(self):
        inline, listed = learnings.select(
            "Save results to Google Sheet connector sheets.googleapis.com")
        self.assertTrue(inline)
        self.assertIn("Sheets", inline[0]["title"])
        titles = " ".join(e["title"] for e in inline)
        self.assertNotIn("PDF text extraction", titles)
        self.assertTrue(any("PDF" in e["title"] for e in listed))

    def test_a_failure_message_finds_the_learning_a_plan_never_would(self):
        inline, _ = learnings.select(
            "the browser window closed and the linkedin session was lost")
        self.assertTrue(inline)
        self.assertIn("LinkedIn", inline[0]["title"])

    def test_unrelated_work_inlines_nothing(self):
        inline, listed = learnings.select(
            "add two numbers together and round the result")
        self.assertEqual(inline, [])
        self.assertEqual(len(listed), 3)

    def test_the_inline_block_respects_the_character_budget(self):
        _clear()
        for i in range(6):
            learnings.save(f"Sheets quirk number {i}",
                           "sheets sheet google " + ("x" * 900))
        inline, listed = learnings.select("google sheets", budget=2000)
        self.assertTrue(inline)
        self.assertLessEqual(sum(len(e["content"]) for e in inline), 2000)
        self.assertTrue(listed)

    def test_context_inlines_for_the_next_unworked_step(self):
        from agent import context
        p = {"id": "p_jit", "name": "Some workflow", "chat": [], "nodes": [],
             "variables": [], "intent": {"summary": "Collect and file data."},
             "plan": {"nodes": [self._sheets_step()]}}
        block = context._learnings_context(p, "carry on")
        self.assertIn("what earlier builds found out", block)
        self.assertIn("oauth2.googleapis.com", block)
        self.assertIn("Save results to Google Sheet", block)
        self.assertNotIn("PDF text extraction", block)

    def test_contributed_learnings_get_a_boost_not_a_bypass(self):
        p = {"id": "p_boost",
             "learning_ids": ["pdf-text-extraction-loses-column-order-in-two-column-layouts"]}

        inline, _ = learnings.select("google sheets oauth token", p)
        self.assertIn("Sheets", inline[0]["title"])

        inline2, _ = learnings.select("pdf column layout", p)
        self.assertIn("PDF", inline2[0]["title"])

class SaveLearningToolTest(unittest.TestCase):
    def setUp(self):
        _clear()

    def test_tool_queues_and_the_green_build_commits(self):
        from agent import node_tools, orchestrator
        p = {"id": "proj_xyz", "name": "Invoice sorter", "variables": []}
        r = node_tools.tool_save_learning(
            p, "Bot checks appear after a few plain fetches",
            "When a site returns 403 to urllib but loads in a browser, it is checking the client, not the credentials.")
        self.assertTrue(r.get("ok"), r)
        self.assertIn("green", r.get("note", ""))
        self.assertEqual(len(learnings.index()), 0)
        self.assertEqual(len(p["pending_learnings"]), 1)
        blocked = node_tools.tool_save_learning(
            p, "Invoice sorter findings", "body")
        self.assertIn("error", blocked)
        self.assertEqual(orchestrator._commit_pending_learnings(p), 1)
        self.assertEqual(len(learnings.index()), 1)
        self.assertEqual(p["pending_learnings"], [])
        self.assertEqual(p["learning_ids"],
                         ["bot-checks-appear-after-a-few-plain-fetches"])

    def test_same_title_overwrites_the_queued_entry(self):
        from agent import node_tools, orchestrator
        p = {"id": "proj_c", "name": "Whatever", "variables": [], "chat": []}
        node_tools.tool_save_learning(
            p, "Cursor pagination needs the last id, not an offset", "body")
        node_tools.tool_save_learning(
            p, "Cursor pagination needs the last id, not an offset", "body 2")
        self.assertEqual(len(p["pending_learnings"]), 1)
        self.assertIn("body 2", p["pending_learnings"][0]["content"])
        orchestrator._commit_pending_learnings(p)
        self.assertEqual(p["learning_ids"],
                         ["cursor-pagination-needs-the-last-id-not-an-offset"])

    def test_a_value_collision_at_commit_is_skipped_never_published(self):
        from agent import node_tools, orchestrator
        p = {"id": "proj_v", "name": "Whatever", "variables": [], "chat": []}
        node_tools.tool_save_learning(
            p, "Feeds page with a cursor", "use the cursor param")
        node_tools.tool_save_learning(
            p, "The magic endpoint", "call https://example.test/hook-abc123")
        p["variables"] = [{"name": "hook", "secret": False, "persistent": True,
                           "value": "https://example.test/hook-abc123"}]
        self.assertEqual(orchestrator._commit_pending_learnings(p), 1)
        titles = [e["title"] for e in learnings.index()]
        self.assertEqual(titles, ["Feeds page with a cursor"])

    def test_a_deleted_learning_leaves_the_id_dangling_harmlessly(self):
        from agent import node_tools, orchestrator
        p = {"id": "proj_d", "name": "Whatever", "variables": [], "chat": []}
        node_tools.tool_save_learning(p, "Something that gets deleted", "body")
        orchestrator._commit_pending_learnings(p)
        lid = p["learning_ids"][0]
        self.assertTrue(learnings.delete(lid))
        self.assertIsNone(learnings.read(lid))
        self.assertEqual(p["learning_ids"], [lid])
        self.assertFalse(learnings.delete(lid))

    def test_save_learning_is_on_the_agent_surface(self):
        from agent import node_tools
        names = {s[0] for s in node_tools._AGENT_SPECS}
        self.assertIn("save_learning", names)
        self.assertNotIn("save_skill", names)
        self.assertNotIn("save_note", names)

if __name__ == "__main__":
    unittest.main()
