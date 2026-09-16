# Tests: workflow lifecycle at the store level: hard delete removes the whole bundle, mid-turn saves never clobber user writes, traversal ids are refused, and write hygiene (about.txt only rewrites on real change)
from __future__ import annotations

import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

import config
from storage import blobstore
from storage import store

class LifecycleTest(unittest.TestCase):
    def test_about_txt_is_not_rewritten_when_unchanged(self):
        pid = "p_about"
        p = {"id": pid, "name": "Invoices", "nodes": [], "edges": [],
             "chat": [], "variables": []}
        config.workflow_dir(pid).mkdir(parents=True, exist_ok=True)
        store.save(p)
        f = config.workflow_dir(pid) / "about.txt"
        before = f.stat().st_mtime_ns
        store.save(p)
        self.assertEqual(f.stat().st_mtime_ns, before)
        p["name"] = "Invoices (renamed)"
        store.save(p)
        self.assertNotEqual(f.stat().st_mtime_ns, before)
        self.assertIn("renamed", f.read_text())

    def test_save_strips_transient_underscore_keys(self):
        p = _workflow(pid="p_lc_t")
        p["_pending_build"] = {"include_proposals": None}
        store.save(p)
        self.assertEqual(p["_pending_build"], {"include_proposals": None})
        self.assertNotIn("_pending_build", store.load("p_lc_t"))

    def test_hard_delete_removes_the_live_bundle(self):
        _workflow(pid="p_lc2")
        self.assertTrue(store.delete_workflow("p_lc2")["ok"])
        self.assertIsNone(store.load("p_lc2"))
        self.assertIn("error", store.delete_workflow("p_lc2"))

    def test_mid_turn_upload_survives_turn_save(self):
        _workflow(pid="p_race")
        turn_copy = store.load("p_race")
        uploader = store.load("p_race")
        uploader.setdefault("samples", []).append(
            {"name": "cv.pdf", "ref": "blob:" + "a" * 64, "mime": "application/pdf"})
        store.save(uploader)
        from agent import transcript as _tr
        _tr.append_message(turn_copy, "assistant", "hi")
        store.save_turn(turn_copy)
        after = store.load("p_race")
        self.assertEqual([s["name"] for s in after.get("samples", [])], ["cv.pdf"])
        self.assertEqual(after["transcript"][-1]["text"], "hi")

        fresh = store.load("p_race")
        fresh["samples"] = []
        store.save(fresh)
        self.assertEqual(store.load("p_race")["samples"], [])

    def test_mid_turn_variable_edit_survives_turn_save(self):
        p = _workflow(pid="p_varrace")
        p["variables"] = [{"name": "region", "value": "", "secret": False,
                           "persistent": True}]
        store.save(p)
        turn_copy = store.load("p_varrace")
        user = store.load("p_varrace")
        user["variables"][0]["value"] = "EMEA"
        user["variables"].append({"name": "added_by_user", "value": "1",
                                  "secret": False, "persistent": True})
        store.save(user)
        turn_copy.setdefault("variables", []).append(
            {"name": "api_base", "value": "", "secret": False, "persistent": True})
        store.save_turn(turn_copy)
        after = {v["name"]: v for v in store.load("p_varrace")["variables"]}
        self.assertEqual(after["region"]["value"], "EMEA")
        self.assertIn("added_by_user", after)
        self.assertIn("api_base", after)

    def test_path_traversal_ids_refused(self):
        for bad in ("..", ".", "../workflows", "a/b", ""):
            self.assertIn("error", store.delete_workflow(bad), bad)
        self.assertTrue(config.DATA_DIR.exists())

if __name__ == "__main__":
    unittest.main()

class SecretMarkerTest(unittest.TestCase):
    def test_marker_derives_from_the_secret_store(self):
        from storage import secrets_store
        from storage import store
        p = {"id": "p_secmark", "name": "s", "nodes": [], "edges": [], "chat": [],
             "samples": [],
             "variables": [{"name": "api_key", "value": None,
                            "secret": True, "persistent": True}]}
        store.save(p)
        secrets_store.set_secret("api_key", "v-123", secrets_store.workflow_owner("p_secmark"))
        store.save_turn(p)
        saved = store.load("p_secmark")
        v = next(x for x in saved["variables"] if x["name"] == "api_key")
        self.assertIs(v["value"], True)
        secrets_store.delete_secret("api_key", secrets_store.workflow_owner("p_secmark"))
        store.save_turn(p)
        v = next(x for x in store.load("p_secmark")["variables"]
                 if x["name"] == "api_key")
        self.assertIsNone(v["value"])

