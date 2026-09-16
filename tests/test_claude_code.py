# Tests: the Claude route runs on Claude Code's own sign-in - the check, its three answers, and the SDK environment it sets
from __future__ import annotations

import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests import _bootstrap

from agent import claude_code
from agent import loop
from agent import transport

def _stand_in_cli(name: str, body: str) -> str:
    p = Path(_bootstrap.TMP) / "stand_in_cli" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)

@unittest.skipIf(os.name == "nt", "the stand-in is a shell script")
class CheckLoginTest(unittest.TestCase):
    def test_signed_in(self):
        binp = _stand_in_cli("in", 'echo \'{"loggedIn": true, "email": "o@example.com"}\'\n')
        with mock.patch.dict(os.environ, {"CRYOGRAM_CLAUDE_BIN": binp}):
            r = claude_code.check_login()
        self.assertTrue(r["ok"] and r["installed"])
        self.assertIn("o@example.com", r["message"])

    def test_the_version_and_the_floor_ride_the_check(self):
        binp = _stand_in_cli("versioned",
                             'if [ "$1" = "--version" ]; then echo "2.1.114 (Claude Code)"; exit 0; fi\n'
                             'echo \'{"loggedIn": true}\'\n')
        with mock.patch.dict(os.environ, {"CRYOGRAM_CLAUDE_BIN": binp}):
            r = claude_code.check_login()
            self.assertTrue(r["ok"])
            self.assertEqual(r["version"], "2.1.114")
            self.assertEqual(r["path"], binp)
            self.assertEqual(loop.sdk_program(), binp)
        old = _stand_in_cli("old",
                            'if [ "$1" = "--version" ]; then echo "1.0.80 (Claude Code)"; exit 0; fi\n'
                            'echo \'{"loggedIn": true}\'\n')
        with mock.patch.dict(os.environ, {"CRYOGRAM_CLAUDE_BIN": old}):
            r = claude_code.check_login()
            self.assertFalse(r["ok"])
            self.assertTrue(r["too_old"])
            self.assertIn("needs 2.0.0 or newer", r["message"])
            with self.assertRaises(transport.TransportError) as cm:
                loop.sdk_program()
            self.assertIn("needs 2.0.0 or newer", str(cm.exception))

        kw = loop.sdk_option_kwargs("sys", object(), [], [], None, {}, None, cli_path=binp)
        self.assertEqual(kw["cli_path"], binp)
        self.assertNotIn("cli_path", loop.sdk_option_kwargs("sys", object(), [], [], None, {}, None))

        self.assertEqual(loop.sdk_option_kwargs("sys", object(), [], [], None, {}, None, effort="high")["effort"], "high")
        self.assertNotIn("effort", loop.sdk_option_kwargs("sys", object(), [], [], None, {}, None))

        cb = lambda line: None
        self.assertIs(loop.sdk_option_kwargs("sys", object(), [], [], None, {}, None, stderr=cb)["stderr"], cb)
        self.assertNotIn("stderr", loop.sdk_option_kwargs("sys", object(), [], [], None, {}, None))

    def test_a_program_that_died_is_said_in_one_plain_sentence(self):
        raw = ("Command failed with exit code -9 (exit code: -9)\n"
               "Error output: Check stderr output for details")
        msg = loop.program_died_message(raw)
        self.assertIn("Claude Code stopped before it finished (exit code -9)", msg)
        self.assertIn("debug log", msg)
        self.assertNotIn("stderr", msg)
        self.assertEqual(loop.program_died_message("API Error: Overloaded"), "")
        self.assertEqual(loop.program_died_message(""), "")

    def test_the_sdks_own_log_goes_to_the_debug_log_not_the_terminal(self):
        import io
        import logging
        from contextlib import redirect_stderr
        from agent import chatlog
        seen = []
        with mock.patch.object(chatlog, "append", lambda wid, kind, data: seen.append((wid, kind, data))):
            loop._route_sdk_log("wfl_test")
            log = logging.getLogger("claude_agent_sdk")
            buf = io.StringIO()
            with redirect_stderr(buf):
                log.error("Fatal error in message reader: boom")
        self.assertEqual(buf.getvalue(), "")
        self.assertEqual(seen, [("wfl_test", "sdk-log", {"level": "ERROR", "message": "Fatal error in message reader: boom"})])
        self.assertFalse(log.propagate)

        loop._route_sdk_log("wfl_other")
        self.assertEqual(sum(isinstance(h, loop._SdkLogHandler) for h in log.handlers), 1)

    def test_installed_but_not_signed_in(self):
        binp = _stand_in_cli("out", 'echo \'{"loggedIn": false, "authMethod": "none"}\'\n')
        with mock.patch.dict(os.environ, {"CRYOGRAM_CLAUDE_BIN": binp}):
            r = claude_code.check_login()
        self.assertFalse(r["ok"])
        self.assertTrue(r["installed"])
        self.assertIn("run `claude`", r["message"])

    def test_not_installed(self):
        with mock.patch.dict(os.environ, {"CRYOGRAM_CLAUDE_BIN": ""}), \
             mock.patch.object(claude_code, "claude_bin", lambda: None):
            r = claude_code.check_login()
        self.assertFalse(r["ok"])
        self.assertFalse(r["installed"])
        self.assertIn(claude_code.INSTALL_PAGE, r["message"])

class SdkEnvironmentTest(unittest.TestCase):
    def test_the_sign_in_route_carries_no_credential_and_the_default_folder(self):
        env = loop._sdk_env(transport.SdkTransport("m", "", auth="claude-subscription"))
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "")

        self.assertNotIn("CLAUDE_CONFIG_DIR", env)

    def test_the_api_key_route_keeps_the_apps_own_folder(self):
        env = loop._sdk_env(transport.SdkTransport("m", "sk-x", auth="api-key"))
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-x")
        self.assertTrue(env["CLAUDE_CONFIG_DIR"].endswith("claude_home"))

    def test_the_transport_needs_no_secret_for_the_sign_in(self):
        from storage import settings
        with mock.patch.object(settings, "get", lambda: {
                "master_ai": {"model": "claude-sonnet-5"},
                "providers": [{"id": "builder", "adapter": "anthropic", "use": "builder",
                               "auth": "claude-subscription", "enabled": True,
                               "models": [{"name": "claude-sonnet-5"}]}]}):
            tr = transport.for_settings()
        self.assertEqual(tr.auth, "claude-subscription")
        self.assertEqual(tr.credential, "")

    def test_the_not_signed_in_message_says_what_to_do(self):
        self.assertIn("run `claude`", loop.NOT_SIGNED_IN)
        self.assertNotIn("setup-token", loop.NOT_SIGNED_IN)

if __name__ == "__main__":
    unittest.main()
