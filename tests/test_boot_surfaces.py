# Tests: the shipped boot script's launch surfaces - the word on the path, the app icon, and the stop and status commands
from __future__ import annotations

import importlib.util
import os
import sys
import subprocess
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest import mock

from tests import _bootstrap

REPO = Path(__file__).resolve().parent.parent

BOOT_PY = next(p for p in (REPO / "boot.py", REPO / "scripts" / "build_dist" / "boot.py")
               if p.is_file())

def _boot(tmp_name: str):
    root = Path(_bootstrap.TMP) / tmp_name / "Cryogram"
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "boot.py").write_text(BOOT_PY.read_text())
    (root / "backend" / "config.py").write_text('VERSION = "0.4.46"\n')
    spec = importlib.util.spec_from_file_location(f"boot_{tmp_name}", root / "boot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return root, mod

class LaunchSurfacesTest(unittest.TestCase):
    def test_the_word_runs_this_folder_with_the_python_the_launcher_found(self):
        root, boot = _boot("boot_word")
        text = boot.shim_text()
        self.assertIn(str(root / "boot.py"), text)
        self.assertIn("python", text)

        self.assertEqual(boot.private_python(), Path(sys.executable))
        own = root / "python" / "bin" / "python3"
        own.parent.mkdir(parents=True)
        own.write_text("")
        self.assertEqual(boot.private_python().resolve(), own.resolve())
        self.assertEqual(boot.app_version(), "0.4.46")
        self.assertFalse(hasattr(boot, "install_from_zip"))
        self.assertFalse(hasattr(boot, "staged_update"))
        if os.name != "nt":
            self.assertTrue(text.startswith("#!/bin/sh"))
            self.assertIn('"$@"', text)

    def test_first_start_writes_the_surfaces_once_and_a_second_start_nothing(self):
        root, boot = _boot("boot_surfaces")
        home = Path(_bootstrap.TMP) / "boot_surfaces" / "home"
        home.mkdir(parents=True, exist_ok=True)
        env = {"HOME": str(home), "SHELL": "", "PATH": "/usr/bin:/bin", "XDG_DATA_HOME": str(home / ".local" / "share")}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(Path, "home", staticmethod(lambda: home)), \
             mock.patch.object(boot, "_mac_icon", lambda res: None), \
             mock.patch.object(boot.subprocess, "run") as run:
            first = boot.install_surfaces()
            second = boot.install_surfaces()
        self.assertTrue(first, first)
        self.assertEqual(second, [])
        if os.name != "nt":
            shim = home / ".local" / "bin" / "cryogram"
            self.assertTrue(shim.exists())
            self.assertTrue(os.access(shim, os.X_OK))
            profile = home / (".zprofile" if sys.platform == "darwin" else ".profile")
            self.assertIn(".local/bin", profile.read_text())
            self.assertEqual(profile.read_text().count("Added by Cryogram"), 1)
        if sys.platform == "darwin":
            app = home / "Applications" / "Cryogram.app" / "Contents"
            self.assertIn("<key>CFBundleExecutable</key><string>cryogram</string>",
                          (app / "Info.plist").read_text())
            self.assertIn("boot.py", (app / "MacOS" / "cryogram").read_text())
            self.assertTrue(os.access(app / "MacOS" / "cryogram", os.X_OK))
        elif os.name != "nt":
            entry = home / ".local" / "share" / "applications" / "cryogram.desktop"
            self.assertIn("Terminal=false", entry.read_text())
        self.assertFalse(run.called or os.name == "nt", "no external tool on a non-Windows box")

    def test_the_windows_shortcut_script_quotes_paths(self):
        root, boot = _boot("boot_lnk")
        script = boot.windows_shortcut_script(Path("C:/Users/o'brien/Desktop/Cryogram.lnk"),
                                              Path("C:/x/favicon.ico"))
        self.assertIn("o''brien", script)
        self.assertIn("WindowStyle = 7", script)
        self.assertIn("boot.py", script)

    @unittest.skipIf(os.name == "nt", "POSIX launchers")
    def test_command_executes_literal_paths_and_preserves_arguments(self):
        root, boot = _boot("boot quote '$dollar`tick")
        (root / "boot.py").write_text("import sys; print(repr(sys.argv[1:]))")
        with mock.patch.object(boot, "private_python", return_value=Path(sys.executable)):
            shim = root / "cryogram"
            boot._write(shim, boot.shim_text(), executable=True)
        result = subprocess.run([str(shim), "hello world", "$literal"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "['hello world', '$literal']")
        shim.chmod(0o644)
        boot._write(shim, shim.read_text(), executable=True)
        self.assertTrue(os.access(shim, os.X_OK))

    @unittest.skipIf(os.name == "nt", "POSIX profiles")
    def test_new_interactive_shell_finds_command_even_with_a_misleading_comment(self):
        _, boot = _boot("boot_profiles")
        for shell in ("bash", "zsh"):
            executable = shutil.which(shell)
            if not executable:
                continue
            with tempfile.TemporaryDirectory() as folder:
                home = Path(folder)
                env = {"HOME": folder, "SHELL": executable, "PATH": "/usr/bin:/bin", "ZDOTDIR": folder}
                with mock.patch.dict(os.environ, env), mock.patch.object(Path, "home", return_value=home):
                    profile = boot.profile_line_needed()
                    profile.write_text("# .local/bin is not configured yet\n")
                    self.assertEqual(boot.profile_line_needed(), profile)
                    profile.write_text(profile.read_text() + boot.profile_line())
                    self.assertIsNone(boot.profile_line_needed())
                    boot._write(home / ".local/bin/cryogram", "#!/bin/sh\necho found\n", executable=True)
                result = subprocess.run([executable, "-lic", "cryogram"], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("found", result.stdout)

    def test_fish_and_custom_zsh_configuration_locations(self):
        _, boot = _boot("boot_custom_profiles")
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {"SHELL": "/bin/zsh", "ZDOTDIR": folder}):
                self.assertEqual(boot.profile_line_needed(), Path(folder) / ".zshrc")
            with mock.patch.dict(os.environ, {"SHELL": "/bin/fish", "XDG_CONFIG_HOME": folder}):
                self.assertEqual(boot.profile_line_needed(), Path(folder) / "fish/conf.d/cryogram.fish")
                self.assertIn("fish_add_path", boot.profile_line())

class StopAndStatusTest(unittest.TestCase):
    def test_failed_shortcut_setup_returns_failure_to_installer(self):
        _, boot = _boot("boot_setup_failure")
        with mock.patch.object(boot, "install_surfaces", return_value=["(the command could not be installed: denied)"]), \
             mock.patch.object(boot, "ensure_venv"):
            self.assertEqual(boot.cmd_setup(), 1)

    def test_a_no_to_the_icon_is_remembered_and_honoured(self):
        root, boot = _boot("boot_no_icon")
        data = root.parent / "data"
        written = []
        with mock.patch.object(boot, "data_dir", return_value=data), \
             mock.patch.object(boot, "ensure_venv"), \
             mock.patch.object(boot, "_write", side_effect=lambda p, *a, **k: written.append(p) or True), \
             mock.patch.object(boot, "profile_line_needed", return_value=None), \
             mock.patch.object(boot, "_mac_icon", lambda res: None), \
             mock.patch.object(boot.subprocess, "run"):
            boot.cmd_setup(["--no-icon"])
            self.assertTrue((data / boot.NO_ICON_MARK).exists())
            self.assertFalse(boot.icon_wanted())
            notes = boot.install_surfaces()
            self.assertFalse(any("Cryogram in" in n for n in notes), notes)
            self.assertTrue(all(p.name == "cryogram" or "bin" in str(p) for p in written), written)
            boot.cmd_setup(["--icon"])
            self.assertTrue(boot.icon_wanted())

    def test_setup_builds_the_environment_too(self):
        _, boot = _boot("boot_setup_venv")
        with mock.patch.object(boot, "install_surfaces", return_value=[]), \
             mock.patch.object(boot, "ensure_venv") as venv:
            self.assertEqual(boot.cmd_setup(), 0)
        venv.assert_called_once()

    def test_nothing_running_is_a_plain_answer(self):
        root, boot = _boot("boot_stop")
        with mock.patch.object(boot, "running_port", lambda: None):
            self.assertEqual(boot.cmd_stop(), 0)
            self.assertEqual(boot.cmd_status(), 1)

    def test_stop_asks_the_running_instance_and_waits_for_it_to_go(self):
        root, boot = _boot("boot_stop2")
        answers = {"v": True}
        calls = []
        def fake_open(req, timeout=0):
            calls.append(getattr(req, "full_url", str(req)))
            answers["v"] = False
            class R:
                def read(self): return b"{}"
            return R()
        with mock.patch.object(boot, "running_port", lambda: 8000), \
             mock.patch.object(boot.urllib.request, "urlopen", fake_open), \
             mock.patch.object(boot, "answers_like_cryogram", lambda port: answers["v"]):
            self.assertEqual(boot.cmd_stop(), 0)
        self.assertTrue(calls and calls[0].endswith("/api/shutdown"))

    def test_the_usage_lists_the_four_words(self):
        root, boot = _boot("boot_usage")
        for word in ("stop", "status", "setup"):
            self.assertIn(f"cryogram {word}", boot.USAGE)

if __name__ == "__main__":
    unittest.main()
