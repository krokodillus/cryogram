# Tests: per-workflow dependencies: spec validation, the AI-SDK denylist, interpreter selection, declaration tool (with an injected installer - never real pip here) and plan-level package validation
from __future__ import annotations

import sys
import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from storage import deps
from agent import node_tools

class DepsValidateTest(unittest.TestCase):
    def test_plain_and_pinned_names_pass(self):
        self.assertEqual(deps.validate(["pypdf", "openpyxl==3.1.2"]), [])

    def test_urls_git_and_paths_refused(self):
        for bad in ("git+https://x/y.git", "https://evil/pkg.whl", "./local", "-e ."):
            self.assertTrue(deps.validate([bad]), bad)

    def test_denylist_normalised(self):
        for sdk in ("anthropic", "OpenAI", "google_genai", "Google-GenerativeAI",
                    "litellm==1.0"):
            errs = deps.validate([sdk])
            self.assertTrue(any("ai_call" in e for e in errs), sdk)

    def test_python_for_falls_back_to_app_interpreter(self):
        self.assertEqual(deps.python_for("p_no_venv"), sys.executable)

class VenvTimeoutNamesItsStageTest(unittest.TestCase):
    def test_venv_stage_timeout_message(self):
        import subprocess
        from unittest import mock
        with mock.patch.object(deps, "_template_venv",
                               side_effect=subprocess.TimeoutExpired("venv", 120)):
            r = deps.install("p_venv_to", ["requests"])
        self.assertIn("Setting up the workflow's packages", r["error"])
        self.assertIn(str(deps.VENV_TIMEOUT), r["error"])
        self.assertNotIn("pin a lighter", r["error"])

class DeclareDependenciesTest(unittest.TestCase):
    def _node_workflow(self):
        return _workflow([{"id": "n_d", "name": "parse", "type": "code",
                          "config": {}, "inputs": [], "outputs": [], "tests": []}],
                        pid="p_deps")

    def test_declares_installs_and_locks(self):
        p = self._node_workflow()
        fake = lambda pid, pkgs: {"ok": True, "pins": ["pypdf==4.2.0"]}
        r = node_tools._declare_dependencies(p, "n_d", ["pypdf"], installer=fake)
        self.assertTrue(r["ok"])
        node = p["nodes"][0]
        self.assertEqual(node["config"]["dependencies"], ["pypdf"])
        self.assertEqual(node["config"]["dependency_lock"], "pypdf==4.2.0")

    def test_denylist_refused_before_install(self):
        p = self._node_workflow()
        called = []
        fake = lambda pid, pkgs: called.append(1) or {"ok": True, "pins": []}
        r = node_tools._declare_dependencies(p, "n_d", ["openai"], installer=fake)
        self.assertFalse(r.get("ok", False))
        self.assertEqual(called, [])

    def test_only_code_and_connector_nodes(self):
        p = _workflow([{"id": "n_ai", "name": "judge", "type": "ai", "config": {},
                       "inputs": [], "outputs": [], "tests": []}], pid="p_deps2")
        r = node_tools._declare_dependencies(p, "n_ai", ["pypdf"],
                                                 installer=lambda *a: {"ok": True})
        self.assertIn("error", r)

class PlanPackagesTest(unittest.TestCase):
    def test_plan_gaps_denylisted_packages_and_the_build_refuses(self):
        p = _workflow(pid="p_planpkg")
        node_tools.tool_save_intent(p, "Purpose.")
        r = node_tools.tool_save_plan(p, {"summary": "Do it.", "nodes": [
            {"name": "parse", "type": "code", "packages": ["anthropic"],
             "outputs": [{"name": "x", "type": "number"}]}]})
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("ai_call" in g for g in r["design_gaps"]), r)
        self.assertTrue(p["plan"]["nodes"])
        self.assertIn("design gaps",
                      node_tools.tool_build_workflow(p).get("error", ""))

    def test_plan_accepts_clean_packages(self):
        p = _workflow(pid="p_planpkg2")
        node_tools.tool_save_intent(p, "Purpose.")
        r = node_tools.tool_save_plan(p, {"summary": "Do it.", "nodes": [
            {"name": "parse", "type": "code", "packages": ["pypdf==4.2.0"],
             "outputs": [{"name": "x", "type": "number"}]}]})
        self.assertTrue(r["ok"], r)

class AppPackagesTest(unittest.TestCase):
    def test_playwright_is_never_installed_per_bundle(self):
        from unittest import mock
        self.assertEqual(deps.bundle_only(["playwright", "pdfplumber"]), ["pdfplumber"])
        self.assertEqual(deps.bundle_only(["Playwright==1.4"]), [])
        def no_pip(cmd, **kw):
            raise AssertionError(f"pip ran for the app's own package: {cmd}")
        with mock.patch.object(deps.subprocess, "run", no_pip):
            self.assertTrue(deps.install("p_app", ["playwright"]).get("ok"))
            self.assertEqual(deps.unsatisfied("p_app", ["playwright"]), [])

    def test_a_browser_cells_probe_sees_the_apps_packages(self):
        import config
        self.assertTrue(config.app_site_packages().endswith("site-packages"))

        self.assertEqual(deps.missing_modules("p_probe", ["no_such_module_x"],
                                              app_packages=True), ["no_such_module_x"])

class SkipSatisfiedTest(unittest.TestCase):
    def test_install_skips_pip_when_venv_already_satisfies(self):
        from unittest import mock
        freeze = mock.Mock(returncode=0, stdout="pdfplumber==0.11.0\n", stderr="")
        def fake_run(cmd, **kw):
            if "freeze" in cmd:
                return freeze
            raise AssertionError(f"pip ran when everything was satisfied: {cmd}")
        fake_vdir = mock.MagicMock()
        fake_vdir.__truediv__ = lambda self, x: mock.Mock(exists=lambda: True)
        with mock.patch.object(deps, "python_for", return_value="python3"), \
             mock.patch.object(deps.subprocess, "run", fake_run), \
             mock.patch.object(deps, "venv_dir", return_value=fake_vdir):
            r = deps.install("p_skip", ["pdfplumber"])
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("already_installed"))
        self.assertEqual(r["pins"], ["pdfplumber==0.11.0"])
        self.assertIn("seconds", r)

if __name__ == "__main__":
    unittest.main()
