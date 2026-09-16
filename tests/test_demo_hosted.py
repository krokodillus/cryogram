# Tests: the hosted demo's integration supplies the one context line the core no longer carries
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from tests import _bootstrap

import extensions
from agent import context

ROOT = Path(__file__).resolve().parent.parent

@unittest.skipUnless((ROOT / "demo").is_dir(), "the demo tree is not part of this checkout")
class HostedLineTest(unittest.TestCase):
    def test_the_line_arrives_through_the_seam(self):
        spec = importlib.util.spec_from_file_location(
            "hosted_demo_ext", ROOT / "demo/common/extensions/hosted/__init__.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        extensions.reset()
        reg = extensions.Registry("hosted")
        mod.register(reg)
        extensions._merge(reg)
        try:
            line = context._where_it_runs({})
            self.assertIn("[hosted]", line)
            self.assertIn("browser windows", line)
        finally:
            extensions.reset()

    def test_without_it_the_core_says_nothing(self):
        extensions.reset()
        self.assertEqual(context._where_it_runs({}), "")
