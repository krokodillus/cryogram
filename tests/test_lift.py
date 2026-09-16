# Tests for storage/lift.py: the one-time data-lift seam (marker-gated, idempotent)
from __future__ import annotations

import unittest
from unittest import mock

from tests import _bootstrap

from storage import lift
from storage import settings

class MarkerSeam(unittest.TestCase):
    def test_mark_then_done(self):
        self.assertFalse(lift.done("t_lift_marker"))
        lift.mark("t_lift_marker")
        self.assertTrue(lift.done("t_lift_marker"))

    def test_mark_merges_never_replaces(self):
        lift.mark("t_lift_a")
        lift.mark("t_lift_b")
        m = settings.get().get("migrations") or {}
        self.assertTrue(m.get("t_lift_a"))
        self.assertTrue(m.get("t_lift_b"))

    def test_seed_markers_share_the_table(self):
        s = settings.get()
        settings.update({"migrations": {**(s.get("migrations") or {}),
                                        "default_providers": True}})
        lift.mark("t_lift_c")
        m = settings.get().get("migrations") or {}
        self.assertTrue(m.get("default_providers"))
        self.assertTrue(m.get("t_lift_c"))

class RunPending(unittest.TestCase):
    def test_runs_each_step_and_isolates_failures(self):
        calls = []

        def ok_step():
            calls.append("ok")

        def bad_step():
            calls.append("bad")
            raise RuntimeError("boom")

        def after_step():
            calls.append("after")

        with mock.patch.object(lift, "_STEPS", (ok_step, bad_step, after_step)):
            lift.run_pending()

        self.assertEqual(calls, ["ok", "bad", "after"])

    def test_marker_gated_step_runs_once(self):
        calls = []

        def step():
            if lift.done("t_lift_once"):
                return
            calls.append("work")
            lift.mark("t_lift_once")

        with mock.patch.object(lift, "_STEPS", (step,)):
            lift.run_pending()
            lift.run_pending()
        self.assertEqual(calls, ["work"])

class BuildChannel(unittest.TestCase):
    def test_dev_checkout_defaults(self):
        import config
        self.assertEqual(config.BUILD, "dev")
        self.assertEqual(str(config.DATA_DIR), _bootstrap.TMP)

    def test_platform_channel_moves_the_default_dir(self):
        import config
        dev = config._default_data_dir()
        with mock.patch.object(config, "BUILD", "mac-silicon"):
            dist = config._default_data_dir()
        self.assertNotEqual(dev, dist)
        self.assertIn("Cryogram", str(dist) + str(dist).lower())

if __name__ == "__main__":
    unittest.main()