class ReadOnlyListingTest(unittest.TestCase):
    def test_listing_changes_nothing_in_the_bundle(self):
        import os

        import config
        from storage import store
        p = {"id": "p_romt", "name": "quiet", "nodes": [], "edges": [],
             "chat": [], "variables": [], "samples": []}
        store.save(p)
        d = config.workflow_dir("p_romt")
        before = {f: os.stat(d / f).st_mtime_ns for f in os.listdir(d)}
        store.list_workflows()
        store.load_ro("p_romt")
        after = {f: os.stat(d / f).st_mtime_ns for f in os.listdir(d)}
        self.assertEqual(before, after)

    def test_load_ro_reads_and_recover_still_recovers(self):
        from storage import store
        p = {"id": "p_rorec", "name": "stuck", "nodes": [], "edges": [],
             "chat": [], "variables": [], "samples": [],
             "plan": {"status": "building", "nodes": []}}
        store.save(p)
        self.assertEqual(store.load_ro("p_rorec")["plan"]["status"], "building")
        self.assertGreaterEqual(store.recover_stuck_plans(), 1)
        self.assertEqual(store.load_ro("p_rorec")["plan"]["status"], "draft")

class DuplicateTest(unittest.TestCase):
    def _src(self, pid="p_dup_src"):
        p = _workflow(pid=pid)
        p["name"] = "Invoices"
        p["nodes"] = [{"id": "n1", "name": "Fetch", "type": "code",
                       "config": {"code": "x"}, "inputs": [], "outputs": [],
                       "stats": {"runs": 4}}]
        p["edges"] = [{"src": "n1", "dst": "n1", "when": ""}]
        p["variables"] = [{"name": "region", "secret": False,
                           "persistent": True, "value": "EMEA"}]
        p["tickets"] = [{"id": "tkt_1"}]
        p["last_run_ts"] = 123.0
        p["variables"].append(
            {"name": "rates_json", "type": "file", "secret": False,
             "persistent": True,
             "value": blobstore.put(b"[1]", "application/json",
                                    {"name": "rates.json"}, owner=blobstore.OWNER_APP)})
        store.save(p)
        return p

    def test_duplicate_copies_workflow_and_resets_history(self):
        self._src()
        r = store.duplicate_workflow("p_dup_src")
        self.assertTrue(r["ok"])
        copy = r["workflow"]
        self.assertNotEqual(copy["id"], "p_dup_src")
        self.assertEqual(copy["name"], "Invoices - Copy")

        self.assertEqual(copy["nodes"][0]["config"]["code"], "x")
        self.assertEqual(copy["variables"][0]["value"], "EMEA")
        import json as _json
        rates = next(v for v in copy["variables"] if v["name"] == "rates_json")
        self.assertEqual(_json.loads(blobstore.get(rates["value"])), [1])

        self.assertEqual(copy["tickets"], [])
        self.assertNotIn("last_run_ts", copy)
        self.assertNotIn("stats", copy["nodes"][0])
        self.assertEqual(copy["edges"][0]["src"], "n1")
        self.assertEqual(copy["nodes"][0]["lineage"],
                         {"from_node": "n1", "from_project": "p_dup_src"})

        loaded = store.load(copy["id"])
        self.assertEqual(loaded["name"], "Invoices - Copy")
        loaded["nodes"][0]["config"]["code"] = "y"
        store.save(loaded)
        self.assertEqual(store.load("p_dup_src")["nodes"][0]["config"]["code"], "x")
        self.assertEqual(store.load("p_dup_src")["tickets"], [{"id": "tkt_1"}])

    def test_duplicate_takes_a_custom_name_and_refuses_missing(self):
        self._src(pid="p_dup_src2")
        r = store.duplicate_workflow("p_dup_src2", name="Invoices cloud")
        self.assertEqual(r["workflow"]["name"], "Invoices cloud")
        self.assertIn("error", store.duplicate_workflow("p_nope"))
        self.assertIn("error", store.duplicate_workflow("../evil"))

