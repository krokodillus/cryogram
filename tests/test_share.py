# Tests: the shareable workflow file (storage/share.py): the workflow as built and nothing else
from __future__ import annotations

import json
import unittest

from tests import _bootstrap

from storage import secrets_store
from storage import share
from storage import store

def _node(nid, ntype="code", **kw):
    n = {"id": nid, "name": kw.pop("name", nid), "type": ntype,
         "description": "", "config": {"prompt": "", "model": {"model": ""},
                                       "code": kw.pop("code", ""),
                                       "dependencies": kw.pop("dependencies", []),
                                       "criteria": []},
         "inputs": [], "outputs": [{"name": "x", "type": "any"}],
         "read_only": False, "external_impact": "", "approval_suppressed": False,
         "stats": {"runs": 3}, "lineage": {"from_project": "old"},
         "tests": [{"name": "recorded run", "inputs": {"a": "owner data"}}]}
    n.update(kw)
    return n

def _rich_workflow(pid="p_share"):
    p = {"id": pid, "name": "Leads to sheet", "description": "Weekly.",
         "group": "Sales", "config_hash": "abc", "config_ts": 1.0,
         "learning_ids": ["l1"], "environment_ids": ["env_share_none"],
         "env_bindings": {"k": "workflow"},
         "nodes": [_node("n1", code="write_output('x', read_input('sheet'))",
                         dependencies=["requests"]),
                   _node("n2", "ai")],
         "edges": [{"src": "n1", "dst": "n2", "when": ""}],
         "variables": [
             {"name": "sheet", "label": "Sheet", "value": "https://s/1",
              "secret": False, "persistent": True},
             {"name": "share_api_key", "value": True, "secret": True, "persistent": True},
             {"name": "doc", "type": "file", "value": "blob:" + "a" * 64,
              "secret": False, "persistent": True}],
         "transcript": [{"seq": 1, "kind": "message", "text": "hi"}],
         "transcript_seq": 1,
         "samples": [{"name": "s.csv", "ref": "blob:" + "b" * 64}],
         "plan": {"summary": "the plan"}, "tickets": [{"id": "t1"}],
         "sessions": {"a": 1}, "published": {"version": 1},
         "intent": {"summary": "Push leads", "instructions": "Run weekly",
                    "facts": ["sheet has 3 tabs"]},
         "decisions": [{"what": "old log"}], "attention_seen_ts": 5.0,
         "master_model": "x", "environment_id": "env_old", "loaded_skills": ["s"],
         "plan_approved_ts": 1.0,
         "deliverables": [{"node": "n2", "port": "x", "label": "Result"}],
         "egress_allowlist": ["sheets.googleapis.com"]}
    store.save(p)
    return p

class ExportTest(unittest.TestCase):
    def test_export_is_the_workflow_and_nothing_else(self):
        p = _rich_workflow("p_sh_ex")
        out = share.export_doc(p)
        self.assertEqual(out["format"], share.FORMAT)
        self.assertEqual(out["origin"]["id"], "p_sh_ex")
        self.assertTrue(out["origin"]["hash"])
        self.assertEqual(out["packages"], ["requests"])
        wf = out["workflow"]

        for k in ("transcript", "transcript_seq", "chat", "samples", "sessions",
                  "tickets", "plan", "published", "learning_ids", "config_hash",
                  "config_ts", "group", "environment_ids", "env_bindings",
                  "decisions", "attention_seen_ts", "master_model",
                  "environment_id", "loaded_skills", "plan_approved_ts"):
            self.assertNotIn(k, wf, k)

        self.assertEqual([n["id"] for n in wf["nodes"]], ["n1", "n2"])
        self.assertEqual(wf["nodes"][0]["config"]["code"],
                         "write_output('x', read_input('sheet'))")
        self.assertEqual(wf["edges"], p["edges"])
        self.assertEqual(wf["deliverables"], p["deliverables"])
        self.assertEqual(wf["egress_allowlist"], p["egress_allowlist"])

        self.assertEqual(wf["intent"], p["intent"])

        for n in wf["nodes"]:
            self.assertEqual(n["tests"], [])
            self.assertNotIn("stats", n)
            self.assertNotIn("lineage", n)

    def test_scrub_is_total_over_values(self):
        p = _rich_workflow("p_sh_val")
        wf = share.export_doc(p)["workflow"]
        names = {v["name"] for v in wf["variables"]}
        self.assertEqual(names, {"sheet", "share_api_key", "doc"})
        for v in wf["variables"]:
            self.assertIsNone(v["value"], v["name"])

        text = json.dumps(wf)
        self.assertNotIn("https://s/1", text)
        self.assertNotIn("a" * 64, text)
        self.assertNotIn("owner data", text)

    def test_the_local_installation_is_never_named(self):
        secrets_store.set_secret("share_api_key", "sk-live-secret", secrets_store.workflow_owner("p_sh_sec"))
        try:
            p = _rich_workflow("p_sh_sec")
            text = json.dumps(share.export_doc(p))
            self.assertNotIn("sk-live-secret", text)
        finally:
            secrets_store.delete_secret("share_api_key", secrets_store.workflow_owner("p_sh_sec"))

