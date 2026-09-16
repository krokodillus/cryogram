# Tests: strict egress: the allowlist default, the global + per-workflow merge into the sandbox, the reasoned egress-blocked halt (env-style: no ticket fuel, resumable), and plan-level domain validation
from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

import config
from runtime import executor
from agent import node_tools
from runtime import sandbox
from storage import settings

class DefaultsTest(unittest.TestCase):
    def test_default_mode_is_allowlist(self):
        self.assertEqual(settings.DEFAULTS["sandbox"]["egress_mode"], "allowlist")

class MergeTest(unittest.TestCase):
    def test_workflow_allowlist_merges_with_global(self):
        seen = {}
        def fake(code, entry, inputs, secrets, egress_mode="",
                 egress_allowlist=None, **kw):
            seen.update({"mode": egress_mode, "allow": egress_allowlist})
            return {"ok": 1}
        with mock.patch.object(sandbox, "_run_subprocess", fake), \
             mock.patch.object(sandbox.settings, "get", lambda: {
                 "sandbox": {"mode": "subprocess", "egress_mode": "allowlist",
                             "egress_allowlist": ["global.example"]}}):
            sandbox.run("def f(): pass", "f", {}, {},
                        extra_allowlist=["api.crm.example", "global.example"])
        self.assertEqual(seen["mode"], "allowlist")
        self.assertEqual(seen["allow"], ["api.crm.example", "global.example"])

class BlockedVerdictTest(unittest.TestCase):
    def test_egress_block_halts_env_style(self):
        p = _workflow([{"id": "n_web", "name": "fetch", "type": "code",
                       "config": {"code": "def f(x):\n    return x\n"},
                       "inputs": [{"name": "x", "type": "number"}],
                       "outputs": [{"name": "x", "type": "number"}], "tests": []}],
                     pid="p_egress")
        def blocked(*a, **k):
            raise sandbox.NodeError(
                "PermissionError", "egress blocked: 'evil.example' is not on the allowlist")
        with mock.patch.object(executor.sandbox, "run", blocked):
            res = executor.run_workflow(p, {"x": 1})
        self.assertEqual(res["status"], "halted")
        self.assertEqual(res["reason"], "egress-blocked")
        self.assertNotIn("case_id", res)
        line = res["verdict"]["environment"][0]
        self.assertIn("evil.example", line)
        self.assertIn("allow the domain", line)

class RawSocketGuardTest(unittest.TestCase):
    def test_raw_socket_connect_hits_the_wall(self):
        code = ("import socket\n"
                "s = socket.socket()\n"
                "try:\n"
                "    s.connect(('203.0.113.9', 443))\n"
                "    write_output('blocked', False)\n"
                "except PermissionError:\n"
                "    write_output('blocked', True)\n"
                "finally:\n"
                "    s.close()\n")
        res = sandbox.run(code, None, {}, {})
        self.assertTrue(res.get("blocked"), res)

    def test_loopback_still_reachable(self):
        code = ("import socket\n"
                "s = socket.socket()\n"
                "try:\n"
                "    s.connect(('127.0.0.1', 1))\n"
                "    write_output('kind', 'connected')\n"
                "except PermissionError:\n"
                "    write_output('kind', 'blocked')\n"
                "except OSError:\n"
                "    write_output('kind', 'refused')\n"
                "finally:\n"
                "    s.close()\n")
        res = sandbox.run(code, None, {}, {})
        self.assertIn(res.get("kind"), ("refused", "connected"), res)

