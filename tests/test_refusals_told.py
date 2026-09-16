# Pins that every rule refusal a tool can give is said beforehand in the tool's description or the skill of the moment
from __future__ import annotations

import glob
import unittest

from tests import _bootstrap

from agent import actions
from agent import skills as _skills

TOLD = [
    ("run_cell", "has its type before it is tried"),
    ("run_cell", "all reported in one answer"),
    ("run_cell", "never from another cell of the same call"),
    ("run_cell", "a get_secret name must be a secret"),
    ("run_cell", "never a workflow tool and never an import"),
    ("run_cell", "Never ai_call here"),
    ("run_cell", "A window belongs to a BROWSER step"),
    ("run_cell", "never a filesystem path"),
    ("run_cell", "$recorded:<name>"),
    ("run_cell", "Never re-run a step's code under a new name"),
    ("run_cell", "a diagnostic takes its own plain name"),
    ("run_cell", "waits for the change card"),
    ("run_cell", "say what each window is for in `reason`"),
    ("run_cell", "REFUSED to the recording"),
    ("run_cell", "A step the user DECLINED to try live is done"),
    ("run_ai_step", "same enforced-shape path a run uses"),
    ("run_ai_step", "model from Admin"),
    ("ask_user", "A question card always gives the user a way to answer"),
    ("ask_user", "already stored is refused"),
    ("ask_user", "tell the user to attach that environment"),
    ("declare_variables", "never copied out of an environment"),
    ("declare_variables", "stored in the type of the input that reads it"),
    ("save_plan", "fix_note is REQUIRED"),
    ("save_plan", "needs change_note"),
    ("build_workflow", "REFUSES while any step hasn't been tested"),
    ("build_workflow", "open design gaps or a port with no type"),
    ("build_workflow", "until the user has agreed on the card"),
    ("run_node", "Never connectors"),
    ("diagnose_case", "its CLASS"),
    ("resolve_issue", "One fix at a time"),
    ("close_issue", "one plain sentence the user reads"),
    ("import_recording", "hold calls or actions"),
    ("share_file", "the blob: reference a cell's write_file returned"),
    ("bind_variable", "ask_user first"),
    ("save_intent", "Required before save_plan"),
    ("04-working-a-step", "A try is judged exactly as a run is"),
    ("07-what-a-plan-contains", "before the step is tried or built"),
    ("08-when-a-run-failed", "agrees to it before anything is built"),
    ("01-how-you-write", "Never use the product's own machinery words"),
]

class RefusalsToldTest(unittest.TestCase):
    def test_every_rule_refusal_is_said_beforehand(self):
        descs = {s["name"]: s.get("description", "") for s in actions.schemas()}
        skills = {}
        for path in glob.glob(str(_skills.STATIC_DIR) + "/**/SKILL.md", recursive=True):
            sid = path.split("/")[-2]
            skills[sid] = open(path, encoding="utf-8").read()
        missing = []
        for where, phrase in TOLD:
            text = descs.get(where) or skills.get(where) or ""
            self.assertTrue(text, f"no tool or skill called {where!r}")
            if phrase not in text:
                missing.append(f"{where}: {phrase!r}")
        self.assertEqual(missing, [], "rule refusals the model is not told about beforehand")

if __name__ == "__main__":
    unittest.main()
