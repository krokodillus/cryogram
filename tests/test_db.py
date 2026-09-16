# Tests: the SQLite app store (Q7): settings/secrets/environments/runstate/blob-meta round-trips in app.db, per-workflow workflow.db documents, the one-time JSON->SQLite import, and the housekeeping sweeps (stale runstate, orphan blobs)
from __future__ import annotations

import json
import unittest

from tests import _bootstrap

import config
from storage import db
from storage import environments
from storage import secrets_store
from storage import settings
from storage import store

class AppDbTest(unittest.TestCase):
    def test_settings_round_trip_and_no_storage_toggle(self):
        self.assertNotIn("storage", settings.DEFAULTS)
        settings.update({"master_ai": {"model": "claude-sonnet-5"}})
        self.assertEqual(settings.get()["master_ai"]["model"], "claude-sonnet-5")

        self.assertIsNotNone(db.get_settings_row())

        db.put_settings_row({**db.get_settings_row(), "storage": {"backend": "json"}})
        self.assertNotIn("storage", settings.get())

    def test_preferences_default_on_and_toggles(self):
        self.assertTrue(settings.get()["preferences"]["notifications"])
        settings.update({"preferences": {"notifications": False}})
        self.assertFalse(settings.get()["preferences"]["notifications"])
        self.assertIn("preferences", settings.public_view())

    def test_secret_round_trip(self):
        secrets_store.set_secret("K_ONE", "v1", secrets_store.OWNER_APP)
        self.assertTrue(secrets_store.has_secret("K_ONE", secrets_store.OWNER_APP))
        self.assertEqual(secrets_store.get_secret("K_ONE", secrets_store.OWNER_APP), "v1")
        self.assertIn("K_ONE", secrets_store.names(secrets_store.OWNER_APP))

        self.assertFalse(secrets_store.has_secret("K_ONE", secrets_store.workflow_owner("wfl_other")))
        self.assertNotIn("K_ONE", secrets_store.names(secrets_store.workflow_owner("wfl_other")))
        secrets_store.delete_secret("K_ONE", secrets_store.OWNER_APP)
        self.assertFalse(secrets_store.has_secret("K_ONE", secrets_store.OWNER_APP))

    def test_environment_round_trip(self):
        environments.save({"id": "env_1", "name": "Prod", "variables": [{"name": "x"}]})
        self.assertEqual(environments.load("env_1")["name"], "Prod")
        environments.delete("env_1")
        self.assertIsNone(environments.load("env_1"))

    def test_runstate_round_trip_and_stale_sweep(self):
        import time

        from runtime import run_state
        rec = {"run_id": "run_db1", "workflow_id": "p_x", "status": "halted",
               "started": time.time(), "path": []}
        db.runstate_put(rec)
        self.assertEqual(db.runstate_get("run_db1")["status"], "halted")
        self.assertIn("run_db1", [r["run_id"] for r in db.runstate_all()])

        db.runstate_put({**rec, "run_id": "run_db_old",
                         "started": time.time() - 90 * 86400})
        db.runstate_put({**rec, "run_id": "run_db_dead", "status": "running"})
        removed = run_state.sweep_stale(days=30)
        self.assertGreaterEqual(removed, 1)
        self.assertIsNone(db.runstate_get("run_db_old"))
        kept = db.runstate_get("run_db_dead")
        self.assertEqual((kept["status"], kept["reason"]), ("halted", "interrupted"))
        self.assertIsNotNone(db.runstate_get("run_db1"))
        db.runstate_delete("run_db_dead")
        db.runstate_delete("run_db1")
        self.assertIsNone(db.runstate_get("run_db1"))

class WorkflowDbTest(unittest.TestCase):
    def test_workflow_document_round_trip(self):
        p = {"id": "p_db1", "name": "Doc", "nodes": [{"id": "n", "type": "ai"}],
             "edges": [], "variables": [], "chat": []}
        store.save(p)
        self.assertTrue(config.workflow_db("p_db1").exists())
        self.assertEqual(store.load("p_db1")["name"], "Doc")
        cards = {c["id"]: c for c in store.list_workflows()}
        self.assertEqual(cards["p_db1"]["stats"]["ai_nodes"], 1)
        self.assertIsNone(cards["p_db1"]["last_run_ts"])
        self.assertGreater(cards["p_db1"]["updated_ts"], 0)

    def test_card_exposes_last_run_ts_once_set(self):
        store.save({"id": "p_db5", "name": "Ran", "nodes": [], "edges": [],
                    "variables": [], "chat": [], "last_run_ts": 12345.0})
        cards = {c["id"]: c for c in store.list_workflows()}
        self.assertEqual(cards["p_db5"]["last_run_ts"], 12345.0)

    def test_list_workflows_orders_most_recently_updated_first(self):
        from contextlib import closing
        store.save({"id": "p_db3", "name": "Older", "nodes": [], "edges": [],
                    "variables": [], "chat": []})
        store.save({"id": "p_db4", "name": "Newer", "nodes": [], "edges": [],
                    "variables": [], "chat": []})

        with closing(db.workflow_conn("p_db3")) as conn, conn:
            conn.execute("UPDATE workflow SET ts = 100.0 WHERE id = 'p_db3'")
        with closing(db.workflow_conn("p_db4")) as conn, conn:
            conn.execute("UPDATE workflow SET ts = 200.0 WHERE id = 'p_db4'")
        ids = [c["id"] for c in store.list_workflows()]
        self.assertLess(ids.index("p_db4"), ids.index("p_db3"))

if __name__ == "__main__":
    unittest.main()

class OverflowPageSweepTest(unittest.TestCase):
    def test_each_owner_names_the_blob(self):
        from storage import blobstore
        a, b = blobstore.workflow_owner("wfl_blob_a"), blobstore.workflow_owner("wfl_blob_b")
        ref = blobstore.put(b"same bytes", "text/plain", {"name": "first.txt"}, owner=a)
        self.assertEqual(blobstore.put(b"same bytes", "text/plain", {"name": "second.txt"}, owner=b), ref)
        blobstore.put(b"same bytes", "text/plain", {"name": "third.txt"}, owner=a)
        self.assertEqual(blobstore.stat(ref, a)["name"], "first.txt")
        self.assertEqual(blobstore.stat(ref, b)["name"], "second.txt")
        self.assertEqual(blobstore.stat(ref)["name"], "first.txt")
        self.assertEqual(blobstore.stat(ref, blobstore.workflow_owner("wfl_blob_c"))["name"],
                         "first.txt")
        db.blob_meta_delete_owner(a)
        self.assertEqual(blobstore.stat(ref, a)["name"], "second.txt")
        db.blob_meta_delete_owner(b)