class HeartbeatWatchdogTest(unittest.TestCase):
    def test_heartbeat_keeps_a_long_step_alive(self):
        code = ("import time\n"
                "for i in range(4):\n"
                "    time.sleep(0.5)\n"
                "    heartbeat(f'lap {i}')\n"
                "write_output('laps', 4)\n")
        res = sandbox.run(code, None, {}, {}, timeout=1)
        self.assertEqual(res.get("laps"), 4)

    def test_silent_step_still_dies_at_the_limit(self):
        code = "import time\ntime.sleep(5)\nwrite_output('done', True)\n"
        t0 = time.time()
        with self.assertRaises(sandbox.NodeError) as cm:
            sandbox.run(code, None, {}, {}, timeout=1)
        self.assertLess(time.time() - t0, 4)
        self.assertEqual(cm.exception.kind, "NoProgress")
        self.assertIn("heartbeat", str(cm.exception))
        self.assertEqual(cm.exception.detail.get("timeout_seconds"), 1)

    def test_a_silent_step_reports_what_it_had_written_before_the_kill(self):
        code = ("import time\n"
                "write_output('rows', [1, 2])\n"
                "time.sleep(5)\n"
                "write_output('done', True)\n")
        t0 = time.time()
        with self.assertRaises(sandbox.NodeError) as cm:
            sandbox.run(code, None, {}, {}, timeout=1)
        self.assertLess(time.time() - t0, 6)
        self.assertEqual(cm.exception.kind, "NoProgress")
        self.assertEqual(cm.exception.detail.get("written"), {"rows": [1, 2]})

    def test_clamp_timeout_bounds(self):
        self.assertIsNone(sandbox.clamp_timeout(None))
        self.assertIsNone(sandbox.clamp_timeout("soon"))
        self.assertIsNone(sandbox.clamp_timeout(0))
        self.assertEqual(sandbox.clamp_timeout(5), sandbox.MIN_TIMEOUT)
        self.assertEqual(sandbox.clamp_timeout(600), 600)
        self.assertEqual(sandbox.clamp_timeout(999999), 999999)

class CertInjectionTest(unittest.TestCase):
    def test_cert_bundle_injected_but_never_overrides(self):
        try:
            import certifi
        except ImportError:
            self.skipTest("certifi not installed in this env")
        env = {}
        sandbox._inject_cert_bundle(env)
        self.assertEqual(env["SSL_CERT_FILE"], certifi.where())
        chosen = {"SSL_CERT_FILE": "/my/own.pem"}
        sandbox._inject_cert_bundle(chosen)
        self.assertEqual(chosen["SSL_CERT_FILE"], "/my/own.pem")

class PlanDomainsTest(unittest.TestCase):
    def test_plan_domains_validated(self):
        p = _workflow(pid="p_egress2")
        node_tools.tool_save_intent(p, "Purpose.")
        bad = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "send", "type": "connector", "external_impact": "posts",
             "domains": ["https://evil.example/path"],
             "outputs": [{"name": "ok", "type": "boolean"}]}]})

        self.assertTrue(bad["ok"], bad)
        self.assertTrue(any("bare hostname" in g for g in bad["design_gaps"]))
        self.assertIn("design gaps",
                      node_tools.tool_build_workflow(p).get("error", ""))
        good = node_tools.tool_save_plan(p, {"summary": "Do.", "nodes": [
            {"name": "send", "type": "connector", "external_impact": "posts",
             "domains": ["api.crm.example"],
             "outputs": [{"name": "ok", "type": "boolean"}]}]})
        self.assertTrue(good["ok"], good)

if __name__ == "__main__":
    unittest.main()

class CellEgressTest(unittest.TestCase):
    def test_plan_domains_reach_the_cell_allowlist(self):
        from agent import node_tools
        from unittest.mock import patch
        p = {"id": "p_cellegress", "nodes": [], "edges": [], "variables": [],
             "chat": [], "samples": [], "plan_approved_ts": 1.0,
             "plan": {"status": "draft", "nodes": [
                 {"name": "fetch", "type": "connector", "read_only": True,
                  "domains": ["api.example.com"]}]}}
        from storage import store as _st
        _st.save(p)
        captured = {}
        def fake_run(code, entry, inputs, secrets, **kw):
            captured["allow"] = kw.get("extra_allowlist")
            return {"ok": True}
        from runtime import sandbox
        with patch.object(sandbox, "run", fake_run):
            node_tools.tool_run_cell(p, "fetch", "write_output('ok', True)", {})
        self.assertIn("api.example.com", captured["allow"])

    def test_blocked_cell_names_the_host_to_declare(self):
        from agent import node_tools
        from unittest.mock import patch
        p = {"id": "p_cellblock", "nodes": [], "edges": [], "variables": [],
             "chat": [], "samples": [], "plan_approved_ts": 1.0,
             "plan": {"status": "draft", "nodes": [{"name": "fetch", "type": "connector",
                                                  "read_only": True}]}}
        from storage import store as _st
        _st.save(p)
        def boom(*a, **k):
            raise RuntimeError("egress blocked: ('www.nrk.no', 443) is not on the allowlist")
        from runtime import sandbox
        with patch.object(sandbox, "run", boom):
            r = node_tools.tool_run_cell(p, "fetch", "import urllib.request", {})
        self.assertFalse(r["ok"])
        self.assertIn("www.nrk.no", r["note"])
        self.assertIn("domains", r["note"])

