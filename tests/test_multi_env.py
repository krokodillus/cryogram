# Tests: multi-environment workflows: one resolver for everything
from __future__ import annotations

import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from storage import environments
from agent import node_tools
from agent import plan_logic
from storage import secrets_store
from storage import store

def _env(eid, name, variables):
    return {"id": eid, "name": name, "description": "", "group": "",
            "variables": variables}

def _attach(pid, env_ids):
    p = store.load(pid)
    p["environment_ids"] = env_ids
    store.save(p)
    return store.load(pid)

class ResolverTest(unittest.TestCase):
    def setUp(self):
        environments.save(_env("env_docs", "Google Docs", [
            {"name": "doc_folder", "value": "reports", "secret": False},
            {"name": "api_url", "value": "https://docs", "secret": False}]))
        environments.save(_env("env_crm", "HubSpot", [
            {"name": "crm_region", "value": "eu", "secret": False},
            {"name": "api_url", "value": "https://crm", "secret": False}]))

    def test_disjoint_names_resolve_silently(self):
        _workflow(pid="p_me1")
        p = _attach("p_me1", ["env_docs", "env_crm"])
        res = environments.resolve(p)
        self.assertEqual(res["doc_folder"]["source"], "env_docs")
        self.assertEqual(res["crm_region"]["source"], "env_crm")
        self.assertEqual(res["doc_folder"]["value"], "reports")

    def test_clash_is_ambiguous_until_pinned_then_stays_pinned(self):
        _workflow(pid="p_me2")
        p = _attach("p_me2", ["env_docs", "env_crm"])
        self.assertIsNone(environments.resolve(p)["api_url"]["source"])
        self.assertEqual(environments.ambiguous_names(p),
                         {"api_url": ["Google Docs", "HubSpot"]})
        r = node_tools.tool_bind_variable(p, "api_url", "HubSpot")
        self.assertTrue(r.get("ok"), r)
        p = store.load("p_me2")
        res = environments.resolve(p)["api_url"]
        self.assertEqual(res["source"], "env_crm")
        self.assertEqual(res["value"], "https://crm")

        docs = environments.load("env_docs")
        docs["variables"].append({"name": "crm_region", "value": "us",
                                  "secret": False})
        environments.save(docs)
        self.assertEqual(environments.resolve(store.load("p_me2"))
                         ["api_url"]["source"], "env_crm")

    def test_workflow_variable_wins_outright(self):
        _workflow(pid="p_me3")
        p = _attach("p_me3", ["env_docs", "env_crm"])
        p["variables"] = [{"name": "api_url", "value": "https://mine",
                           "secret": False, "persistent": True}]
        store.save(p)
        res = environments.resolve(store.load("p_me3"))["api_url"]
        self.assertEqual(res["source"], "workflow")
        self.assertEqual(res["value"], "https://mine")

    def test_vanished_pick_falls_back_local_never_another_env(self):
        _workflow(pid="p_me4")
        p = _attach("p_me4", ["env_docs", "env_crm"])
        node_tools.tool_bind_variable(p, "api_url", "Google Docs")
        environments.delete("env_docs")
        p = store.load("p_me4")
        self.assertEqual(p["environment_ids"], ["env_crm"])
        self.assertEqual(p.get("env_bindings"), {"api_url": "workflow"})
        res = environments.resolve(p)["api_url"]
        self.assertIsNone(res["source"])
        self.assertEqual([c["id"] for c in res["ambiguous"]], ["env_crm"])

    def test_vanished_pick_creates_blank_for_used_names(self):
        _workflow(pid="p_me4b")
        p = _attach("p_me4b", ["env_docs", "env_crm"])
        p["nodes"] = [{"id": "n1", "type": "code", "name": "Fetch",
                       "inputs": [{"name": "api_url", "type": "text"}],
                       "outputs": [{"name": "out", "type": "text"}]}]
        store.save(p)
        node_tools.tool_bind_variable(store.load("p_me4b"), "api_url",
                                      "Google Docs")
        environments.delete("env_docs")
        p = store.load("p_me4b")
        var = next(v for v in p["variables"] if v["name"] == "api_url")
        self.assertEqual(var["value"], "")
        res = environments.resolve(p)["api_url"]
        self.assertEqual(res["source"], "workflow")

    def test_user_pick_outranks_project_value(self):
        _workflow(pid="p_me4c")
        p = _attach("p_me4c", ["env_docs"])
        p["variables"] = [{"name": "api_url", "value": "https://mine",
                           "secret": False, "persistent": True}]
        p["env_bindings"] = {"api_url": environments.canonical("env_docs",
                                                               "api_url")}
        store.save(p)
        self.assertEqual(environments.resolve(store.load("p_me4c"))
                         ["api_url"]["source"], "env_docs")
        p = store.load("p_me4c")
        p["env_bindings"] = {"api_url": "workflow"}
        store.save(p)
        self.assertEqual(environments.resolve(store.load("p_me4c"))
                         ["api_url"]["source"], "workflow")

    def test_bind_refuses_the_unknown(self):
        _workflow(pid="p_me5")
        p = _attach("p_me5", ["env_docs"])
        self.assertIn("error", node_tools.tool_bind_variable(p, "nope", "Google Docs"))
        self.assertIn("error", node_tools.tool_bind_variable(p, "api_url", "Slack"))

    def test_used_by_counts_membership(self):
        _workflow(pid="p_me6")
        _attach("p_me6", ["env_docs", "env_crm"])
        cards = {e["id"]: e for e in environments.list_environments()}
        self.assertGreaterEqual(cards["env_docs"]["used_by"], 1)
        self.assertGreaterEqual(cards["env_crm"]["used_by"], 1)

