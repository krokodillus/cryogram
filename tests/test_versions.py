# Tests: the workflow version ledger: versions cut at meaningful moments with hash-dedupe and a drop-oldest cap; restore reverts the config subset while keeping the user's values, deriving secret markers from the store and leaving the timeline (chat/tickets) alone; run manifests carry the config hash they executed under
from __future__ import annotations

import unittest

from tests import _bootstrap

from storage import db, store

def _workflow(pid: str) -> dict:
    p = {"id": pid,
         "nodes": [{"id": "n1", "name": "Step one", "type": "code",
                    "config": {"code": "v1"}, "stats": {"runs": 3}}],
         "edges": [],
         "variables": [{"name": "cap", "label": "Cap", "value": "10",
                        "secret": False, "persistent": True}],
         "chat": [{"role": "user", "content": "hello"}],
         "tickets": [{"id": "t1", "status": "open"}]}
    store.save(p)
    return p

class RecordVersionTest(unittest.TestCase):
    def test_cut_dedupe_and_reasons(self):
        p = _workflow("p_ver_cut")
        self.assertIsNotNone(store.record_version(p, "build"))
        self.assertIsNone(store.record_version(p, "build"))
        p["nodes"][0]["config"]["code"] = "v2"
        store.save(p)
        self.assertIsNotNone(store.record_version(p, "prompt-edit"))
        lst = db.workflow_versions("p_ver_cut")
        self.assertEqual([v["reason"] for v in lst], ["prompt-edit", "build"])
        self.assertTrue(all(v["ts"] > 0 for v in lst))
        self.assertEqual(lst[0]["hash"], p["config_hash"])

    def test_subset_excludes_timeline_and_stats(self):
        p = _workflow("p_ver_subset")
        store.record_version(p, "build")
        data = db.workflow_version_get(
            "p_ver_subset", db.workflow_versions("p_ver_subset")[0]["id"])["data"]
        self.assertNotIn("chat", data)
        self.assertNotIn("tickets", data)
        self.assertNotIn("stats", data["nodes"][0])

        p["chat"].append({"role": "assistant", "content": "hi"})
        store.save(p)
        self.assertIsNone(store.record_version(p, "build"))

    def test_every_version_is_kept(self):
        p = _workflow("p_ver_cap")
        for i in range(5):
            p["nodes"][0]["config"]["code"] = f"v{i}"
            store.save(p)
            store.record_version(p, "build")
        lst = db.workflow_versions("p_ver_cap")
        self.assertEqual(len(lst), 5)
        self.assertEqual(lst[0]["hash"], p["config_hash"])

class DrawerEditVersionsTest(unittest.TestCase):
    def test_prompt_edit_and_reset_cut_versions_with_the_step_name(self):
        import http.client
        import json as _json
        from tests.test_server import _start
        from storage import db, store
        srv, port = _start()
        try:
            pid = "p_ver_prompt"
            store.save({"id": pid, "nodes": [
                {"id": "n1", "name": "Score posts", "type": "ai",
                 "config": {"prompt": "judge", "prompt_agent": "judge"},
                 "inputs": [], "outputs": [{"name": "v", "type": "text"}],
                 "tests": []}], "edges": [], "variables": [], "chat": []})

            def post(path, body):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                c.request("POST", path, body=_json.dumps(body),
                          headers={"Content-Type": "application/json"})
                r = c.getresponse()
                data = _json.loads(r.read() or b"{}")
                c.close()
                return r.status, data

            s, _ = post(f"/api/workflows/{pid}/nodes/n1/prompt",
                        {"prompt": "judge harder"})
            self.assertEqual(s, 200)
            reasons = [v["reason"] for v in db.workflow_versions(pid)]
            self.assertIn("prompt-edit::Score posts", reasons)
            s, _ = post(f"/api/workflows/{pid}/nodes/n1/prompt",
                        {"reset": True})
            self.assertEqual(s, 200)
            reasons = [v["reason"] for v in db.workflow_versions(pid)]
            self.assertIn("prompt-reset::Score posts", reasons)
        finally:
            srv.shutdown()