class EgressApprovalSeamTest(unittest.TestCase):
    def test_blocked_host_is_parseable(self):
        from agent import node_tools
        err = ("NodeError: <urlopen error egress blocked: ('www.nrk.no', 443) is not on the allowlist>")
        self.assertEqual(node_tools.egress_block_domain(err), "www.nrk.no")
        self.assertIsNone(node_tools.egress_block_domain("some other error"))
        self.assertIsNone(node_tools.egress_block_domain(None))

class TrailingDotBypassTest(unittest.TestCase):
    def test_trailing_dot_hostname_is_blocked(self):
        from runtime import _sandbox_runner
        self.assertNotIn("", _sandbox_runner._LOOPBACK)
        code = ("import socket\n"
                "try:\n"
                "    socket.getaddrinfo('203.0.113.7.', 443)\n"
                "    write_output('blocked', False)\n"
                "except PermissionError:\n"
                "    write_output('blocked', True)\n"
                "except OSError:\n"
                "    write_output('blocked', True)\n")
        res = sandbox.run(code, None, {}, {})
        self.assertTrue(res.get("blocked"), res)

class ChildEnvAllowlistTest(unittest.TestCase):
    def test_canary_never_reaches_the_child(self):
        import os
        os.environ["CRYO_CANARY_SECRET"] = "leak-me"
        try:
            code = ("import os\n"
                    "write_output('canary', os.environ.get('CRYO_CANARY_SECRET'))\n"
                    "write_output('path_ok', bool(os.environ.get('PATH')))\n")
            res = sandbox.run(code, None, {}, {})
        finally:
            del os.environ["CRYO_CANARY_SECRET"]
        self.assertIsNone(res.get("canary"), res)
        self.assertTrue(res.get("path_ok"), res)

    def test_declared_secrets_still_cross(self):
        code = ("v = get_secret('MY_TOKEN')\n"
                "write_output('got_it', v == 'tok-123')\n"
                "write_output('v', v)\n")
        res = sandbox.run(code, None, {}, {"MY_TOKEN": "tok-123"})
        self.assertTrue(res.get("got_it"), res)
        self.assertEqual(res.get("v"), "***")

class CheckpointResumeTest(unittest.TestCase):
    def setUp(self):
        import config
        self.path = config.checkpoint_file("test_ckpt_scope")
        if self.path.exists():
            self.path.unlink()

    def tearDown(self):
        if self.path.exists():
            self.path.unlink()

    def test_items_survive_a_crash_and_the_rerun_skips_them(self):
        code = ("done = checkpoints()\n"
                "sent = []\n"
                "for i in ['a', 'b', 'c', 'd']:\n"
                "    if i in done:\n"
                "        continue\n"
                "    if i == 'd':\n"
                "        raise RuntimeError('429 too many requests')\n"
                "    checkpoint(i, {'ok': True})\n"
                "    sent.append(i)\n"
                "write_output('sent', sent)\n")
        with self.assertRaises(Exception):
            sandbox.run(code, None, {}, {}, checkpoint_file=str(self.path))

        import json
        self.assertEqual(sorted(json.loads(self.path.read_text())),
                         ["a", "b", "c"])

        code_ok = code.replace("if i == 'd':\n"
                               "        raise RuntimeError('429 too many requests')\n",
                               "")
        res = sandbox.run(code_ok, None, {}, {},
                          checkpoint_file=str(self.path))
        self.assertEqual(res.get("sent"), ["d"])

    def test_no_ledger_means_no_op(self):
        code = ("checkpoint('x', 1)\n"
                "write_output('seen', checkpoints())\n")
        res = sandbox.run(code, None, {}, {})
        self.assertEqual(res.get("seen"), {})