class SecretIdentityTest(unittest.TestCase):
    def test_canonical_names_never_collide_and_lookup_follows_the_pin(self):
        from runtime import executor
        environments.save(_env("env_a", "A", [
            {"name": "api_key", "value": None, "secret": True}]))
        environments.save(_env("env_b", "B", [
            {"name": "api_key", "value": None, "secret": True}]))
        secrets_store.set_secret("api_key", "AAA", secrets_store.environment_owner("env_a"))
        secrets_store.set_secret("api_key", "BBB", secrets_store.environment_owner("env_b"))
        _workflow(pid="p_sec")
        p = _attach("p_sec", ["env_a", "env_b"])
        node_tools.tool_bind_variable(p, "api_key", "B")
        p = store.load("p_sec")
        res = environments.resolve(p)["api_key"]
        self.assertTrue(res["secret"])
        smap = {"api_key": res["owner"]}
        self.assertEqual(executor._secret_val("api_key", None, smap), "BBB")
        for eid in ("env_a", "env_b"):
            secrets_store.delete_secret("api_key", secrets_store.environment_owner(eid))

class RetireFallbacksTest(unittest.TestCase):
    def _p(self, pid, variables, nodes=None):
        p = _workflow(pid=pid)
        p["nodes"] = nodes or []
        p["variables"] = variables
        return p

    def test_a_blank_setting_nothing_reads_is_retired(self):
        node = {"id": "n1", "name": "Fetch", "type": "code", "config": {},
                "inputs": [{"name": "feed_url", "type": "text"}],
                "outputs": [{"name": "posts", "type": "any"}], "tests": []}
        p = self._p("p_ret1", [
            {"name": "feed_url", "value": "", "secret": False},
            {"name": "reddit_feed_url", "value": "", "secret": False}], [node])
        self.assertEqual(environments.retire_unused_fallbacks(p),
                         ["reddit_feed_url"])
        self.assertEqual([v["name"] for v in p["variables"]], ["feed_url"])

    def test_a_value_the_user_typed_is_never_removed(self):
        node = {"id": "n1", "name": "Fetch", "type": "code", "config": {},
                "inputs": [], "outputs": [], "tests": []}
        p = self._p("p_ret2", [
            {"name": "old_link", "value": "https://kept", "secret": False},
            {"name": "old_key", "value": None, "secret": True}], [node])
        self.assertEqual(environments.retire_unused_fallbacks(p), [])
        self.assertEqual(len(p["variables"]), 2)

    def test_nothing_happens_before_there_are_steps_to_judge_against(self):
        p = self._p("p_ret3", [{"name": "early", "value": "", "secret": False}])
        self.assertEqual(environments.retire_unused_fallbacks(p), [])
        self.assertEqual(len(p["variables"]), 1)

    def test_a_name_the_plan_is_about_to_use_survives(self):
        node = {"id": "n1", "name": "Old", "type": "code", "config": {},
                "inputs": [], "outputs": [], "tests": []}
        p = self._p("p_ret4", [{"name": "arriving", "value": "", "secret": False}],
                    [node])
        plan = {"nodes": [{"name": "New", "type": "code",
                           "inputs": [{"name": "arriving", "type": "text"}]}]}
        self.assertEqual(environments.retire_unused_fallbacks(p, plan), [])
        self.assertEqual(len(p["variables"]), 1)

