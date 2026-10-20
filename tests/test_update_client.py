# Tests for backend/updater.py: the update check asks for the repository's latest release, says whether it is newer, and sends only the version and the OS
from __future__ import annotations

import unittest
import urllib.error
from unittest import mock

from tests import _bootstrap

import config
import updater

def _release(tag: str) -> dict:
    return {"tag_name": tag,
            "html_url": f"https://github.com/x/y/releases/tag/{tag}"}

class CheckFlow(unittest.TestCase):
    def setUp(self):
        updater._checked = None
        self.addCleanup(lambda: setattr(updater, "_checked", None))
        p = mock.patch.object(config, "BUILD", "source")
        p.start(); self.addCleanup(p.stop)

    def _check(self, answer):
        with mock.patch.object(updater, "_update_url", return_value="https://x/latest"), \
             mock.patch.object(updater, "_http_json", **answer):
            return updater.check()

    def test_dev_checkout_is_inert(self):
        with mock.patch.object(config, "BUILD", "dev"):
            r = updater.check()
        self.assertFalse(r["available"])
        self.assertEqual(r["channel_kind"], "dev")

    def test_a_newer_release_is_available_with_its_page(self):
        r = self._check({"return_value": _release("v99.999")})
        self.assertTrue(r["available"])
        self.assertEqual(r["latest"], "99.999")
        self.assertNotIn("changelog", r)
        self.assertEqual(r["channel_kind"], "source")
        self.assertIn("releases/tag/v99.999", r["page"])

    def test_the_same_version_is_not_available(self):
        r = self._check({"return_value": _release(f"v{config.VERSION}")})
        self.assertFalse(r["available"])

    def test_an_offline_check_is_a_silent_no(self):
        r = self._check({"side_effect": urllib.error.URLError("down")})
        self.assertFalse(r["available"])
        self.assertIn("check failed", r["note"])

    def test_an_answer_without_a_tag_is_no_update(self):
        r = self._check({"return_value": {"message": "Not Found"}})
        self.assertFalse(r["available"])
        self.assertEqual(r["latest"], "")

    def test_the_check_has_no_download_and_no_approve(self):
        for gone in ("download", "approve", "state", "applicable_migrations"):
            self.assertFalse(hasattr(updater, gone), gone)

    def test_checks_turned_off_skip_the_read(self):
        from storage import settings
        with mock.patch.object(settings, "get", return_value={"preferences": {"check_updates": False}}), \
             mock.patch.object(updater, "_http_json") as read:
            r = updater.check()
        self.assertFalse(r["available"])
        self.assertFalse(read.called)

class BusyProbe(unittest.TestCase):
    def test_any_running_scans_all_workflows(self):
        import turns
        self.assertFalse(turns.any_running())
        self.assertTrue(turns.begin("p_upd_busy", "chat"))
        try:
            self.assertTrue(turns.any_running())
        finally:
            turns.finish("p_upd_busy")
        self.assertFalse(turns.any_running())

class WhatTheCheckSaysTest(unittest.TestCase):
    def test_the_user_agent_is_the_version_and_the_os(self):
        import getpass
        import socket
        ua = updater.user_agent()
        self.assertTrue(ua.startswith(f"Cryogram/{config.VERSION} ("), ua)
        self.assertTrue(ua.endswith(")"), ua)
        self.assertNotIn(socket.gethostname(), ua)
        self.assertNotIn(getpass.getuser(), ua)

    def test_the_request_carries_it(self):
        seen = {}

        class _Answer:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"tag_name": "v9.9.9"}'

        def fake_open(req, **kw):
            seen["ua"] = req.get_header("User-agent")
            seen["url"] = req.full_url
            return _Answer()

        with mock.patch.object(updater.urllib.request, "urlopen", fake_open):
            updater._http_json("https://cryogram.app/api/latest")
        self.assertEqual(seen["ua"], updater.user_agent())
        self.assertEqual(seen["url"], "https://cryogram.app/api/latest")

    def test_the_default_address_is_cryogram_app(self):
        from storage import settings
        self.assertEqual(settings.DEFAULTS["update"]["url"], "https://cryogram.app/api/latest")

class VersionSchemeTest(unittest.TestCase):
    def test_old_series_sorts_below_the_new_scheme(self):
        vt = config.version_tuple
        self.assertEqual(vt("0.179"), (0, 0, 179))
        self.assertEqual(vt("0.4.1"), (0, 4, 1))
        self.assertLess(vt("0.179"), vt("0.4.1"))
        self.assertLess(vt("0.4.9"), vt("0.4.10"))
        self.assertLess(vt("0.4.1"), vt("1.0.0"))
        self.assertEqual(vt("garbage"), ())
        self.assertEqual(updater._version_tuple("0.179"), (0, 0, 179))

if __name__ == "__main__":
    unittest.main()