class ThrottleClassifierTest(unittest.TestCase):
    def test_service_pushback_is_recognised_bugs_are_not(self):
        from step_types import connector
        for text in ("HTTP 429 Too Many Requests", "Whoa there, slow down",
                     "unusual activity detected", "please complete the captcha",
                     "rate limit exceeded"):
            self.assertTrue(connector.SLOW_DOWN_WORDS.search(text), text)
        for text in ("NameError: name 'x' is not defined",
                     "KeyError: 'title'", "404 Not Found"):
            self.assertFalse(connector.SLOW_DOWN_WORDS.search(text), text)

    def test_tokens_buried_in_an_embedded_payload_do_not_classify(self):
        from runtime import executor, run_state
        run_state.start("p_bur", "run_bur", {})
        conn = {"type": "connector", "name": "Fetch", "read_only": True}
        buried = "[Errno 63] File name too long: '" + "x" * 700 + " captcha 429'"
        self.assertIsNone(executor._service_halt("run_bur", "n1", conn, buried))
        self.assertIsNone(executor._service_halt(
            "run_bur", "n1", conn, "ValueError: " + "y" * 700 + " connection reset"))

        self.assertEqual(executor._service_halt(
            "run_bur", "n1", conn, "HTTP Error 429")["reason"], "rate-limited")
        self.assertEqual(executor._service_halt(
            "run_bur", "n1", conn, "The read operation timed out")["reason"],
            "service-unavailable")
        run_state.finish("run_bur")

    def test_service_halts_are_connector_only(self):
        from runtime import executor, run_state
        run_state.start("p_thr", "run_thr", {})
        code_node = {"type": "code", "name": "Parse the panel"}
        conn_node = {"type": "connector", "name": "Fetch", "read_only": True}
        err = "HTTP 429 Too Many Requests"
        self.assertIsNone(
            executor._service_halt("run_thr", "n1", code_node, err))
        got = executor._service_halt("run_thr", "n1", conn_node, err)
        self.assertEqual(got["reason"], "rate-limited")
        terr = "TimeoutError: The read operation timed out"
        self.assertIsNone(
            executor._service_halt("run_thr", "n1", code_node, terr))
        got = executor._service_halt("run_thr", "n1", conn_node, terr)
        self.assertEqual(got["reason"], "service-unavailable")
        run_state.finish("run_thr")

    def test_a_writes_ambiguous_error_is_never_a_safe_pause(self):
        from runtime import executor, run_state
        from step_types import connector
        run_state.start("p_thr2", "run_thr2", {})
        writer = {"type": "connector", "name": "Send", "read_only": False}
        both = "HTTP 429 rate limit - the read operation timed out"
        self.assertIsNone(connector.read_slow_down(writer, both, ""))
        got = executor._service_halt("run_thr2", "n1", writer, both)
        self.assertEqual(got["reason"], "send-unverified")

        got2 = executor._service_halt("run_thr2", "n1", writer,
                                      "HTTP 429 Too Many Requests")
        self.assertEqual(got2["reason"], "rate-limited")
        run_state.finish("run_thr2")

class WindowLockPauseTest(unittest.TestCase):
    def test_the_lock_error_pauses_resumably_connector_only(self):
        from runtime import executor, run_state
        run_state.start("p_wl", "run_wl", {})
        conn = {"type": "connector", "name": "Check login",
                "read_only": True}
        err = ("BrowserType.launch_persistent_context: Failed to create a ProcessSingleton for your profile directory...")
        got = executor._window_lock_halt("run_wl", "n1", conn, err)
        self.assertEqual(got["reason"], "browser-window-open")
        self.assertIn("Close that window",
                      got["verdict"]["environment"][0])
        code_node = {"type": "code", "name": "Parse"}
        self.assertIsNone(
            executor._window_lock_halt("run_wl", "n1", code_node, err))
        self.assertIsNone(
            executor._window_lock_halt("run_wl", "n1", conn,
                                       "KeyError: 'title'"))
        run_state.finish("run_wl")

