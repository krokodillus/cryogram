# Tests for the system prompt: it is the always-on policy core plus the index of skills the model can load on demand, and no on-demand body is baked in
from __future__ import annotations

import unittest

from tests import _bootstrap

from agent import prompt as prompt_mod
from agent import skills

POLICY_FILES = ("00-what-cryogram-is", "01-how-you-write", "02-a-message-arrives",
                "03-starting-the-work", "04-working-a-step", "05-what-you-learn",
                "06-showing-and-building", "07-what-a-plan-contains",
                "08-when-a-run-failed")

class PromptAssemblyTest(unittest.TestCase):
    def test_the_prompt_is_every_policy_body_plus_the_skill_index(self):
        ap = prompt_mod.system({"id": "p_smoke"})
        self.assertIn("load_skill", ap)
        for sid in POLICY_FILES:
            body = skills.read_static(sid)["content"]
            self.assertEqual(ap.count(body.strip()), 1, sid)
        action_body = skills.read_static("25-recordings")["content"]
        self.assertNotIn(action_body.strip()[:80], ap)

    def test_the_tail_states_only_how_a_turn_ends(self):
        tail = prompt_mod._TERMINAL_TAIL
        self.assertIn("A plain reply with no tool call ends it", tail)
        self.assertIn("end the turn quietly", tail)
        self.assertLess(len(tail), 900)
        self.assertNotIn("BATCH", tail)
        self.assertNotIn("NEVER", tail)

    def test_the_policy_files_are_the_whole_always_on_set(self):
        names = {m["name"] for m in
                 (skills.parse_frontmatter(p.read_text())[0]
                  for d in skills.STATIC_DIR.iterdir() if d.is_dir()
                  for p in skills._skill_paths(d))
                 if m.get("kind") == "policy"}
        self.assertEqual(names, set(POLICY_FILES))

if __name__ == "__main__":
    unittest.main()