class SaveTurnOwnershipTest(unittest.TestCase):
    def test_user_owned_keys_survive_the_turn_save(self):
        p = _workflow(pid="p_own")
        p["nodes"] = [{"id": "n1", "name": "Send", "type": "connector",
                       "read_only": False, "config": {}, "inputs": [],
                       "outputs": [], "tests": []}]
        p["tickets"] = [{"id": "t1", "status": "open", "node_id": "n1"}]
        store.save(p)
        turn_copy = store.load("p_own")
        user = store.load("p_own")
        user["environment_ids"] = ["env_a"]
        user["env_bindings"] = {"region": "workflow"}
        user["tickets"][0]["status"] = "dismissed"
        user["nodes"][0]["approval_suppressed"] = True
        store.save(user)
        from agent import transcript as _tr
        _tr.append_message(turn_copy, "assistant", "hi")
        store.save_turn(turn_copy)
        after = store.load("p_own")
        self.assertEqual(after["environment_ids"], ["env_a"])
        self.assertEqual(after["env_bindings"], {"region": "workflow"})
        self.assertEqual(after["tickets"][0]["status"], "dismissed")
        self.assertTrue(after["nodes"][0]["approval_suppressed"])
        self.assertEqual(after["transcript"][-1]["text"], "hi")

    def test_corrupt_bundle_never_bricks_the_listing(self):
        _workflow(pid="p_good")
        bad = config.workflow_dir("p_corrupt")
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "workflow.db").write_bytes(b"this is not a sqlite database....")
        self.addCleanup(store.delete_workflow, "p_corrupt")
        cards = store.list_workflows()
        ids = [c["id"] for c in cards]
        self.assertIn("p_good", ids)
        bad_card = next(c for c in cards if c["id"] == "p_corrupt")
        self.assertTrue(bad_card.get("unreadable"))

        self.assertIsNone(store.load_ro("p_corrupt"))
        self.assertIsNone(store.load("p_corrupt"))
        store.recover_stuck_plans()
        from storage import environments
        environments.save({"id": "env_walk_c", "name": "W", "description": "",
                           "group": "", "variables": []})
        environments.delete("env_walk_c")

class SecretLifecycleTest(unittest.TestCase):
    def test_environment_delete_prunes_its_secrets(self):
        from storage import environments
        from storage import secrets_store
        env = {"id": "env_sl", "name": "E",
               "variables": [{"name": "api_key", "secret": True},
                             {"name": "region", "secret": False, "value": "eu"}]}
        environments.save(env)
        secrets_store.set_secret("api_key", "v-1", secrets_store.environment_owner("env_sl"))
        environments.delete("env_sl")
        self.assertFalse(secrets_store.has_secret("api_key", secrets_store.environment_owner("env_sl")))

class NeedsAttentionTest(unittest.TestCase):
    def test_new_assistant_message_lights_and_seen_clears(self):
        import time
        from tests._bootstrap import workflow as _workflow
        from storage import store
        from agent import transcript
        p = _workflow([], pid="p_attn1")
        self.assertFalse(store._needs_attention(p))
        transcript.append_message(p, "assistant", "Done - built and checked.")
        self.assertTrue(store._needs_attention(p))
        p["attention_seen_ts"] = time.time()
        self.assertFalse(store._needs_attention(p))
        transcript.append_request(p, "ask", {"question": "Which sheet?"})
        self.assertTrue(store._needs_attention(p))
        p["attention_seen_ts"] = time.time()
        self.assertFalse(store._needs_attention(p))
        p["tickets"] = [{"id": "t1", "status": "open", "ts": time.time() + 1}]
        self.assertTrue(store._needs_attention(p))

    def test_activity_only_and_user_entries_never_light(self):
        from tests._bootstrap import workflow as _workflow
        from storage import store
        p = _workflow([], pid="p_attn3")

        from agent import transcript
        transcript.append_message(p, "user", "hello")
        self.assertFalse(store._needs_attention(p))

    def test_card_carries_the_flag(self):
        from tests._bootstrap import workflow as _workflow
        from storage import store
        p = _workflow([], pid="p_attn2")
        from agent import transcript
        transcript.append_request(p, "ask", {"question": "Which sheet?"})
        store.save(p)
        card = next(c for c in store.list_workflows() if c["id"] == "p_attn2")
        self.assertTrue(card["needs_attention"])

