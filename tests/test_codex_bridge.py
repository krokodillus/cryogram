# Tests: the Codex builder engine's offline seams: the bridge registry (tokens, per-token locks, the shared turn-over rail, envelopes), the shim over real pipes against a stub bridge, transport selection/dispatch, the node-exclusion belt for the codex auths, the failure table, usage mapping, and the intake hardening
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import pathlib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from agent import actions, codex_bridge, codex_engine, loop, transport
from agent import turnstate

class BridgeTest(unittest.TestCase):
    def test_lifecycle_tools_execute_release(self):
        p = _workflow([], pid="p_cbx1")
        token = codex_bridge.register(p, lambda ev: None)
        self.assertEqual(codex_bridge.status(token), "live")

        from agent import codex_engine, actions
        tools = codex_engine.dynamic_tools()
        names = {t["name"] for t in tools}
        self.assertIn("run_cell", names)
        self.assertIn("ask_user", names)
        self.assertTrue(all(t["type"] == "function" and t["deferLoading"] is False
                            and "inputSchema" in t for t in tools))
        self.assertTrue(codex_engine.initialize_params()["capabilities"]["experimentalApi"])

        out = codex_bridge.execute(token, "save_intent", {"summary": "Do x."})
        env = json.loads(out["envelope_text"])
        self.assertTrue(env.get("ok"), env)
        codex_bridge.release(token)
        self.assertEqual(codex_bridge.status(token), "released")
        self.assertEqual(codex_bridge.status("nope"), "unknown")

        out2 = codex_bridge.execute(token, "save_intent", {"summary": "x"})
        self.assertIn("turn is over", out2["envelope_text"])

    def test_a_tool_call_request_is_answered_in_process(self):
        import threading, time
        from agent import codex_engine
        p = _workflow([], pid="p_cbx2")
        token = codex_bridge.register(p, lambda ev: None)
        app = codex_engine._AppServer.__new__(codex_engine._AppServer)
        app.pid, app.token = p["id"], token
        sent, got = [], threading.Event()
        app._send = lambda msg: (sent.append(msg), got.set())
        app._serve_request({"id": 7, "method": "item/tool/call",
                            "params": {"threadId": "t", "turnId": "u", "callId": "c",
                                       "tool": "save_intent", "arguments": {"summary": "Do x."}}})
        self.assertTrue(got.wait(10))
        reply = sent[0]
        self.assertEqual(reply["id"], 7)
        self.assertTrue(reply["result"]["success"])
        items = reply["result"]["contentItems"]
        self.assertEqual(items[0]["type"], "inputText")
        self.assertTrue(json.loads(items[0]["text"]).get("ok"))
        codex_bridge.release(token)

    def test_busy_lock_refuses_a_second_call(self):
        p = _workflow([], pid="p_cbx3")
        token = codex_bridge.register(p, lambda ev: None)
        e = codex_bridge.entry(token)
        e["lock"].acquire()
        e["busy_tool"] = "run_cell"
        try:
            out = codex_bridge.execute(token, "save_intent", {"summary": "x"})
            self.assertIn("still finishing", out["envelope_text"])
            self.assertIn("run_cell", out["envelope_text"])
        finally:
            e["lock"].release()

    def test_turn_over_rail_is_shared(self):
        p = _workflow([], pid="p_cbx4")
        turnstate.of(p).ask_open = True
        token = codex_bridge.register(p, lambda ev: None)
        out = codex_bridge.execute(token, "run_cell",
                                   {"name": "x", "code": "pass"})
        env = json.loads(out["envelope_text"])
        self.assertFalse(env.get("ok"))
        self.assertIn("OVER", env.get("error", ""))

    def test_terminal_sets_the_event_and_on_tool_fires(self):
        p = _workflow([], pid="p_cbx5")
        gaps = []
        token = codex_bridge.register(p, lambda ev: None,
                                      on_tool=lambda: gaps.append(1))
        codex_bridge.execute(token, "save_intent", {"summary": "Do."})
        self.assertEqual(len(gaps), 1)
        e = codex_bridge.entry(token)
        self.assertFalse(e["terminal"].is_set())

        out = codex_bridge.execute(token, "ask_user",
                                   {"question": "Are you ready to start?",
                                    "options": ["Yes", "Not yet"]})
        self.assertTrue(json.loads(out["envelope_text"]).get("ok"), out)
        self.assertTrue(e["terminal"].is_set())

    def test_image_split(self):
        env = {"action": "read_sample", "ok": True,
               "data": {"note": "n", "image": {"data": "AAA",
                                               "media_type": "image/png"}}}
        slim = codex_bridge._strip_image(env)
        self.assertNotIn("image", slim["data"])
        self.assertEqual(env["data"]["image"]["data"], "AAA")

