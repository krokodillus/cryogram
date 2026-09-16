# Tests: the window keeps the session between windows, and signing in is the step's own work
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from tests import _bootstrap

from agent import gate
from runtime import _sandbox_runner, capability

class _Context:
    def __init__(self, cookies=()):
        self.jar = [dict(c) for c in cookies]
        self.added = []

    def cookies(self):
        return list(self.jar)

    def add_cookies(self, cookies):
        self.added.extend(cookies)
        self.jar.extend(cookies)

    def storage_state(self, path=None):
        with open(path, "w") as f:
            json.dump({"cookies": self.jar, "origins": []}, f)

def _bind_profile():
    capability._ctx.update({"outputs": {}, "snapshots": [],
                            "profile_dir": tempfile.mkdtemp(prefix="cg_session_profile_")})
    return os.path.join(capability.browser_profile(), capability.STORAGE_STATE_NAME)

class TheSessionOutlivesTheWindowTest(unittest.TestCase):
    def test_a_saved_session_comes_back_for_what_the_profile_lost(self):
        state = _bind_profile()
        _Context([{"name": "sess", "domain": "example.test", "path": "/", "expires": -1},
                  {"name": "pref", "domain": "example.test", "path": "/", "expires": -1}]
                 ).storage_state(path=state)
        ctx = _Context([{"name": "pref", "domain": "example.test", "path": "/",
                         "value": "newer", "expires": -1}])
        capability._put_session_back(ctx)
        self.assertEqual([c["name"] for c in ctx.added], ["sess"])

        self.assertEqual([c.get("value") for c in ctx.jar if c["name"] == "pref"], ["newer"])

    def test_an_expired_cookie_is_never_put_back(self):
        state = _bind_profile()
        _Context([{"name": "old", "domain": "example.test", "path": "/",
                   "expires": time.time() - 60}]).storage_state(path=state)
        ctx = _Context()
        capability._put_session_back(ctx)
        self.assertEqual(ctx.added, [])

    def test_the_session_is_saved_and_a_broken_file_means_signing_in_again(self):
        state = _bind_profile()
        capability._keep_session(_Context([{"name": "sess", "domain": "example.test",
                                            "path": "/", "expires": -1}]))
        with open(state) as f:
            self.assertEqual(json.load(f)["cookies"][0]["name"], "sess")
        with open(state, "w") as f:
            f.write("not json")
        ctx = _Context()
        capability._put_session_back(ctx)
        self.assertEqual(ctx.added, [])

class SigningInIsTheStepsOwnTest(unittest.TestCase):
    def test_no_sign_in_helper_on_the_surface(self):
        self.assertFalse(hasattr(capability, "browser_login"))
        self.assertNotIn("browser_login", gate.RESERVED_NAMES)
        src = open(_sandbox_runner.__file__).read()
        self.assertNotIn('"browser_login"', src)
        self.assertIn('"confirm_with_user": capability.confirm_with_user', src)

    def test_the_finder_names_a_real_browser_or_none(self):
        exe = capability.browser_executable()
        if exe is not None:
            self.assertTrue(os.path.exists(exe))
            self.assertTrue(any(w in exe for w in ("Chrome", "Chromium", "Edge", "chrome", "chromium", "edge")))

    def test_the_page_patches_cover_the_two_tells(self):
        self.assertIn("'webdriver'", capability._PAGE_PATCH)
        self.assertIn("attachShadow", capability._PAGE_PATCH)
        self.assertIn("__closedRoot", capability._PAGE_PATCH)
        self.assertIn("Function.prototype.toString", capability._PAGE_PATCH)

if __name__ == "__main__":
    unittest.main()