class PersistEntryValuesTest(unittest.TestCase):
    def test_fills_only_empty_persistent_nonsecret(self):
        from tests._bootstrap import workflow as _workflow
        from storage import store
        p = _workflow([], pid="p_pev1")
        p["variables"] = [
            {"name": "feed_url", "value": "", "secret": False, "persistent": True},
            {"name": "region", "value": "EU", "secret": False, "persistent": True},
            {"name": "run_note", "value": "", "secret": False, "persistent": False},
            {"name": "api_key", "value": None, "secret": True, "persistent": True},
        ]
        entry = {"feed_url": "https://r/x.rss", "region": "US",
                 "run_note": "today only", "api_key": "sekrit"}
        self.assertTrue(store.persist_entry_values(p, entry))
        by = {v["name"]: v for v in p["variables"]}
        self.assertEqual(by["feed_url"]["value"], "https://r/x.rss")
        self.assertEqual(by["region"]["value"], "EU")
        self.assertEqual(by["run_note"]["value"], "")
        self.assertIsNone(by["api_key"]["value"])

        self.assertFalse(store.persist_entry_values(p, entry))

class OriginFieldTest(unittest.TestCase):
    def test_origin_survives_save_and_duplicate(self):
        p = {"id": "p_origin", "name": "Shared thing", "nodes": [],
             "edges": [], "variables": [], "transcript": [],
             "transcript_seq": 0,
             "origin": {"workflow_id": "wf_123", "version": 3}}
        store.save(p)
        self.assertEqual(store.load("p_origin")["origin"],
                         {"workflow_id": "wf_123", "version": 3})
        r = store.duplicate_workflow("p_origin")
        self.assertEqual(r["workflow"]["origin"],
                         {"workflow_id": "wf_123", "version": 3})

    def test_absent_origin_stays_absent(self):
        p = {"id": "p_noorigin", "name": "Local", "nodes": [], "edges": [],
             "variables": [], "transcript": [], "transcript_seq": 0}
        store.save(p)
        self.assertNotIn("origin", store.load("p_noorigin"))

class DeleteTakesEverythingTest(unittest.TestCase):
    def test_owned_state_goes_with_the_bundle(self):
        import config
        from agent import chatlog
        from runtime import run_state
        from storage import blobstore, secrets_store, store, db
        pid = "p_del_all"
        store.save({"id": pid, "name": "d", "nodes": [], "edges": [], "chat": [],
                    "variables": [{"name": "k", "secret": True, "value": None, "persistent": True}]})
        owner = secrets_store.workflow_owner(pid)
        secrets_store.set_secret("k", "v", owner)
        ref = blobstore.put(b"owned bytes", "text/plain", {"name": "mine.txt"}, owner=owner)
        config.browser_profile_dir(pid).mkdir(parents=True, exist_ok=True)
        config.checkpoint_file(f"cell_{pid}_step").write_text("{}")
        run_state.start(pid, "run_del_1")
        run_state.halt("run_del_1", "n1", "missing-value")
        chatlog.append(pid, "user", {"content": "hi"})
        self.assertEqual(store.delete_workflow(pid), {"ok": True})
        self.assertFalse(secrets_store.has_secret("k", owner))
        self.assertEqual(blobstore.stat(ref, owner), {})
        self.assertFalse(blobstore.exists(ref))
        self.assertFalse(config.browser_profile_dir(pid).exists())
        self.assertFalse(config.checkpoint_file(f"cell_{pid}_step").exists())
        self.assertIsNone(db.runstate_get("run_del_1"))
        self.assertFalse((config.DATA_DIR / "logs" / f"{pid}.jsonl").exists())