class LatestHaltIsAnAskTest(unittest.TestCase):
    def test_only_an_asking_run_wins(self):
        from runtime import run_state
        run_state.start("p_lh", "run_lh1", {})
        run_state.halt("run_lh1", "n1", "missing-value")
        run_state.start("p_lh", "run_lh2", {})
        run_state.halt("run_lh2", "n2", "stopped-by-user")
        run_state.start("p_lh", "run_lh3", {})
        run_state.halt("run_lh3", "n3", "service-unavailable")
        run_state.start("p_lh", "run_lh4", {})
        run_state.halt("run_lh4", "n4", "browser-window-open")
        got = run_state.latest_halt("p_lh")
        self.assertEqual(got.get("reason"), "missing-value")
        self.assertTrue(run_state.load("run_lh1").get("waiting_on_you"))
        self.assertFalse(run_state.load("run_lh3").get("waiting_on_you"))
        for r in ("run_lh1", "run_lh2", "run_lh3", "run_lh4"):
            run_state.finish(r)

class NoProgressIsAWorkflowProblemTest(unittest.TestCase):
    def test_no_progress_on_a_connector_is_an_issue_with_evidence(self):
        from runtime import env_checks, sandbox
        node = {"id": "n_np", "name": "Read the feed", "type": "connector",
                "read_only": True, "external_impact": "reads a feed",
                "config": {"code": "write_output('rows', [])"},
                "inputs": [], "outputs": [{"name": "rows", "type": "any"}],
                "tests": []}
        p = _workflow([node], pid="p_noprog")
        err = sandbox.NodeError(
            "NoProgress", "no sign of progress for 120 seconds",
            detail={"timeout_seconds": 120,
                    "saw": {"traffic": [{"method": "GET", "host": "x.test",
                                         "path": "/feed", "status": 200}]},
                    "written": {"rows": [{"id": 1}]}})
        with mock.patch.object(sandbox, "run", side_effect=err), \
             mock.patch.object(env_checks, "check_all_nodes", return_value=[]), \
             mock.patch.object(env_checks, "check_node", return_value=[]):
            res = executor.run_workflow(p, {})
        self.assertEqual((res["status"], res["reason"]), ("halted", "no-progress"), res)
        self.assertIn("case_id", res)
        line = res["verdict"]["no_progress"][0]
        self.assertIn('The step "Read the feed"', line)
        self.assertIn("120 seconds", line)
        self.assertEqual(res["saw"]["traffic"][0]["host"], "x.test")
        self.assertIn("rows", res["saw"]["written"])

class PathInputMaterialisesTest(unittest.TestCase):
    def test_inline_value_becomes_a_real_file(self):
        code = ("p = read_input('doc', path=True)\n"
                "with open(p, encoding='utf-8') as f:\n"
                "    write_output('got', f.read())\n")
        res = sandbox.run(code, None, {"doc": "<!DOCTYPE html>hello"}, {})
        self.assertEqual(res.get("got"), "<!DOCTYPE html>hello")

    def test_an_actual_path_value_passes_through(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                         delete=False) as f:
            f.write("real file")
            real = f.name
        try:
            code = ("p = read_input('doc', path=True)\n"
                    "write_output('same', p)\n")
            res = sandbox.run(code, None, {"doc": real}, {})
            self.assertEqual(res.get("same"), real)
        finally:
            os.unlink(real)

class BrowserErrorsAreWorkflowProblemsTest(unittest.TestCase):
    def test_browser_timeout_is_not_a_service_pause(self):
        from runtime import executor
        browser = {"id": "n_b", "name": "Read the thread", "type": "browser",
                   "read_only": True}
        err = RuntimeError("Timeout 30000ms exceeded while waiting for selector")
        self.assertIsNone(executor._service_halt("run_be1", "n_b", browser, err))

    def test_a_connector_pause_quotes_the_step_and_the_service(self):
        from runtime import executor, run_state
        conn = {"id": "n_c", "name": "Fetch the feed", "type": "connector",
                "read_only": True}
        run_state.start("p_be2", "run_be2", {})
        try:
            got = executor._service_halt("run_be2", "n_c", conn,
                                           RuntimeError("The read operation timed out"))
            self.assertEqual(got["reason"], "service-unavailable")
            line = got["verdict"]["environment"][0]
            self.assertIn('The step "Fetch the feed"', line)
            self.assertIn("The service said: The read operation timed out", line)
        finally:
            run_state.finish("run_be2")
