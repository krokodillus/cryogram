import unittest
from unittest.mock import patch
from tests import _bootstrap
from agent import actions, active_skills, context, skills, trail

class ActiveSkillsTest(unittest.TestCase):
    def test_guide_survives_replay_trimming_and_restarts_without_duplicate_body(self):
        p = _bootstrap.workflow([], pid="registry_trim")
        trail.begin(p["id"], "one", "start")
        result = actions.execute(p, {"name": "load_skill", "input": {"id": "browser"}}, None)
        self.assertTrue(result["ok"])
        trail.end(p["id"])
        body = skills.read_static("browser")["content"]
        with patch.object(trail, "TRAIL_WINDOW_CHARS", 1):
            loaded = context.build(p, "next")[0]["content"]
        self.assertEqual(loaded.count(body), 1)
        self.assertIn("active skill browser", loaded)

        self.assertIn(body, active_skills.render(p["id"])[0])
        self.assertEqual(context.build(p, "again")[0]["content"].count(body), 1)

    def test_changed_removed_and_failed_skills_are_explicit(self):
        p = _bootstrap.workflow([], pid="registry_changed")
        actions._load_skill(p, "browser")
        original = skills.read_static("browser")
        with patch.object(skills, "read_static", return_value={**original, "content": "New instructions"}):
            text, _ = active_skills.render(p["id"])
            self.assertIn("CHANGED", text)
            self.assertIn("New instructions", text)
            self.assertNotIn(original["content"], text)
        with patch.object(skills, "read_static", return_value=None):
            self.assertIn("unavailable", active_skills.render(p["id"])[0])
        fresh = _bootstrap.workflow([], pid="registry_failed")
        self.assertIn("error", actions._load_skill(fresh, "does-not-exist"))
        self.assertEqual(active_skills.render(fresh["id"]), ("", set()))

    def test_reference_is_pinned_but_policy_is_not_duplicated(self):
        p = _bootstrap.workflow([], pid="registry_reference")
        actions._load_skill(p, "61-connector-practice/resilient-request")
        actions._load_skill(p, "00-what-cryogram-is")
        text, ids = active_skills.render(p["id"])
        self.assertEqual(ids, {"61-connector-practice/resilient-request"})
        self.assertIn("request_with_retry", text)

class ArgumentBoundaryTest(unittest.TestCase):
    def test_browser_is_public_but_approval_flags_are_not(self):
        schema = next(s["input_schema"] for s in actions.schemas() if s["name"] == "run_cell")
        self.assertEqual(schema["properties"]["browser"], {"type": "boolean"})
        self.assertFalse(schema["additionalProperties"])
        good = {"name": "probe", "code": "pass", "browser": True}
        self.assertEqual(actions._argument_error("run_cell", good), "")
        self.assertIn("unknown", actions._argument_error("run_cell", {**good, "browser_ok": True}))
        self.assertIn("unknown", actions._argument_error("run_cell", {"cells": [{**good, "send_ok": True}]}))

    def test_bad_modes_and_types_never_call_a_handler_and_are_recorded(self):
        p = _bootstrap.workflow([], pid="argument_boundary")

        bad = [[], {"code": "pass"}, {"name": "x", "code": "pass", "browser": "true"},
               {"cells": [], "name": "x"}, {"cells": [{"cells": []}]}]
        trail.begin(p["id"], "bad-calls", "start")
        with patch.object(actions, "_execute") as handler:
            for value in bad:
                got = actions.execute(p, {"name": "run_cell", "input": value}, None)
                self.assertFalse(got["ok"])
                self.assertEqual(got["failure_stage"], "schema")
            handler.assert_not_called()
        trail.end(p["id"])
        self.assertEqual(len([i for i in trail.turns(p["id"])[0]["items"] if i["kind"] == "tool"]), len(bad))

    def test_internal_type_error_is_execution_not_bad_arguments(self):
        p = _bootstrap.workflow([], pid="argument_typeerror")
        def broken(workflow):
            raise TypeError("internal bug")
        with patch.object(actions, "_fns", return_value={"list_samples": broken}):
            got = actions.execute(p, {"name": "list_samples", "input": {}}, None)
        self.assertEqual(got["failure_stage"], "execution")
        self.assertNotIn("bad arguments", got["error"])

if __name__ == "__main__":
    unittest.main()