class ImportTest(unittest.TestCase):
    def test_import_makes_an_owned_workflow_with_provenance(self):
        p = _rich_workflow("p_sh_src")
        payload = share.export_doc(p)
        r = share.import_doc(payload)
        self.assertTrue(r.get("ok"), r)
        doc = r["workflow"]
        self.assertNotEqual(doc["id"], "p_sh_src")
        self.assertTrue(doc["id"].startswith("wfl_"))
        self.assertEqual(doc["name"], "Leads to sheet")
        self.assertTrue(doc["description"].startswith(share.IMPORT_PREFIX))
        self.assertIn("Weekly.", doc["description"])
        self.assertEqual(doc["origin"]["id"], "p_sh_src")
        self.assertEqual(doc["origin"]["hash"], payload["origin"]["hash"])
        self.assertEqual(doc["intent"]["summary"], "Push leads")
        self.assertEqual(doc["transcript"], [])
        self.assertEqual(doc["tickets"], [])
        self.assertIsNone(doc["plan"])

        loaded = store.load(doc["id"])
        self.assertEqual(loaded["name"], "Leads to sheet")
        self.assertEqual(len(loaded["nodes"]), 2)

    def test_import_scrubs_even_a_hand_edited_file(self):
        payload = share.export_doc(_rich_workflow("p_sh_edit"))
        payload["workflow"]["variables"][0]["value"] = "smuggled"
        payload["workflow"]["nodes"][0]["tests"] = [{"name": "x"}]
        payload["workflow"]["learning_ids"] = ["l9"]
        doc = share.import_doc(payload)["workflow"]
        self.assertIsNone(doc["variables"][0]["value"])
        self.assertEqual(doc["nodes"][0]["tests"], [])
        self.assertNotIn("learning_ids", doc)

    def test_secret_markers_start_unset_on_import(self):
        payload = share.export_doc(_rich_workflow("p_sh_mark"))
        secrets_store.set_secret("share_api_key", "v",
                                 secrets_store.workflow_owner("p_sh_mark"))
        try:
            doc = share.import_doc(payload)["workflow"]
            self.assertIsNone(next(v for v in doc["variables"]
                                   if v["name"] == "share_api_key")["value"])
        finally:
            secrets_store.delete_secret("share_api_key",
                                        secrets_store.workflow_owner("p_sh_mark"))

    def test_a_prefix_is_never_doubled(self):
        payload = share.export_doc(_rich_workflow("p_sh_pre"))
        doc = share.import_doc(share.export_doc(
            share.import_doc(payload)["workflow"]))["workflow"]
        self.assertEqual(doc["description"].count(share.IMPORT_PREFIX.strip()), 1)

    def test_bad_files_are_refused_plainly(self):
        self.assertIn("error", share.import_doc("nonsense"))
        self.assertIn("error", share.import_doc({"format": "other/9",
                                                 "workflow": {"nodes": []}}))
        self.assertIn("error", share.import_doc({"format": share.FORMAT,
                                                 "workflow": {"nodes": [{}]}}))
        self.assertIn("error", share.import_doc({"format": share.FORMAT,
                                                 "workflow": "x"}))

if __name__ == "__main__":
    unittest.main()