class RestoreVersionTest(unittest.TestCase):
    def test_restore_reverts_config_keeps_values_and_timeline(self):
        p = _workflow("p_ver_restore")
        store.record_version(p, "build")
        first = db.workflow_versions("p_ver_restore")[0]["id"]
        p["nodes"][0]["config"]["code"] = "v2"
        p["nodes"][0]["stats"] = {"runs": 9}
        p["variables"][0]["value"] = "99"
        store.save(p)
        store.record_version(p, "build")

        res = store.restore_version("p_ver_restore", first)
        self.assertTrue(res["ok"])
        doc = store.load("p_ver_restore")
        self.assertEqual(doc["nodes"][0]["config"]["code"], "v1")
        self.assertEqual(doc["variables"][0]["value"], "99")
        self.assertNotIn("stats", doc["nodes"][0])
        self.assertEqual(len(doc["chat"]), 1)
        self.assertEqual(doc["tickets"][0]["id"], "t1")

        newest = db.workflow_versions("p_ver_restore")[0]
        self.assertEqual(newest["reason"], f"restore::{first}")
        self.assertEqual(newest["hash"], doc["config_hash"])
        self.assertEqual(newest["number"], 1)

    def test_every_version_is_numbered_and_a_restore_borrows_its_targets_number(self):
        p = _workflow("p_ver_numbers")
        store.record_version(p, "build")
        p["nodes"][0]["config"]["code"] = "v2"; store.save(p)
        store.record_version(p, "build")
        v1 = next(v for v in db.workflow_versions("p_ver_numbers") if v["number"] == 1)
        store.restore_version("p_ver_numbers", v1["id"])
        p = store.load("p_ver_numbers")
        p["nodes"][0]["config"]["code"] = "v3"; store.save(p)
        store.record_version(p, "build")
        rows = list(reversed(db.workflow_versions("p_ver_numbers")))
        self.assertEqual([(r["reason"].split("::")[0], r["number"]) for r in rows],
                         [("build", 1), ("build", 2), ("restore", 1), ("build", 3)])

    def test_a_bundle_from_before_is_numbered_on_open(self):
        import sqlite3
        from contextlib import closing
        import config
        p = _workflow("p_ver_old")
        store.record_version(p, "build")
        with closing(sqlite3.connect(str(config.workflow_db("p_ver_old")))) as raw, raw:
            raw.execute("CREATE TABLE old_versions(id INTEGER PRIMARY KEY, ts REAL NOT NULL, hash TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', data TEXT NOT NULL)")
            raw.execute("INSERT INTO old_versions(ts, hash, reason, data) SELECT ts, hash, reason, data FROM versions")
            raw.execute("INSERT INTO old_versions(ts, hash, reason, data) VALUES(1, 'h2', 'build', '{}')")
            raw.execute("DROP TABLE versions")
            raw.execute("ALTER TABLE old_versions RENAME TO versions")
        rows = list(reversed(db.workflow_versions("p_ver_old")))
        self.assertEqual([r["number"] for r in rows], [1, 2])

    def test_restore_with_unversioned_changes_cuts_pre_restore(self):
        p = _workflow("p_ver_undo")
        store.record_version(p, "build")
        first = db.workflow_versions("p_ver_undo")[0]["id"]
        p["nodes"][0]["config"]["code"] = "v2"
        store.save(p)
        res = store.restore_version("p_ver_undo", first)
        self.assertTrue(res["ok"])
        reasons = [v["reason"] for v in db.workflow_versions("p_ver_undo")]
        self.assertIn("pre-restore", reasons)

        pre = next(v for v in db.workflow_versions("p_ver_undo")
                   if v["reason"] == "pre-restore")
        store.restore_version("p_ver_undo", pre["id"])
        self.assertEqual(store.load("p_ver_undo")["nodes"][0]["config"]["code"],
                         "v2")

    def test_secret_marker_derived_from_store_never_trusted(self):
        from storage import secrets_store
        p = _workflow("p_ver_secret")
        p["variables"].append({"name": "api_key", "label": "API key",
                               "value": True, "secret": True,
                               "persistent": True})
        store.save(p)
        store.record_version(p, "build")
        first = db.workflow_versions("p_ver_secret")[0]["id"]
        res = store.restore_version("p_ver_secret", first)
        self.assertTrue(res["ok"])
        sec = next(v for v in store.load("p_ver_secret")["variables"]
                   if v.get("secret"))

        self.assertFalse(secrets_store.has_secret("api_key", secrets_store.workflow_owner("p_ver_secret")))
        self.assertIsNone(sec["value"])

    def test_restore_missing_version_or_workflow_says_so(self):
        p = _workflow("p_ver_miss")
        self.assertFalse(store.restore_version("p_ver_miss", 12345)["ok"])
        self.assertFalse(store.restore_version("p_nope_at_all", 1)["ok"])

class RunManifestAnchorTest(unittest.TestCase):
    def test_manifest_carries_config_hash(self):
        from storage import deliverables
        p = _workflow("p_ver_manifest")
        p["deliverables"] = [{"node": "n1", "port": "out", "label": "Result"}]
        store.save(p)
        m = deliverables.persist(p, "run_x", {"n1": {"out": 42}}, ["n1"])
        self.assertEqual(m["config_hash"], p["config_hash"])
        self.assertEqual(m["results"][0]["value"], 42)

if __name__ == "__main__":
    unittest.main()
