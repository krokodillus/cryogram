# Cryogram start-up: its own environment, a free port, the server in the background, and the cryogram command
import json
import os
import re
import shlex
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

APP = Path(__file__).resolve().parent
PORT_TRIES = 10
START_WAIT = 90
STOP_WAIT = 15

def say(msg):
    print(msg, flush=True)

# Workflows and settings live in the application-data area, never in the app folder
def data_dir():
    env = os.environ.get("CRYOGRAM_DATA_DIR")
    if env:
        return Path(env)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Cryogram"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Cryogram"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "cryogram"

# The version is the one line in the app's own config file
def app_version():
    try:
        text = (APP / "backend" / "config.py").read_text()
    except OSError:
        return ""
    m = re.search(r'^VERSION = "([^"]+)"', text, re.M)
    return m.group(1) if m else ""

# The private Python downloaded into the folder, or the one running this script
def private_python():
    own = APP / "python" / ("python.exe" if os.name == "nt" else "bin/python3")
    return own if own.exists() else Path(sys.executable)

def venv_python(venv):
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")

def _runs(py):
    try:
        return subprocess.run([str(py), "-c", "pass"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

# The first start builds the app's own environment from requirements.txt; a changed file or a broken venv rebuilds it
def ensure_venv():
    import hashlib
    lock = APP / "requirements.txt"
    stamp_val = hashlib.sha256(lock.read_bytes()).hexdigest()[:16] + \
        "-" + sys.version.split()[0]
    venv = APP / ".venv"
    stamp = venv / "cryogram-stamp"
    if stamp.exists() and stamp.read_text().strip() == stamp_val \
            and _runs(venv_python(venv)):
        return venv
    say("")
    say("First start: setting things up for you (this will take a minute).")
    import shutil
    shutil.rmtree(venv, ignore_errors=True)
    import venv as venv_mod

    venv_mod.create(venv, symlinks=(os.name != "nt"), with_pip=True)

    r = subprocess.run([str(venv_python(venv)), "-m", "pip", "install", "-q",
                        "--disable-pip-version-check", "-r", str(lock)])
    if r.returncode != 0:
        say("")
        say("Setup didn't finish - this usually means no internet connection. Try to reconnect, then start Cryogram again.")
        if os.name == "nt":
            input("Press Enter to close.")
        raise SystemExit(1)
    stamp.write_text(stamp_val)
    return venv

# The port the settings name, read straight from the settings table
def preferred_port():
    try:
        db = data_dir() / "app.db"
        if db.exists():
            with sqlite3.connect(str(db), timeout=5) as conn:
                row = conn.execute(
                    "SELECT value FROM settings LIMIT 1").fetchone()
            if row:
                return int(json.loads(row[0]).get("server", {})
                           .get("port", 8000))
    except Exception:
        pass
    return 8000

# What a Cryogram on this port says about itself, or None
def version_at(port):
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/version", timeout=2) as r:
            d = json.loads(r.read().decode())
            return d if d.get("version") else None
    except Exception:
        return None

def answers_like_cryogram(port):
    return version_at(port) is not None

# The port a Cryogram answers on, near the preferred one, or None
def running_port():
    want = preferred_port()
    for port in range(want, want + PORT_TRIES + 1):
        if answers_like_cryogram(port):
            return port
    return None

# The preferred port, else the next free one; a port held by a Cryogram is handed to the server, which sorts out whose it is
def pick_port():
    want = preferred_port()
    for port in range(want, want + PORT_TRIES + 1):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                if answers_like_cryogram(port):
                    return port
    say(f"No free spot near port {want} - close whatever is using those ports and start again.")
    raise SystemExit(1)

# The page opens by itself unless CRYOGRAM_NO_BROWSER=1
def open_browser(url):
    if os.environ.get("CRYOGRAM_NO_BROWSER") != "1":
        webbrowser.open(url)

# The word cryogram from any terminal: a two-line script that runs this file, rewritten on every start
def shim_text():
    py, boot = private_python(), APP / "boot.py"
    if os.name == "nt":
        return f'@echo off\r\n"{py}" "{boot}" %*\r\n'
    return f'#!/bin/sh\nexec {shlex.quote(str(py))} {shlex.quote(str(boot))} "$@"\n'

def shim_path():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Microsoft" / "WindowsApps" / "cryogram.cmd"
    return Path.home() / ".local" / "bin" / "cryogram"

# On a Mac ~/.local/bin is not on the path in a fresh account; one guarded line in the login profile fixes that
def profile_line_needed():
    if os.name == "nt":
        return None
    shell = Path(os.environ.get("SHELL", "")).name
    if shell == "zsh":
        profile = Path(os.environ.get("ZDOTDIR") or str(Path.home())) / ".zshrc"
    elif shell == "bash":
        if sys.platform == "darwin":
            profile = next((Path.home() / name for name in (".bash_profile", ".bash_login", ".profile")
                            if (Path.home() / name).exists()), Path.home() / ".bash_profile")
        else:
            profile = Path.home() / ".bashrc"
    elif shell == "fish":
        profile = Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")) / "fish" / "conf.d" / "cryogram.fish"
    else:
        profile = Path.home() / (".zprofile" if sys.platform == "darwin" else ".profile")
    try:
        if profile_line() in profile.read_text():
            return None
    except OSError:
        pass
    return profile

PROFILE_LINE = '\n# Added by Cryogram: the `cryogram` command lives here\nexport PATH="$HOME/.local/bin:$PATH"\n'

def profile_line():
    if Path(os.environ.get("SHELL", "")).name == "fish":
        return '\n# Added by Cryogram\nfish_add_path --path "$HOME/.local/bin"\n'
    return PROFILE_LINE

# Keeps the command folder on the user's PATH even when WindowsApps was removed from it
def windows_command_path():
    import winreg
    folder = str(shim_path().parent)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        if any(os.path.normcase(os.path.expandvars(p.strip().strip('"'))) == os.path.normcase(folder)
               for p in value.split(";") if p.strip()):
            return False
        winreg.SetValueEx(key, "Path", 0, kind, value.rstrip(";") + ";" + folder)
    import ctypes
    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(65535, 0x001A, 0, ctypes.c_wchar_p("Environment"), 2, 5000,
                                           ctypes.byref(result))
    return True

def plist_text():
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Cryogram</string>
<key>CFBundleDisplayName</key><string>Cryogram</string>
<key>CFBundleIdentifier</key><string>app.cryogram.launcher</string>
<key>CFBundleVersion</key><string>{app_version() or "0"}</string>
<key>CFBundleShortVersionString</key><string>{app_version() or "0"}</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleExecutable</key><string>cryogram</string>
<key>CFBundleIconFile</key><string>cryogram</string>
<key>LSMinimumSystemVersion</key><string>11.0</string>
</dict></plist>
"""

def mac_app_launcher_text():
    return f'#!/bin/bash\nexec {shlex.quote(str(private_python()))} {shlex.quote(str(APP / "boot.py"))} start\n'

def desktop_entry_text():
    return (f"[Desktop Entry]\nType=Application\nName=Cryogram\n"
            f"Comment=Workflows built by an agent, run on your machine\n"
            f'Exec="{private_python()}" "{APP / "boot.py"}" start\n'
            f"Icon={APP / 'frontend' / 'apple-touch-icon.png'}\n"
            f"Terminal=false\nCategories=Office;Utility;\n")

# PowerShell that writes one shortcut: this Python, this file, start, minimised so the console is a taskbar blip
def windows_shortcut_script(lnk, icon):
    esc = lambda p: str(p).replace("'", "''")
    return (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{esc(lnk)}'); "
            f"$s.TargetPath = '{esc(private_python())}'; "
            f"$s.Arguments = '\"{esc(APP / 'boot.py')}\" start'; "
            f"$s.WorkingDirectory = '{esc(APP)}'; "
            f"$s.IconLocation = '{esc(icon)},0'; "
            f"$s.WindowStyle = 7; $s.Description = 'Cryogram'; $s.Save()")

def _write(path, text, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == text:
        if executable:
            path.chmod(0o755)
        return False
    path.write_text(text)
    if executable:
        path.chmod(0o755)
    return True

# The app icon from the app's own PNG, with the tools every Mac has; a missing icon is a plain one, never a failed setup
def _mac_icon(res_dir):
    src = APP / "frontend" / "apple-touch-icon.png"
    icns = res_dir / "cryogram.icns"
    if icns.exists() or not src.exists():
        return
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "cryogram.iconset"
    tmp.mkdir()
    try:
        for size in (16, 32, 64, 128, 256, 512):
            for scale, suffix in ((1, ""), (2, "@2x")):
                px = size * scale
                subprocess.run(["sips", "-z", str(px), str(px), str(src), "--out",
                                str(tmp / f"icon_{size}x{size}{suffix}.png")],
                               check=True, capture_output=True)
        res_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(tmp), "-o", str(icns)],
                       check=True, capture_output=True)
    except Exception:
        pass
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)

# The word and the icon, written fresh on every start so they point at this folder wherever it is now
NO_ICON_MARK = "no-app-icon"

# Whether the app icon is wanted: yes unless the install said no
def icon_wanted():
    return not (data_dir() / NO_ICON_MARK).exists()

def install_surfaces(icon=None):
    notes = []
    try:
        if _write(shim_path(), shim_text(), executable=True):
            notes.append("the command `cryogram` (in a terminal, from anywhere)")
        profile = profile_line_needed()
        if profile is not None:
            profile.parent.mkdir(parents=True, exist_ok=True)
            with open(profile, "a") as f:
                f.write(profile_line())
            notes.append(f"a line in {profile.name} so the command is found - open a new terminal window first")
        if os.name == "nt" and windows_command_path():
            notes.append("the command on your user PATH - open a new terminal first")
    except OSError as e:
        notes.append(f"(the command could not be installed: {e})")
    if icon is None:
        icon = icon_wanted()
    if not icon:
        return notes
    try:
        if sys.platform == "darwin":
            app = Path.home() / "Applications" / "Cryogram.app"
            changed = _write(app / "Contents" / "Info.plist", plist_text())
            changed |= _write(app / "Contents" / "MacOS" / "cryogram",
                              mac_app_launcher_text(), executable=True)
            _mac_icon(app / "Contents" / "Resources")
            if changed:
                notes.append("Cryogram in your Applications folder (drag it to the Dock if you like)")
        elif os.name == "nt":
            icon = APP / "frontend" / "favicon.ico"
            start_menu = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) \
                / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Cryogram.lnk"
            desktop = Path.home() / "Desktop" / "Cryogram.lnk"
            for lnk in (start_menu, desktop):
                lnk.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                windows_shortcut_script(lnk, icon)],
                               check=True, capture_output=True, creationflags=0x08000000)
            notes.append("Cryogram in the Start menu and on the desktop")
        else:
            entry = Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")) \
                / "applications" / "cryogram.desktop"
            if _write(entry, desktop_entry_text(), executable=True):
                notes.append("Cryogram in the applications menu")
    except Exception as e:
        notes.append(f"(the app shortcut could not be created: {e})")
    return notes

# Spawns the server in the background, waits for it to answer; its output goes to a log in the data folder
def start_detached(venv, port):
    logs = data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = open(logs / "server.log", "w")
    env = dict(os.environ, PORT=str(port), CRYOGRAM_NO_BROWSER="1")
    cmd = [str(venv_python(venv)), "-u", str(APP / "backend" / "server.py")]
    if os.name == "nt":
        subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT,
                         creationflags=0x00000008 | 0x08000000)
    else:
        subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.time() + START_WAIT
    while time.time() < deadline:
        if answers_like_cryogram(port):
            return True
        time.sleep(0.4)
    say(f"Cryogram did not start. What it said is in {logs / 'server.log'}")
    return False

# Start: the surfaces, the environment, a port, then the server in the background and the browser
def cmd_start(foreground=False):
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.environ["CRYOGRAM_DATA_DIR"] = str(d)
    for line in install_surfaces():
        say("")
        say(f"Set up: {line}")
    venv = ensure_venv()
    port = pick_port()
    url = f"http://127.0.0.1:{port}"
    running = version_at(port)
    if running and running.get("version") == app_version():
        say(f"Cryogram is already running at {url} - opening it.")
        open_browser(url)
        return 0
    if foreground:
        os.environ["PORT"] = str(port)
        py, server = venv_python(venv), APP / "backend" / "server.py"
        if os.name == "nt":
            return subprocess.call([str(py), str(server)])
        os.execv(str(py), [str(py), str(server)])
    if not start_detached(venv, port):
        return 1
    say(f"Cryogram is running at {url}")
    open_browser(url)
    say("Quit it from the bottom of the app's sidebar, or with: cryogram stop")
    return 0

def cmd_stop():
    port = running_port()
    if port is None:
        say("Cryogram is not running.")
        return 0
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown",
                                     data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        say(f"Could not ask Cryogram to stop: {e}")
        return 1
    deadline = time.time() + STOP_WAIT
    while time.time() < deadline:
        if not answers_like_cryogram(port):
            say("Cryogram has stopped.")
            return 0
        time.sleep(0.3)
    say("Cryogram was asked to stop but is still answering.")
    return 1

def cmd_status():
    port = running_port()
    if port is None:
        say("Cryogram is not running.")
        return 1
    v = version_at(port) or {}
    say(f"Cryogram {v.get('version', '')} is running at http://127.0.0.1:{port}")
    return 0

# Everything but the start: the command and the icon, then the app's own environment, so the installer can finish these before its questions
def cmd_setup(args=()):
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    if "--no-icon" in args:
        (d / NO_ICON_MARK).touch()
    elif "--icon" in args:
        (d / NO_ICON_MARK).unlink(missing_ok=True)
    lines = install_surfaces()
    say("")
    say("Set up: " + ("; ".join(lines) if lines else "nothing to change"))
    ensure_venv()
    return 1 if any(line.startswith("(the ") for line in lines) else 0

USAGE = """cryogram            start Cryogram and open it in the browser
cryogram stop       stop the running Cryogram
cryogram status     say whether it is running, and where
cryogram setup      set up the environment, the command and the app icon again (--no-icon leaves the icon out, --icon puts it back)"""

def main():
    args = [a for a in sys.argv[1:] if a]
    cmd = args[0] if args else "start"
    if cmd == "start":
        raise SystemExit(cmd_start(foreground="--foreground" in args))
    if cmd == "stop":
        raise SystemExit(cmd_stop())
    if cmd == "status":
        raise SystemExit(cmd_status())
    if cmd == "setup":
        raise SystemExit(cmd_setup(args[1:]))
    say(USAGE)
    raise SystemExit(0 if cmd in ("help", "--help", "-h") else 2)

if __name__ == "__main__":
    main()
