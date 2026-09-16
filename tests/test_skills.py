# Tests: skills loader seams: static index/read, traversal guards, learned skills round-trip and the secret-value scrub
from __future__ import annotations

import unittest

from tests import _bootstrap

from agent import skills

class SkillsTest(unittest.TestCase):
    def test_static_index_and_read(self):
        idx = skills.static_index()
        ids = [e["id"] for e in idx]
        self.assertIn("00-what-cryogram-is", ids)
        s = skills.read_static("04-working-a-step")
        flat = " ".join(s["content"].lower().split())
        self.assertIn("never ask the user beforehand whether you may test it", flat)

    def test_traversal_guarded(self):
        self.assertIsNone(skills.read_static("../../config"))

    def test_reference_files_load_by_skill_slash_ref(self):
        got = skills.read_static("browser/har-capture")
        self.assertIsNotNone(got)
        self.assertIn("HAR", got["content"])
        self.assertEqual(got["id"], "browser/har-capture")

        body = skills.read_static("browser")["content"]
        self.assertIn("browser/har-capture", body)
        self.assertIn("browser/page-state", body)
        from agent import actions
        r = actions._load_skill({"id": "p_ref"}, "browser/page-state")
        self.assertIn("confirm_with_user", r["content"])

    def test_reference_files_are_traversal_and_name_guarded(self):
        self.assertIsNone(
            skills.read_static("browser/../browser-login"))
        self.assertIsNone(
            skills.read_static("browser/SKILL"))
        self.assertIsNone(
            skills.read_static("browser/nope"))

    def test_action_index_offers_action_skills_and_learnings(self):
        from agent import actions
        from agent import prompt as prompt_mod
        from storage import learnings
        learnings.save("Dates arrive day-first from this CRM family",
                   "When reading dates from a CRM export: DD.MM.YYYY.")
        idx = prompt_mod._action_index({"id": "p_sync"})
        self.assertIn("25-recordings", idx)
        self.assertIn("learning-dates-arrive-day-first-from-this-crm-family", idx)

        self.assertNotIn("00-what-cryogram-is", idx)
        self.assertNotIn("02-a-message-arrives", idx)
        self.assertNotIn("10-freeze", idx)

        got = actions._load_skill(
            {"id": "p_sync"}, "learning-dates-arrive-day-first-from-this-crm-family")
        self.assertIn("DD.MM.YYYY", got["content"])

class PolicyActionSplitTest(unittest.TestCase):
    def test_every_master_skill_declares_a_valid_kind(self):
        seen = 0
        for e in skills.static_index():
            self.assertIn(e["kind"], ("policy", "action"),
                          f"{e['group']}/{e['id']} kind={e['kind']!r}")
            seen += 1
        self.assertGreater(seen, 10)

    def test_policy_text_carries_policy_bodies_not_action_bodies(self):
        po = skills.policy_text()

        say = skills.read_static("00-what-cryogram-is")["content"]
        self.assertIn(say.strip()[:80], po)

        loop = skills.read_static("02-a-message-arrives")["content"]
        self.assertIn(loop.strip()[:80], po)

        plan = skills.read_static("25-recordings")["content"]
        self.assertNotIn(plan.strip()[:80], po)

class BatchingRuleTest(unittest.TestCase):
    def test_exploration_batches_reads_never_writes(self):
        body = skills.read_static("04-working-a-step")["content"]
        low = body.lower()
        self.assertIn("one cell per question", low)
        self.assertIn("a write always stands alone", low)

class ConnectorGuidesTest(unittest.TestCase):
    def test_guides_are_offered_as_loadable_index_lines(self):
        ids = [e["id"] for e in skills.static_index()]
        self.assertIn("google-sheets", ids)
        from agent import prompt as prompt_mod
        self.assertIn("google-sheets", prompt_mod._action_index({"id": "p_conn"}))

    def test_a_guide_reads_and_is_never_always_on(self):
        s = skills.read_static("google-sheets")
        self.assertTrue(s["content"].strip())

        self.assertEqual(s["meta"]["kind"], "action")
        self.assertNotIn(s["content"].strip()[:80], skills.policy_text())

    def test_exploration_policy_points_at_guides_and_sources(self):
        po = skills.policy_text()
        self.assertIn("connector guide for an outside tool before searching", po)
        self.assertIn("the announcement names it and says why", po)
        self.assertIn("goes in a fenced code block of its own", po)
        self.assertIn("ask it with options", po)

class DeferralAndFreeTextPolicyTest(unittest.TestCase):
    def test_policy_carries_the_three_facts(self):
        po = skills.policy_text()
        self.assertIn("is the same claim in softer words", po)
        self.assertIn("Options are shortcuts, not the whole menu", po)
        self.assertIn("never the disk", po)

class PracticeSkillsTest(unittest.TestCase):
    def test_three_practices_discoverable_as_actions(self):
        ids = {e["id"]: e for e in skills.static_index()}
        for sid in ("61-connector-practice", "62-ai-practice", "63-code-practice"):
            self.assertIn(sid, ids)
            self.assertEqual(ids[sid]["kind"], "action")
        po = skills.policy_text()
        body = skills.read_static("62-ai-practice")["content"]
        self.assertNotIn(body.strip()[:80], po)

    def test_practices_answer_the_same_three_questions(self):
        for sid in ("61-connector-practice", "62-ai-practice", "63-code-practice"):
            low = skills.read_static(sid)["content"].lower()
            self.assertIn("## what to include", low, sid)
            self.assertIn("## how to structure it", low, sid)
            self.assertIn("## what to test", low, sid)

    def test_resilience_and_prompt_rules_pinned(self):
        conn = skills.read_static("61-connector-practice")["content"]

        self.assertIn("resilient-request", conn)
        self.assertIn("The approval card is the ask", conn)
        self.assertIn("says what happened per item", conn)
        ref = skills.read_static("61-connector-practice/resilient-request")["content"]
        self.assertIn("timeout", ref)
        self.assertIn("4xx", ref)
        ai = skills.read_static("62-ai-practice")["content"]
        self.assertIn("never describes the output format", ai)
        self.assertIn("a description saying exactly what goes in it", ai)

    def test_practice_locks_in_a_route(self):
        practice = skills.read_static("61-connector-practice")["content"]
        self.assertIn("locks in a route", practice)
        self.assertIn("no access to the user's computer", practice)

    def test_connector_guides_expanded(self):
        ids = [e["id"] for e in skills.static_index()]
        for g in ("gmail", "outlook-email", "microsoft-teams", "slack",
                  "webhook", "onedrive", "excel-onedrive"):
            self.assertIn(g, ids)
