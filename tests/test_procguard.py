# Tests: step code may not start a process, and the harness's own browser launch still may
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from tests import _bootstrap

import step_types
from runtime import procguard

ROOT = Path(__file__).resolve().parent.parent

_CHILD = textwrap.dedent('''
    import json, os, subprocess, sys
    sys.path.insert(0, sys.argv[1])
    from runtime import diskguard, procguard
    procguard.install()
    out = {}
    def attempt(name, fn):
        try:
            fn(); out[name] = "ok"
        except PermissionError as e:
            out[name] = ("blocked" if str(e).startswith(procguard.BLOCK_PREFIX)
                         else "other: " + str(e))
        except Exception as e:  # noqa: BLE001 - reported, not hidden
            out[name] = "error: " + type(e).__name__
    attempt("subprocess_run", lambda: subprocess.run([sys.executable, "-c", "pass"], check=True))
    attempt("popen", lambda: subprocess.Popen([sys.executable, "-c", "pass"]))
    attempt("check_output", lambda: subprocess.check_output([sys.executable, "-c", "pass"]))
    attempt("os_system", lambda: os.system("true"))
    attempt("os_popen", lambda: os.popen("true").read())
    attempt("os_execv", lambda: os.execv(sys.executable, [sys.executable, "-c", "pass"]))
    def in_harness():
        with diskguard.harness():
            subprocess.run([sys.executable, "-c", "pass"], check=True)
    attempt("the_harness_may_launch", in_harness)
    attempt("installing_twice_is_a_noop", lambda: procguard.install())
    print(json.dumps(out))
''')

class ProcGuardTest(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "the shell probes differ on Windows")
    def test_step_code_cannot_start_a_program_but_the_harness_can(self):
        r = subprocess.run([sys.executable, "-c", _CHILD, str(ROOT / "backend")],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out, {
            "subprocess_run": "blocked",
            "popen": "blocked",
            "check_output": "blocked",
            "os_system": "blocked",
            "os_popen": "blocked",
            "os_execv": "blocked",

            "the_harness_may_launch": "ok",
            "installing_twice_is_a_noop": "ok",
        })

    def test_the_message_names_the_program_and_the_way_round_it(self):
        m = procguard.message("/usr/bin/ffmpeg")
        self.assertTrue(m.startswith(procguard.BLOCK_PREFIX))
        self.assertIn("ffmpeg", m)
        self.assertIn("declare the package", m)

    def test_only_a_browser_step_may_start_one_at_all(self):
        self.assertTrue(step_types.may("browser", "process"))
        for t in ("code", "connector", "ai", "user-input"):
            self.assertFalse(step_types.may(t, "process"), t)

class TheNetworkFollowsTheTypeTest(unittest.TestCase):
    def _allow(self, network):
        from unittest import mock
        from runtime import sandbox
        seen = {}

        def fake(code, entry, inputs, secrets, **kw):
            seen["allow"] = kw.get("egress_allowlist")
            return {}
        with mock.patch.object(sandbox, "_run_subprocess", fake):
            sandbox.run("x", None, {}, {}, extra_allowlist=["api.example.com"],
                        network=network)
        return seen["allow"]

    def test_a_type_with_the_network_keeps_the_workflows_domains(self):
        self.assertIn("api.example.com", self._allow(True))

    def test_a_type_without_it_runs_with_an_empty_allowlist(self):
        self.assertEqual(self._allow(False), [])

    def test_which_types_have_it(self):
        self.assertTrue(step_types.may("connector", "network"))

        for t in ("code", "browser", "ai", "user-input"):
            self.assertFalse(step_types.may(t, "network"), t)

class TheWindowsRecordingIsOfferedAndKeptTest(unittest.TestCase):
    def test_a_failed_step_keeps_the_page_and_the_traffic(self):
        from unittest import mock
        from runtime import _sandbox_runner, capability
        with mock.patch.object(capability, "window_opened", return_value=True), \
             mock.patch.object(_sandbox_runner, "_stored_captures",
                               return_value={"har": "blob:har", "snapshot": "blob:page"}):
            payload = _sandbox_runner._failure_payload("it broke", "ValueError")
        self.assertTrue(payload["window_opened"])
        self.assertEqual(payload["$capture"]["har"], "blob:har")

    def test_a_step_that_opened_no_window_keeps_nothing(self):
        from unittest import mock
        from runtime import _sandbox_runner, capability
        called = []
        with mock.patch.object(capability, "window_opened", return_value=False), \
             mock.patch.object(_sandbox_runner, "_stored_captures",
                               side_effect=lambda: called.append(1) or {}):
            payload = _sandbox_runner._failure_payload("it broke", "ValueError")
        self.assertNotIn("$capture", payload)
        self.assertEqual(called, [], "nothing to keep when no window opened")

    def test_the_recording_survives_the_trip_to_the_parent(self):
        src = (ROOT / "backend" / "runtime" / "sandbox.py").read_text()
        i = src.index('raise NodeError(res.get("error_kind")')
        self.assertIn("$capture", src[i:i + 700])

if __name__ == "__main__":
    unittest.main()