class TransportDispatchTest(unittest.TestCase):
    def _settings(self, auth, key_set=True):
        return {"master_ai": {"model": "codex-default"},
                "providers": [{"id": "builder", "use": "builder",
                               "adapter": "codex", "auth": auth,
                               "key_name": "CODEX_API_KEY" if auth ==
                               "codex-api-key" else "",
                               "models": [{"name": "codex-default"}]}]}

    def test_codex_subscription_touches_no_secret(self):
        from storage import secrets_store, settings
        touched = []
        with patch.object(settings, "get",
                          return_value=self._settings("codex-subscription")), \
             patch.object(secrets_store, "get_secret",
                          side_effect=lambda *a: touched.append(a) or ""):
            tr = transport.for_settings()
        self.assertIsInstance(tr, transport.CodexTransport)
        self.assertEqual(tr.auth, "codex-subscription")
        self.assertEqual(tr.credential, "")
        self.assertEqual(touched, [])

    def test_codex_api_key_reads_our_store(self):
        from storage import secrets_store, settings
        with patch.object(settings, "get",
                          return_value=self._settings("codex-api-key")), \
             patch.object(secrets_store, "get_secret", return_value="sk-x"):
            tr = transport.for_settings()
        self.assertIsInstance(tr, transport.CodexTransport)
        self.assertEqual(tr.credential, "sk-x")

    def test_run_turn_dispatches_codex_transport_to_the_engine(self):
        p = _workflow([], pid="p_cbx6")
        tr = transport.CodexTransport("codex-default")
        reply = {"content": "hi", "kind": "text"}
        with patch.object(codex_engine, "run_turn_codex",
                          return_value=reply) as run:
            got = loop.run_turn(p, "msg", None, tr=tr)
        self.assertIs(got, reply)
        run.assert_called_once()

    def test_sdk_env_raises_on_codex_auths(self):
        for auth in ("codex-subscription", "codex-api-key"):
            tr = transport.CodexTransport("m", auth=auth)
            with self.assertRaises(transport.TransportError):
                loop._sdk_env(tr)

class NodeExclusionTest(unittest.TestCase):
    def test_node_capable_refuses_codex_auths(self):
        import providers
        for auth in ("claude-subscription", "codex-subscription",
                     "codex-api-key"):
            p = {"id": "x", "use": "workflow", "enabled": True, "auth": auth,
                 "models": [{"name": "m"}]}
            self.assertFalse(providers._node_capable(p), auth)

    def test_node_model_state_calls_a_codex_only_model_disabled(self):
        import providers
        from storage import settings
        reg = [{"id": "w", "use": "workflow", "enabled": True,
                "auth": "codex-api-key", "key_name": "K",
                "models": [{"name": "codex-default"}]}]
        with patch.object(settings, "get",
                          return_value={"providers": reg, "master_ai": {}}):
            state, _p = providers.node_model_state("codex-default")
        self.assertEqual(state, "missing")