class PlanGapTest(unittest.TestCase):
    def test_a_used_clash_is_a_design_gap_an_unused_one_is_silent(self):
        environments.save(_env("env_x", "X", [
            {"name": "base_url", "value": "https://x", "secret": False},
            {"name": "quiet_clash", "value": "1", "secret": False}]))
        environments.save(_env("env_y", "Y", [
            {"name": "base_url", "value": "https://y", "secret": False},
            {"name": "quiet_clash", "value": "2", "secret": False}]))
        _workflow(pid="p_gap")
        p = _attach("p_gap", ["env_x", "env_y"])
        plan = {"summary": "fetch", "nodes": [
            {"name": "fetch", "type": "code", "description": "fetch the page",
             "inputs": [{"name": "base_url", "type": "text"}],
             "outputs": [{"name": "page", "type": "longtext"}]}], "edges": []}
        findings = plan_logic.check(p, plan)["findings"]
        self.assertTrue(any("base_url" in f and "more than one" in f
                            for f in findings), findings)
        self.assertFalse(any("quiet_clash" in f for f in findings), findings)

        node_tools.tool_bind_variable(p, "base_url", "X")
        p = store.load("p_gap")
        findings = plan_logic.check(p, plan)["findings"]
        self.assertFalse(any("base_url" in f for f in findings), findings)

if __name__ == "__main__":
    unittest.main()

class TypedEverywhereTest(unittest.TestCase):
    def _proj_with_port(self, pid, port):
        p = _workflow(pid=pid)
        p["nodes"] = [{"id": "n1", "name": "Step", "type": "code", "config": {},
                       "inputs": [port], "outputs": [], "tests": []}]
        return p

    def test_pass_through_types_are_checked_where_they_are_stored(self):
        cases = [
            ({"name": "cutoff", "type": "date"}, "tomorrow-ish", "2026-01-31"),
            ({"name": "mode", "type": "enum", "options": ["fast", "slow"]},
             "sideways", "fast"),
        ]
        for port, bad, good in cases:
            p = self._proj_with_port(f"p_fit_{port['name']}", port)
            ok, want = environments.value_fits_port(bad, port)
            self.assertFalse(ok, f"{bad!r} should not fit {port['type']}")
            self.assertTrue(want)
            self.assertTrue(environments.value_fits_port(good, port)[0])

            self.assertTrue(environments.value_fits_port("", port)[0])

    def test_an_environment_value_is_typed_by_the_step_that_reads_it(self):
        environments.save(_env("env_t", "T", [
            {"name": "posts_per_scan", "value": "100", "secret": False}]))
        _workflow(pid="p_envtype")
        p = _attach("p_envtype", ["env_t"])
        p["nodes"] = [{"id": "n1", "name": "Fetch", "type": "code", "config": {},
                       "inputs": [{"name": "posts_per_scan", "type": "number"}],
                       "outputs": [], "tests": []}]
        self.assertEqual(environments.resolve(p)["posts_per_scan"]["value"], 100)

    def test_an_untyped_reader_leaves_the_value_alone(self):
        environments.save(_env("env_u", "U", [
            {"name": "note", "value": "100", "secret": False}]))
        _workflow(pid="p_envtype2")
        p = _attach("p_envtype2", ["env_u"])
        self.assertEqual(environments.resolve(p)["note"]["value"], "100")

    def test_the_reader_and_the_run_never_disagree(self):
        environments.save(_env("env_v", "V", [
            {"name": "posts_per_scan", "value": "lots", "secret": False}]))
        _workflow(pid="p_envtype3")
        p = _attach("p_envtype3", ["env_v"])
        p["nodes"] = [{"id": "n1", "name": "Fetch", "type": "code", "config": {},
                       "inputs": [{"name": "posts_per_scan", "type": "number",
                                   "label": "Posts per scan"}],
                       "outputs": [], "tests": []}]
        from agent import receipts
        findings = receipts._settings_fit(p)
        self.assertTrue(findings)
        self.assertIn("Posts per scan", findings[0])
