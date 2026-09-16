# Whether Claude Code is installed and signed in on this computer; the builder's Claude subscription is that sign-in
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import time
from pathlib import Path

CHECK_TIMEOUT = 20
# The page that installs Claude Code, the program every Claude route runs through, key or subscription
INSTALL_PAGE = "https://docs.claude.com/en/docs/claude-code/setup"
CONNECTED_TTL = 60

MIN_VERSION = "2.0.0"

# The one sentence for a program below the floor, said on the card and by a turn that is tried
def too_old_sentence(program: str, version: str, floor: str) -> str:
    return (f"{program} {version} is installed, and Cryogram needs {floor} or newer - "
            f"update it, then check again")

# The version number in a program's own version line, or blank
def version_number(text: str) -> str:
    m = re.search(r"\d+\.\d+\.\d+", str(text or ""))
    return m.group(0) if m else ""

# Whether a version is below a floor; an unknown version is never called old
def below(version: str, floor: str) -> bool:
    import config
    return bool(version) and config.version_tuple(version) < config.version_tuple(floor)

# Claude Code's version on this computer, from its own version line
def version_of(binp: str) -> str:
    try:
        return version_number(_run(binp, "--version").stdout)
    except Exception:
        return ""

# Finds Claude Code's command: the path, then the places installers put it when the app's own PATH is thin
def claude_bin() -> str | None:
    env = os.environ.get("CRYOGRAM_CLAUDE_BIN")
    if env:
        return env
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    candidates = [home / ".claude" / "local" / "claude",
                  Path("/usr/local/bin/claude"), Path("/opt/homebrew/bin/claude")]
    nvm = home / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        for d in sorted(nvm.iterdir(), reverse=True):
            candidates.append(d / "bin" / "claude")
    if os.name == "nt":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        candidates.append(appdata / "npm" / "claude.cmd")
    for c in candidates:
        if c.exists():
            return str(c)
    return None

def _run(binp: str, *args: str) -> subprocess.CompletedProcess:
    cmd = [binp, *args]
    if os.name == "nt" and binp.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", *cmd]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=CHECK_TIMEOUT)

# Installed and signed in, or what to do: the "Check connection" answer for the Claude route
def check_login() -> dict:
    binp = claude_bin()
    if not binp:
        return {"ok": False, "installed": False, "path": "", "version": "",
                "message": f"Claude Code isn't installed. Install it from "
                           f"{INSTALL_PAGE}, then run `claude` and sign in."}
    version = version_of(binp)
    program = {"installed": True, "path": binp, "version": version}
    if below(version, MIN_VERSION):
        return {"ok": False, **program, "too_old": True,
                "message": too_old_sentence("Claude Code", version, MIN_VERSION)}
    try:
        r = _run(binp, "auth", "status")
    except Exception as e:
        return {"ok": False, **program,
                "message": f"Couldn't check the sign-in: {e}"}
    try:
        status = json.loads(r.stdout or "{}")
    except ValueError:
        status = {}
    if status.get("loggedIn"):
        who = str(status.get("email") or "").strip()
        return {"ok": True, **program, "email": who,
                "message": "Signed in and ready." + (f" ({who})" if who else "")}
    return {"ok": False, **program,
            "message": "Not signed in yet. In a terminal run `claude`, sign in when it asks, then check again."}

_hint = {"ts": 0.0, "v": False, "program": {}}

# The cached yes/no for the settings payload, so a page load never waits on a subprocess
def connected_hint(fresh: bool = False) -> bool:
    now = time.time()
    if fresh or now - _hint["ts"] > CONNECTED_TTL:
        got = check_login()
        _hint["v"] = bool(got.get("ok"))
        _hint["program"] = {k: got.get(k) for k in ("path", "version", "too_old", "message")}
        _hint["ts"] = now
    return _hint["v"]

# Which Claude Code the card shows: its path, its version and the floor, from the same cached check
def program_hint(fresh: bool = False) -> dict:
    connected_hint(fresh)
    return {**_hint["program"], "floor": MIN_VERSION}