class EngineSeamsTest(unittest.TestCase):
    def test_usage_maps_to_the_sdk_shape_same_total(self):
        m = codex_engine._map_usage({"inputTokens": 1000,
                                     "cachedInputTokens": 700,
                                     "outputTokens": 42})

        total_in = m["input_tokens"] + m["cache_read_input_tokens"]
        self.assertEqual(total_in, 1000)
        self.assertEqual(m["output_tokens"], 42)
        self.assertEqual(m["codex_raw"]["inputTokens"], 1000)

    def test_failure_table_classifies(self):
        e = codex_engine._fail("p_cbx7", "stream error: 401 Unauthorized")
        self.assertIn("signed in", str(e))
        e = codex_engine._fail("p_cbx7", "You've hit your usage limit")
        self.assertIn("usage limit", str(e))
        e = codex_engine._fail("p_cbx7", "something odd")
        self.assertIn("Codex builder failed", str(e))

    def test_check_login_routes_on_exit_codes(self):
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="codexbin_"))
        fake = d / "codex"
        fake.write_text("#!/bin/sh\n"
                        'if [ "$1" = "--version" ]; then echo codex-cli 9.9; exit 0; fi\n'
                        'if [ "$1" = "login" ]; then exit ${CODEX_FAKE_LOGIN:-0}; fi\n'
                        "exit 1\n")
        fake.chmod(0o755)
        with patch.dict(os.environ, {"CRYOGRAM_CODEX_BIN": str(fake),
                                     "CODEX_FAKE_LOGIN": "0"}):
            r = codex_engine.check_login()
            self.assertTrue(r["ok"])
            self.assertIn("9.9", r["version"])
        with patch.dict(os.environ, {"CRYOGRAM_CODEX_BIN": str(fake),
                                     "CODEX_FAKE_LOGIN": "1"}):
            r = codex_engine.check_login()
            self.assertFalse(r["ok"])
            self.assertIn("codex login", r["message"])
        with patch.dict(os.environ,
                        {"CRYOGRAM_CODEX_BIN": str(d / "missing")}):
            r = codex_engine.check_login()
            self.assertFalse(r["ok"])
            self.assertFalse(r["installed"])

        old = d / "codex_old"
        old.write_text("#!/bin/sh\n"
                       'if [ "$1" = "--version" ]; then echo codex-cli 0.88.0; exit 0; fi\n'
                       "exit 0\n")
        old.chmod(0o755)
        with patch.dict(os.environ, {"CRYOGRAM_CODEX_BIN": str(old)}):
            r = codex_engine.check_login()
            self.assertFalse(r["ok"])
            self.assertTrue(r["too_old"])
            self.assertIn("needs 0.92.0 or newer", r["message"])

    def test_lockdown_file_disables_quoted_keys(self):
        import tempfile
        cwd = Path(tempfile.mkdtemp(prefix="codexcwd_"))
        codex_engine.write_workflow_lockdown(cwd)
        text = (cwd / ".codex" / "config.toml").read_text()
        self.assertIn('[mcp_servers."computer-use"]', text)
        for pid in codex_engine.LOCKDOWN_PLUGINS:
            self.assertIn(f'[plugins."{pid}"]', text)
        self.assertNotIn("BRIDGE_TOKEN", text)

class CodexHomeIsolationTest(unittest.TestCase):
    def test_chatgpt_home_links_the_users_sign_in_only(self):
        import tempfile
        from unittest import mock
        import config
        theirs = Path(tempfile.mkdtemp(prefix="theircodex_"))
        (theirs / "auth.json").write_text("{}")
        (theirs / "config.toml").write_text('notify = ["evil"]\n')
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(theirs)}):
            home = codex_engine._chatgpt_home()
        self.assertIsNotNone(home)
        self.assertTrue(str(home).startswith(str(config.DATA_DIR)))
        link = home / "auth.json"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (theirs / "auth.json").resolve())
        self.assertFalse((home / "config.toml").exists())

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(theirs)}):
            self.assertEqual(codex_engine._chatgpt_home(), home)

    def test_the_spawn_env_is_sealed(self):
        from unittest import mock
        from agent import loop
        with mock.patch.dict(os.environ, {"OPENAI_BASE_URL": "https://elsewhere",
                                          "CODEX_HOME": "/theirs"}):
            env = loop.sealed_env({"CODEX_HOME": "/ours"})
        self.assertEqual(env["OPENAI_BASE_URL"], "")
        self.assertEqual(env["CODEX_HOME"], "/ours")

class IntakeHardeningTest(unittest.TestCase):
    def test_string_shaped_list_and_dict_args_parse(self):
        out = actions._coerce_args(
            "save_plan", {"plan": '{"summary": "s", "nodes": []}'})
        self.assertEqual(out["plan"], {"summary": "s", "nodes": []})
        out = actions._coerce_args(
            "declare_variables", {"entries": '[{"name": "cap", "value": "5"}]'})
        self.assertEqual(out["entries"], [{"name": "cap", "value": "5"}])

    def test_plain_strings_and_bad_json_pass_through(self):
        out = actions._coerce_args(
            "run_cell", {"name": "x", "code": "{'not': json}"})
        self.assertEqual(out["code"], "{'not': json}")
        out = actions._coerce_args(
            "save_plan", {"plan": "{broken json"})
        self.assertEqual(out["plan"], "{broken json")
        out = actions._coerce_args("unknown_tool", {"a": "[1]"})
        self.assertEqual(out["a"], "[1]")

if __name__ == "__main__":
    unittest.main()

