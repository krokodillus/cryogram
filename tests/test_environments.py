# Tests: environment store seams: round-trip, cards view counts, delete-detaches
from __future__ import annotations

import unittest

from tests import _bootstrap
from agent import turnstate

from storage import environments
from storage import store

def _env(eid="env_t", name="CRM test", variables=None):
    return {"id": eid, "name": name, "description": "", "group": "",
            "variables": variables if variables is not None else [
                {"name": "crm_base_url", "value": "https://x", "secret": False,
                 "kind": "logic-affecting"}]}

class EnvironmentsTest(unittest.TestCase):
    def test_roundtrip(self):
        environments.save(_env())
        loaded = environments.load("env_t")
        self.assertEqual(loaded["name"], "CRM test")
        self.assertEqual(loaded["variables"][0]["name"], "crm_base_url")

    def test_list_counts_variables_and_used_by(self):
        environments.save(_env("env_used"))
        store.save({"id": "p_envtest", "name": "p", "environment_ids": ["env_used"],
                    "nodes": [], "edges": [], "variables": [], "chat": []})
        cards = {e["id"]: e for e in environments.list_environments()}
        self.assertEqual(cards["env_used"]["variable_count"], 1)
        self.assertEqual(cards["env_used"]["used_by"], 1)

    def test_duplicate_copies_variables_and_secret_values(self):
        from storage import secrets_store
        environments.save(_env("env_dup", "Sales", variables=[
            {"name": "base_url", "value": "https://x", "secret": False},
            {"name": "api_key", "value": None, "secret": True}]))
        secrets_store.set_secret("api_key", "sk-live", secrets_store.environment_owner("env_dup"))
        r = environments.duplicate_environment("env_dup")
        self.assertTrue(r.get("ok"), r)
        copy = r["environment"]
        self.assertNotEqual(copy["id"], "env_dup")
        self.assertEqual(copy["name"], "Sales - Copy")
        self.assertEqual([v["name"] for v in copy["variables"]],
                         ["base_url", "api_key"])
        self.assertEqual(secrets_store.get_secret(
            "api_key", secrets_store.environment_owner(copy["id"])), "sk-live")

        copy["variables"][0]["value"] = "https://changed"
        environments.save(copy)
        self.assertEqual(environments.load("env_dup")["variables"][0]["value"],
                         "https://x")
        cards = {e["id"]: e for e in environments.list_environments()}
        self.assertEqual(cards[copy["id"]]["used_by"], 0)

    def test_duplicate_unknown_env_errors(self):
        self.assertEqual(environments.duplicate_environment("env_nope"),
                         {"error": "not found"})

    def test_delete_detaches_workflows(self):
        environments.save(_env("env_gone"))
        store.save({"id": "p_detach", "name": "p", "environment_ids": ["env_gone"],
                    "nodes": [], "edges": [], "variables": [], "chat": []})
        environments.delete("env_gone")
        self.assertIsNone(environments.load("env_gone"))
        self.assertEqual(store.load("p_detach")["environment_ids"], [])

if __name__ == "__main__":
    unittest.main()

class EnvironmentReachesTheTurnAtOnceTest(unittest.TestCase):
    def _turn_copy(self, pid):
        p = {"id": pid, "name": "t", "nodes": [], "edges": [], "variables": [],
             "environment_ids": [], "intent": {"summary": "Purpose."}}
        store.save(p)
        turn = store.load(pid)
        return turn

    def test_attach_mid_turn_lands_on_the_next_tool_result(self):
        from agent import actions, context
        environments.save(_env("env_goog", "Google", variables=[
            {"name": "service_account_key", "value": "", "secret": True,
             "kind": "config"},
            {"name": "sheet_id", "value": "abc", "secret": False, "kind": "config"}]))
        turn = self._turn_copy("p_envturn1")
        context.build(turn, "chat", "hello")
        self.assertEqual(turnstate.of(turn).env_seen, {})

        disk = store.load("p_envturn1")
        disk["environment_ids"] = ["env_goog"]
        store.save(disk)
        out = actions.execute(turn, {"name": "list_nodes", "input": {}},
                              lambda e: None)
        self.assertEqual(turn["environment_ids"], ["env_goog"])
        note = out.get("environment_update", "")
        self.assertIn('ATTACHED environment "Google"', note)
        self.assertIn("service_account_key (secret)", note)
        self.assertIn("sheet_id", note)
        self.assertNotIn("abc", note)

        out2 = actions.execute(turn, {"name": "list_nodes", "input": {}},
                               lambda e: None)
        self.assertNotIn("environment_update", out2)

        env = environments.load("env_goog")
        env["variables"].append({"name": "tab_name", "value": "x",
                                 "secret": False, "kind": "config"})
        environments.save(env)
        out3 = actions.execute(turn, {"name": "list_nodes", "input": {}},
                               lambda e: None)
        self.assertIn("gained variables: tab_name", out3.get("environment_update", ""))

    def test_unattached_environments_are_named_in_the_context(self):
        from agent import orchestrator
        environments.save(_env("env_other", "Slack team", variables=[
            {"name": "slack_token", "value": "", "secret": True, "kind": "config"}]))
        turn = self._turn_copy("p_envturn2")
        ctx = orchestrator._environment_context(turn)
        self.assertIn("NOT attached", ctx)
        self.assertIn('"Slack team" (slack_token (secret))', ctx)
        self.assertIn("attach it", ctx)
        self.assertNotIn("_env_seen", ctx)
