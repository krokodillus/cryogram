# Tests: the fixes from the 2026-09-09 review - secrets, the run's durable record, the process tree, approvals bound to what was shown, and the small boundary cases
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from agent import assembler, plan_check
from runtime import capability, executor, run_state, sandbox
from storage import blobstore, secrets_store, share, store

class SecretsTest(unittest.TestCase):
    def test_an_existing_file_key_is_read_before_any_key_is_made(self):
        existing = b"k" * 32
        saved = {}
        with mock.patch.object(secrets_store.sys, "platform", "darwin"), \
             mock.patch.object(secrets_store, "_keychain_get", lambda: None), \
             mock.patch.object(secrets_store, "_keychain_set", lambda k: saved.setdefault("k", k) or True), \
             mock.patch.object(secrets_store, "_read_key_file", lambda p: existing), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CRYOGRAM_SECRET_KEY_FILE", None)
            key = secrets_store._load_key()
        self.assertEqual(key, existing)
        self.assertEqual(saved.get("k"), existing)

    def test_a_missing_cipher_refuses_the_write(self):
        with mock.patch.object(secrets_store, "_aesgcm", lambda: None):
            with self.assertRaises(secrets_store.SecretsUnavailable) as cm:
                secrets_store._encrypt("value")
        self.assertIn("can't be stored", str(cm.exception))

    def test_a_secret_with_special_characters_is_scrubbed_before_serialising(self):
        secret = 'pa"ss\\wo\nrd-ø'
        out = capability.scrub_value({"note": f"the value is {secret}", "rows": [secret, 3]}, [secret])
        self.assertNotIn(secret, json.dumps(out))
        self.assertEqual(out["rows"], ["***", 3])

    def test_the_runner_never_returns_such_a_secret(self):
        secret = 'qu"ote\\slash\nline-é'
        code = ("write_output('echo', get_secret('PW'))\n"
                "write_output('inside', 'value ' + get_secret('PW') + ' end')\n")
        out = sandbox.run(code, None, {}, {"PW": secret}, timeout=30)
        self.assertNotIn(secret, json.dumps(out))
        self.assertEqual(out["echo"], "***")

    def test_a_run_only_secret_never_reaches_the_manifest(self):
        secret = 'run"only\\pw'
        p = _workflow([{"id": "n_e", "name": "echo", "type": "code",
                        "config": {"code": "write_output('v', get_secret('pw'))\n", "criteria": []},
                        "inputs": [], "outputs": [{"name": "v", "type": "text"}], "tests": []}],
                      pid="p_rev_runonly")
        p["deliverables"] = [{"node": "n_e", "port": "v", "label": "v"}]
        store.save(p)
        res = executor.run_workflow(p, {}, secret_inputs={"pw": secret})
        self.assertEqual(res["status"], "completed", res)
        from storage import deliverables
        runs = deliverables.list_runs(p["id"])
        self.assertTrue(runs)
        self.assertNotIn(secret, json.dumps(runs))
        self.assertNotIn(secret, json.dumps(res))