class PausedCardOnCodexTest(unittest.TestCase):
    def test_the_spawn_carries_no_tool_server_and_the_backstop(self):
        from agent import codex_engine
        cmd = codex_engine.spawn_command()
        self.assertFalse([c for c in cmd if "mcp_servers.cryogram" in c])

        from agent import previews
        cut = [c for c in cmd if c.startswith("tool_output_token_limit=")]
        self.assertEqual(len(cut), 1)
        self.assertEqual(int(cut[0].split("=")[1]), codex_engine.CODEX_TOOL_OUTPUT_TOKENS)
        self.assertGreater(codex_engine.CODEX_TOOL_OUTPUT_TOKENS * 3, previews.inline_limit("codex"))

        self.assertIn("tools.web_search=false", cmd)
        self.assertIn("features.apps=false", cmd)

        for f in ("shell_tool", "code_mode_host", "tool_search_always_defer_mcp_tools", "browser_use", "plugins"):
            self.assertIn(f"features.{f}=false", codex_engine.LOCKDOWN_CONFIG)

    def test_builder_turns_stay_out_of_the_users_chat_history(self):
        from agent import codex_engine
        cmd = codex_engine.spawn_command()
        self.assertIn('history.persistence="none"', cmd)

    def test_the_thinking_effort_rides_codex_own_config_key(self):
        from agent import codex_engine
        self.assertIn('model_reasoning_effort="low"', codex_engine.spawn_command("low"))
        self.assertFalse([c for c in codex_engine.spawn_command() if "reasoning_effort" in c])

    def test_an_orphaned_wait_is_parked_and_a_late_answer_misses_resolve(self):
        import threading
        import time
        from agent import interactions
        iid = interactions.create_sync("ask", {"workflow_id": "p_park1", "title": "q"})
        got = {}
        def waiter():
            got["v"] = interactions.wait_sync(iid, timeout=30)
        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.2)
        parked = interactions.park_workflow("p_park1")
        t.join(timeout=5)
        self.assertEqual(parked, [iid])
        self.assertFalse(t.is_alive())
        self.assertIsNone(got["v"])
        self.assertFalse(interactions.resolve(iid, "yes"))

        other = interactions.create_sync("ask", {"workflow_id": "p_park2", "title": "q"})
        self.assertEqual(interactions.park_workflow("p_park1"), [])
        self.assertTrue(interactions.resolve(other, "ok"))

        self.assertEqual(interactions.wait_sync(other, timeout=1), "ok")

class AppServerProcessTest(unittest.TestCase):
    def test_the_spawn_names_utf8(self):
        from unittest import mock
        from agent import codex_engine
        seen = {}

        class FakeProc:
            stdin = mock.Mock()
            stdout = iter(())
            stderr = iter(())

        def fake_popen(cmd, **kw):
            seen.update(kw)
            return FakeProc()
        with mock.patch.object(codex_engine.subprocess, "Popen", fake_popen), \
                mock.patch.object(codex_engine, "spawn_command", lambda effort: ["codex"]):
            import tempfile
            codex_engine._AppServer("p_utf8", pathlib.Path(tempfile.mkdtemp()), "tok")
        self.assertEqual(seen.get("encoding"), "utf-8")
        self.assertEqual(seen.get("errors"), "replace")

    def test_terminate_kills_before_closing_the_pipe(self):
        from agent import codex_engine
        order = []

        class Stdin:
            def close(self):
                order.append("close")

        class Proc:
            stdin = Stdin()
            def terminate(self): order.append("terminate")
            def wait(self, timeout=None): order.append("wait")
            def kill(self): order.append("kill")
        app = codex_engine._AppServer.__new__(codex_engine._AppServer)
        app.proc = Proc()
        app.terminate()
        self.assertEqual(order, ["terminate", "wait", "close"])
        self.assertFalse(hasattr(codex_engine, "INTERRUPT_WAIT"))
        self.assertFalse(hasattr(codex_engine, "CODEX_SILENCE"))

class LockdownNoopTest(unittest.TestCase):
    # The disabled tool servers point at a command every OS has - the app's own interpreter doing nothing
    def test_the_noop_is_the_apps_own_interpreter(self):
        import sys
        from agent import codex_engine
        self.assertIn(pathlib.Path(sys.executable).as_posix(), codex_engine.LOCKDOWN_CONFIG[0])
        self.assertIn('args = ["-c", "pass"]', codex_engine.LOCKDOWN_CONFIG[0])
        self.assertNotIn("/usr/bin/true", "\n".join(codex_engine.LOCKDOWN_CONFIG))
        import tempfile
        d = pathlib.Path(tempfile.mkdtemp())
        codex_engine.write_workflow_lockdown(d)
        cfg = (d / ".codex" / "config.toml").read_text()
        self.assertNotIn("/usr/bin/true", cfg)
        self.assertEqual(cfg.count(pathlib.Path(sys.executable).as_posix()), 2)
