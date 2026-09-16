# Tests: the save_plan check list - the order the checks apply in, and that the first refusal ends the save
from __future__ import annotations

import unittest
from unittest import mock

from tests import _bootstrap

from agent import plan_save

ORDER = [
    "intake", "amend", "harness_keys", "identity", "continuity", "evidence", "change_note", "scope",
    "note_judgement", "note_untested", "note_instructions_early",
    "note_identity_checks", "warn_ai_model", "unread_values", "warn_ai_tuning",
    "note_must_have", "note_unexpected_input", "note_sender_outcomes",
    "note_read_only", "warn_batch_policy", "warn_pace", "secret_inputs", "warn_unset_secrets",
    "note_domains", "note_ai_descriptions", "warn_widened",
    "retarget_deliverables", "logic_and_settings", "note_near_names", "persist_and_card",
]

class PlanSaveOrderTest(unittest.TestCase):
    def test_the_phases_apply_in_this_order(self):
        self.assertEqual([f.__name__ for f in plan_save.PHASES], ORDER)

    def test_every_phase_is_a_function_of_this_module(self):
        for f in plan_save.PHASES:
            self.assertEqual(f.__module__, plan_save.__name__, f.__name__)

    def test_the_first_refusal_ends_the_save(self):
        seen = []

        def passes(s):
            seen.append("passes")

        def refuses(s):
            seen.append("refuses")
            return {"ok": False, "error": "no"}

        def never(s):
            seen.append("never")

        with mock.patch.object(plan_save, "PHASES", [passes, refuses, never]):
            out = plan_save.save({"id": "p_order", "nodes": []}, {"nodes": []})
        self.assertEqual(out, {"ok": False, "error": "no"})
        self.assertEqual(seen, ["passes", "refuses"])