class DurableRecordTest(unittest.TestCase):
    def test_a_send_marked_before_a_failure_is_never_repeated(self):
        import tempfile
        marker = Path(os.path.realpath(tempfile.mkdtemp(prefix="rev_sent_"))) / "marker.txt"
        marker.write_text("0")
        code = (f"n = int(open({str(marker)!r}).read()) + 1\n"
                f"open({str(marker)!r}, 'w').write(str(n))\n"
                "mark_sent()\n"
                "raise ValueError('the response could not be parsed')\n")
        node = {"id": "n_send", "name": "Send it", "type": "connector",
                "read_only": False, "approval_suppressed": True,
                "config": {"code": code, "paths": [str(marker.parent)]}, "inputs": [],
                "outputs": [{"name": "sent", "type": "boolean"}], "tests": []}
        p = _workflow([node], pid="p_rev_sent")
        p["path_allowlist"] = [str(marker.parent)]
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "halted")
        self.assertEqual(marker.read_text(), "1")
        self.assertTrue(run_state.has_fired(res["run_id"], "n_send"))
        res2 = executor.run_workflow(p, {}, run_id=res["run_id"])
        self.assertEqual(res2["reason"], "fired-unverified")
        self.assertEqual(marker.read_text(), "1")
        run_state.finish(res2["run_id"])

    def test_checkpoint_calls_mark_sent(self):
        d = Path(_bootstrap.TMP) / "rev_ckpt"
        d.mkdir(exist_ok=True)
        with mock.patch.dict(os.environ, {"CRYOGRAM_CHECKPOINT_FILE": str(d / "c.json"),
                                          "CRYOGRAM_SENT_FILE": str(d / "sent")}):
            capability.checkpoint("row-1", True)
        self.assertTrue((d / "sent").exists())

    def test_a_checkpoint_that_cannot_be_written_stops_the_step(self):
        d = Path(_bootstrap.TMP) / "rev_ckpt2"
        d.mkdir(exist_ok=True)
        with mock.patch.dict(os.environ, {"CRYOGRAM_CHECKPOINT_FILE": str(d / "c.json")}), \
             mock.patch.object(capability.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(RuntimeError) as cm:
                capability.checkpoint("row-1", True)
        self.assertIn("checkpoint could not be written", str(cm.exception))

    def test_the_manifest_is_written_before_the_buffer_is_discarded(self):
        p = _workflow([{"id": "n_a", "name": "a", "type": "code",
                        "config": {"code": "write_output('v', 1)\n", "criteria": []},
                        "inputs": [], "outputs": [{"name": "v", "type": "number"}], "tests": []}],
                      pid="p_rev_manifest")
        from storage import deliverables
        with mock.patch.object(deliverables, "persist", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                executor.run_workflow(p, {})
        live = [r for r in run_state.load_all() if r.get("workflow_id") == p["id"]] \
            if hasattr(run_state, "load_all") else None
        if live is None:
            from storage import db
            live = [r for r in db.runstate_all() if r.get("workflow_id") == p["id"]]
        self.assertTrue(live, "the run buffer must survive a failed manifest write")
        self.assertEqual(live[0]["steps"]["n_a"]["output"], {"v": 1})
        for r in live:
            run_state.finish(r["run_id"])

    def test_an_interrupted_run_is_kept_as_a_resumable_halt(self):
        from storage import db
        db.runstate_put({"run_id": "run_rev_int", "workflow_id": "p_x", "status": "running",
                         "started": time.time(), "path": ["n_a"], "steps": {"n_a": {"output": {"v": 1}}}})
        run_state.sweep_stale(days=30)
        kept = db.runstate_get("run_rev_int")
        self.assertEqual((kept["status"], kept["reason"]), ("halted", "interrupted"))
        self.assertEqual(kept["steps"]["n_a"]["output"], {"v": 1})
        db.runstate_delete("run_rev_int")

@unittest.skipIf(os.name == "nt", "process groups are POSIX here")
class ProcessTreeTest(unittest.TestCase):
    def test_stop_ends_the_child_the_harness_started(self):
        tag = f"rev_tree_{os.getpid()}"
        code = ("import subprocess, sys, time\n"
                "from runtime import diskguard\n"
                "with diskguard.harness():\n"
                f"    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60) # {tag}'])\n"
                "time.sleep(60)\n")
        t0 = time.time()
        with self.assertRaises(sandbox.NodeStopped):
            sandbox.run(code, None, {}, {}, timeout=60,
                        should_stop=lambda: time.time() - t0 > 1.5)
        time.sleep(0.5)
        left = subprocess.run(["pgrep", "-f", tag], capture_output=True, text=True)
        self.assertEqual(left.returncode, 1, f"child still running: {left.stdout}")

class ApprovalBindingTest(unittest.TestCase):
    def _flow(self, pid, dest):
        p = _workflow([
            {"id": "n_in", "name": "entry", "type": "user-input", "config": {},
             "inputs": [], "outputs": [{"name": "dest", "type": "text"}], "tests": []},
            {"id": "n_send", "name": "Send", "type": "connector", "read_only": False,
             "external_impact": "sends", "config": {"code": "write_output('ok', True)\n"},
             "inputs": [{"name": "dest", "type": "text"}],
             "outputs": [{"name": "ok", "type": "boolean"}], "tests": []}], pid=pid)
        p["edges"] = [{"src": "n_in", "dst": "n_send", "when": ""}]
        store.save(p)
        return p

    def test_an_approval_is_for_the_values_that_were_shown(self):
        p = self._flow("p_rev_appr", "original")
        res = executor.run_workflow(p, {"dest": "original"})
        self.assertEqual(res["reason"], "awaiting-approval")
        rid = res["run_id"]

        res2 = executor.run_workflow(p, {"dest": "original"}, run_id=rid, approvals={"n_send"})
        self.assertEqual(res2["status"], "completed", res2)

        p2 = self._flow("p_rev_appr2", "original")
        res3 = executor.run_workflow(p2, {"dest": "original"})
        self.assertEqual(res3["reason"], "awaiting-approval")
        rid3 = res3["run_id"]
        run_state.record_step(rid3, "n_in", {"dest": "changed"})
        res4 = executor.run_workflow(p2, {"dest": "changed"}, run_id=rid3, approvals={"n_send"})
        self.assertEqual(res4["reason"], "awaiting-approval")
        self.assertIn("changed", json.dumps(res4["verdict"].get("sending")))

class SmallBoundaryTest(unittest.TestCase):
    def test_a_condition_that_cannot_be_decided_stops_the_run(self):
        p = _workflow([
            {"id": "n_a", "name": "a", "type": "code",
             "config": {"code": "write_output('n', 0)\n", "criteria": []},
             "inputs": [], "outputs": [{"name": "n", "type": "number"}], "tests": []},
            {"id": "n_b", "name": "b", "type": "code",
             "config": {"code": "write_output('v', 1)\n", "criteria": []},
             "inputs": [], "outputs": [{"name": "v", "type": "number"}], "tests": []}],
            pid="p_rev_branch")
        p["edges"] = [{"src": "n_a", "dst": "n_b", "when": "10 / n > 1"}]
        store.save(p)
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "halted")
        self.assertEqual(res["reason"], "branch-condition-error")
        self.assertIn("could not be decided", res["verdict"]["branch"][0])

    def test_a_shared_workflow_carries_no_permission_of_the_senders(self):
        p = _workflow([{"id": "n_w", "name": "Send", "type": "connector", "read_only": False,
                        "approval_suppressed": True, "config": {"code": "x=1"},
                        "inputs": [], "outputs": [], "tests": []}], pid="p_rev_share")
        p["approval_grants"] = [{"step": "Send", "ts": time.time()}]
        p["egress_allowlist"] = ["evil.example"]
        out = share.export_doc(p)
        wf = out["workflow"]
        self.assertNotIn("approval_suppressed", wf["nodes"][0])
        self.assertNotIn("approval_grants", wf)
        self.assertEqual(wf["egress_allowlist"], ["evil.example"])
        back = share.import_doc(out, name="Imported")
        self.assertTrue(back.get("ok"), back)
        imported = store.load(back["workflow"]["id"])
        self.assertFalse(imported["nodes"][0].get("approval_suppressed", False))

    def test_two_writers_of_the_same_bytes_both_succeed(self):
        data = os.urandom(2048)
        errors = []

        def put():
            try:
                blobstore.put(data, "application/octet-stream", {"name": "same.bin"},
                              owner=blobstore.OWNER_APP)
            except Exception as e:
                errors.append(repr(e))
        threads = [threading.Thread(target=put) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        h = __import__("hashlib").sha256(data).hexdigest()
        self.assertTrue(blobstore.exists(f"blob:{h}"))
        self.assertFalse(list(blobstore._path(h).parent.glob(f"{h}*.tmp")))

    def test_a_built_plan_that_lists_no_connections_drops_the_old_ones(self):
        p = _workflow([
            {"id": "n_a", "name": "A", "type": "code", "config": {"code": "x=1"},
             "inputs": [], "outputs": [], "tests": []},
            {"id": "n_b", "name": "B", "type": "code", "config": {"code": "x=1"},
             "inputs": [], "outputs": [], "tests": []}], pid="p_rev_edges")
        p["edges"] = [{"src": "n_a", "dst": "n_b", "when": ""}]
        store.save(p)
        plan = {"nodes": [{"name": "A", "type": "code"}, {"name": "B", "type": "code"}],
                "edges": [], "built_ts": time.time()}
        from agent import node_tools
        with mock.patch.object(node_tools, "build_step", lambda w, n: {"ok": True}):
            assembler.assemble(p, plan, lambda ev: None)
        self.assertEqual(p["edges"], [])

        p["edges"] = [{"src": "n_a", "dst": "n_b", "when": ""}]
        findings = plan_check.check(p, plan)["findings"]
        self.assertTrue(any("not in the plan" in f for f in findings), findings)

if __name__ == "__main__":
    unittest.main()
