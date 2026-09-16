# Tests: the disk guard - what step code may read and write, checked in a child process where the guard is really installed
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import _bootstrap

from runtime import diskguard

ROOT = Path(__file__).resolve().parent.parent

_CHILD = r"""
import json, os, sqlite3, sys, tempfile
sys.path.insert(0, sys.argv[1])
allowed, scratch, data, app_src = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
from runtime import diskguard
out = {}
def attempt(name, fn):
    try:
        fn(); out[name] = "ok"
    except PermissionError as e:
        out[name] = "blocked" if str(e).startswith(diskguard.BLOCK_PREFIX) else "other: " + str(e)
    except Exception as e:  # noqa: BLE001 - reported, not hidden
        out[name] = "error: " + type(e).__name__
diskguard.install([allowed], [scratch], data, [app_src])
attempt("scratch_write", lambda: open(os.path.join(scratch, "a.txt"), "w").close())
attempt("allowed_write", lambda: open(os.path.join(allowed, "a.txt"), "w").close())
attempt("allowed_list", lambda: os.listdir(allowed))
attempt("temp_write", lambda: open(os.path.join(tempfile.gettempdir(), "cg_diskguard_probe"), "w").close())
attempt("interpreter_read", lambda: open(sys.executable, "rb").close())
attempt("system_write", lambda: open(os.path.join("/usr", "cg_diskguard_probe"), "w").close())
attempt("home_list", lambda: os.listdir(os.path.expanduser("~")))
attempt("data_read", lambda: open(os.path.join(data, "app.db"), "rb").close())
attempt("data_sqlite", lambda: sqlite3.connect(os.path.join(data, "app.db")).close())
attempt("allowed_sqlite_ro", lambda: sqlite3.connect(
    "file:" + os.path.join(allowed, "x.db") + "?mode=ro", uri=True).close())
attempt("memory_sqlite", lambda: sqlite3.connect(":memory:").close())
def in_harness():
    with diskguard.harness():
        open(os.path.join(data, "app.db"), "rb").close()
attempt("data_read_in_harness", in_harness)
attempt("app_source_read", lambda: open(os.path.join(app_src, "mod.py"), "rb").close())
attempt("app_source_write", lambda: open(os.path.join(app_src, "evil.py"), "w").close())
attempt("interpreter_write", lambda: open(os.path.join(sys.prefix, "cg_probe.py"), "w").close())
# THE HONEST LIMIT: the guard patches this process, so a step that shells
# out is not bounded by it at all. Pinned so it fails loudly the day that
# stops being true - closing it needs the OS, not another patched function.
def shell_out():
    import subprocess
    target = os.path.join(os.path.dirname(data), "cg_escaped.txt")
    subprocess.run([sys.executable, "-c",
                    "import sys; open(sys.argv[1], 'w').close()", target], check=True)
    if not os.path.exists(target):
        raise AssertionError("the child did not write")
attempt("subprocess_escape", shell_out)
attempt("message_shape", lambda: (_ for _ in ()).throw(PermissionError(diskguard.message("/x"))))
print(json.dumps(out))
"""

class DiskGuardTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "the system roots differ on Windows")
    def test_the_fence_in_a_child_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            allowed, scratch, data, app_src = (
                os.path.join(tmp, n) for n in ("allowed", "scratch", "data", "appsrc"))
            for d in (allowed, scratch, data, app_src):
                os.makedirs(d)
            open(os.path.join(data, "app.db"), "w").close()

            open(os.path.join(app_src, "mod.py"), "w").close()
            import sqlite3
            sqlite3.connect(os.path.join(allowed, "x.db")).close()
            r = subprocess.run([sys.executable, "-c", _CHILD, str(ROOT / "backend"),
                                allowed, scratch, data, app_src],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            out = json.loads(r.stdout.strip().splitlines()[-1])
        expected = {
            "scratch_write": "ok",
            "allowed_write": "ok",
            "allowed_list": "ok",
            "temp_write": "ok",
            "interpreter_read": "ok",
            "system_write": "blocked",
            "home_list": "blocked",
            "data_read": "blocked",
            "data_sqlite": "blocked",
            "allowed_sqlite_ro": "ok",
            "memory_sqlite": "ok",
            "data_read_in_harness": "ok",
            "app_source_read": "ok",
            "app_source_write": "blocked",
            "interpreter_write": "blocked",
            "subprocess_escape": "ok",
            "message_shape": "blocked",
        }
        self.assertEqual(out, expected)

    @unittest.skipIf(os.name == "nt", "the bin is a shell call on Windows")
    def test_a_delete_in_the_persons_own_folder_goes_to_the_bin(self):
        child = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
from runtime import diskguard
allowed, scratch, data = sys.argv[2], sys.argv[3], sys.argv[4]
diskguard.install([allowed], [scratch], data, [])
out = {}
mine = os.path.join(allowed, "invoice.txt")
open(mine, "w").write("keep me")
os.remove(mine)
out["gone_from_the_folder"] = not os.path.exists(mine)
tmpf = os.path.join(scratch, "chatter.tmp")
open(tmpf, "w").write("x")
os.remove(tmpf)
out["scratch_gone"] = not os.path.exists(tmpf)
print(json.dumps(out))
"""
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            allowed, scratch, data = (os.path.join(tmp, n)
                                      for n in ("allowed", "scratch", "data"))
            for d in (home, allowed, scratch, data,
                      os.path.join(home, ".Trash"),
                      os.path.join(home, ".local", "share")):
                os.makedirs(d, exist_ok=True)
            env = dict(os.environ, HOME=home,
                       XDG_DATA_HOME=os.path.join(home, ".local", "share"))
            r = subprocess.run([sys.executable, "-c", child, str(ROOT / "backend"),
                                allowed, scratch, data],
                               capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            out = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertTrue(out["gone_from_the_folder"])
            self.assertTrue(out["scratch_gone"])
            if sys.platform == "darwin":
                binned = os.path.join(home, ".Trash", "invoice.txt")
            else:
                binned = os.path.join(home, ".local", "share", "Trash",
                                      "files", "invoice.txt")
            self.assertTrue(os.path.exists(binned), "the file is in the bin")
            with open(binned) as f:
                self.assertEqual(f.read(), "keep me", "and it is the file itself")

            self.assertFalse(os.path.exists(
                os.path.join(home, ".Trash", "chatter.tmp")))

    def test_a_bin_that_cannot_be_reached_deletes_the_ordinary_way(self):
        from runtime import recycle
        self.assertFalse(recycle.to_bin("/nowhere/at/all/x.txt"))

    def test_allowed_is_open_until_installed(self):
        self.assertFalse(diskguard._state["installed"])
        self.assertTrue(diskguard.allowed("/anything", write=True))

    def test_the_message_names_the_path_and_the_rule(self):
        m = diskguard.message("/tmp/x")
        self.assertTrue(m.startswith(diskguard.BLOCK_PREFIX))
        self.assertIn("/tmp/x", m)
        self.assertIn("allowed folders", m)

class TheOneListTest(unittest.TestCase):
    def _workflow(self, **kw):
        wf = {"id": "p_roots", "name": "W", "nodes": [], "edges": [], "variables": []}
        wf.update(kw)
        return wf

    def test_a_grant_a_declaration_and_a_setting_all_land(self):
        from runtime import sandbox
        from storage import environments
        picked = os.path.join(tempfile.gettempdir(), "cg_picked")
        folder_type = next((t for t in ("folder", "directory", "path")
                            if environments.setting_type(t) == "folder"), None)
        self.assertIsNotNone(folder_type, "no setting type resolves to a folder")
        wf = self._workflow(path_allowlist=["/granted/on/a/card"],
                            variables=[{"name": "outdir", "type": folder_type,
                                        "value": picked}])
        roots = sandbox.workflow_roots(wf, ["/declared/on/the/step"])
        self.assertIn("/granted/on/a/card", roots)
        self.assertIn("/declared/on/the/step", roots)
        self.assertIn(os.path.abspath(picked), roots)

    def test_a_relative_or_blank_folder_is_ignored(self):
        from runtime import sandbox
        wf = self._workflow(path_allowlist=["", "   ", "relative/dir", "./also/relative"])
        self.assertEqual(sandbox.workflow_roots(wf), [])

    def test_a_home_folder_is_expanded_the_one_way(self):
        from runtime import sandbox
        wanted = os.path.abspath(os.path.expanduser("~/reports"))
        self.assertEqual(sandbox.workflow_roots(self._workflow(), ["~/reports"]), [wanted])

    def test_the_build_and_the_run_both_go_through_the_one_function(self):
        cell = (ROOT / "backend" / "agent" / "cell_gates.py").read_text()
        run = (ROOT / "backend" / "runtime" / "executor.py").read_text()
        self.assertIn("sandbox.workflow_roots(", cell)
        self.assertIn("sandbox.workflow_roots(", run)

        self.assertIn('(node.get("config") or {}).get("paths")', run)
        self.assertIn("os.path.abspath(os.path.expanduser(str(x)))", run)

    def test_the_card_names_the_folder_the_guard_actually_refused(self):
        from agent import steps
        blocked = os.path.join(tempfile.gettempdir(), "cg_reports", "q3.csv")
        msg = "PermissionError: " + diskguard.message(blocked)
        self.assertEqual(steps.path_block_path(msg), blocked)
        self.assertEqual(steps.folder_of(blocked), os.path.dirname(blocked))

    def test_a_granted_folder_is_what_the_guard_then_allows(self):
        from agent import steps
        from runtime import sandbox
        blocked = os.path.join(tempfile.gettempdir(), "cg_reports", "q3.csv")
        folder = steps.folder_of(steps.path_block_path(diskguard.message(blocked)))
        wf = self._workflow(path_allowlist=[folder])
        self.assertIn(folder, sandbox.workflow_roots(wf))
