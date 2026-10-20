# Pins that every rule refusal a tool can give is said beforehand in the tool's description or the skill of the moment
from __future__ import annotations

import glob
import unittest

from tests import _bootstrap

from agent import actions
from agent import skills as _skills

TOLD = [
    ("run_cell", "Save the step in the plan, with typed inputs and outputs, before you try it"),
    ("run_cell", "all reported in one answer"),
    ("run_cell", "so every name its code reads is one of them"),
    ("run_cell", "no try may read the output of another try in the same call"),
    ("run_cell", "A name passed to get_secret must be a stored secret"),
    ("run_cell", "never call your own tools or ai_call there"),
    ("run_cell", "A browser step of the plan gives its url in the plan"),
    ("run_cell", "not a path into the app's storage"),
    ("run_cell", "'$recorded:name'"),
    ("run_cell", "Never run a step's code again under a new name"),
    ("run_cell", "a try that only looks at something gets its own name"),
    ("run_cell", "Changing a step of a built workflow needs the user's approval of its declaration first"),
    ("run_cell", "it is required for a try that opens a window"),
    ("run_cell", "A recorded read is fetched again only when data you need is missing from it"),
    ("run_cell", "Do not try again something the user said no to"),
    ("run_cell", "Leave code out to run the step's own code again with new inputs"),
    ("run_cell", "adds it to the change, with the try's reason"),
    ("run_ai_step", "name takes the step's name or its id"),
    ("04-working-a-step", "a try whose loop has none is refused with the rest of the code check"),
    ("07-what-a-plan-contains", "Decide where a file comes from by what it is for"),
    ("run_ai_step", "held to the declared outputs by the same check a run uses"),
    ("run_ai_step", "leave model blank for the configured default"),
    ("ask_user", "A question card always gives the user a way to answer"),
    ("ask_user", "A value already stored is returned instead of being asked again"),
    ("ask_user", "ask the user to attach an environment"),
    ("declare_variables", "A value from an attached environment is read by its name, never copied into the workflow"),
    ("declare_variables", "The value is stored in the type of the input that reads it"),
    ("save_plan", "fix_note={went_wrong,will_change}"),
    ("save_plan", "change_note={found,will_change}"),
    ("build_workflow", "Every step needs an accepted try"),
    ("build_workflow", "It needs every input and output typed, every design gap and finding fixed"),
    ("build_workflow", "the user's click on the opening, change or fix card"),
    ("run_node", "Never connectors"),
    ("diagnose_case", "supported class/conditions"),
    ("resolve_issue", "One fix at a time"),
    ("close_issue", "one plain sentence the user reads"),
    ("import_recording", "holds no calls or actions is refused"),
    ("share_file", "the blob reference returned by write_file"),
    ("bind_variable", "ask_user first"),
    ("save_intent", "before save_plan"),
    ("04-working-a-step", "A try is judged exactly as a run is"),
    ("07-what-a-plan-contains", "typed inputs/outputs"),
    ("08-when-a-run-failed", "Before you change or try any step the fix affects, save the fix's declaration and wait for the user's click on its card"),
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
