# Paths, version and build-channel constants. Everything tunable at runtime lives in settings, not here
import os
import re
from pathlib import Path

VERSION = "0.5.0"

# Versions compare as integer tuples so 0.4.10 sorts after 0.4.9; unparseable input compares lowest
def version_tuple(v) -> tuple:
    try:
        parts = tuple(int(x) for x in str(v).strip().split("."))
    except ValueError:
        return ()
    if len(parts) == 2:
        return (0, 0, parts[1]) if parts[0] == 0 else (parts[0], parts[1], 0)
    return parts

# How much output a workflow AI step may produce by default; a step may declare its own max_tokens, and the provider is the only ceiling
AI_MAX_TOKENS = 32_768

# How long one AI call may wait; a step's own timeout_seconds can extend this, never shorten it
AI_CALL_TIMEOUT = 300

# The build channel: "dev" in a checkout, "source" in the public repository everyone installs from
BUILD = os.environ.get("CRYOGRAM_BUILD") or "source"

# An installed copy of the public repository: it checks for updates and says how to apply one
def is_source_build() -> bool:
    return BUILD == "source"

# Updates and the launch page must carry a valid Ed25519 signature from this key
RELEASE_PUBKEY = "7d9b3cc1575122fbc402b6a3366596205e4c8424981649ffe4766df27f8bdb53"

ROOT = Path(__file__).resolve().parent.parent

# Where the app's own packages are installed, so a browser step's process can import playwright from there
def app_site_packages() -> str:
    import sysconfig
    return sysconfig.get_paths().get("purelib", "")
FRONTEND_DIR = ROOT / "frontend"

# Workflows and settings live in the OS application-data area, so replacing the app folder never touches them
def _default_data_dir() -> Path:
    if BUILD == "dev":
        return ROOT / "data"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Cryogram"
    if os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Cryogram"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "cryogram"

DATA_DIR = Path(os.environ.get("CRYOGRAM_DATA_DIR") or _default_data_dir())
WORKFLOWS_DIR = DATA_DIR / "workflows"

# The skill files as shipped with this copy of the app
SHIPPED_SKILLS_DIR = ROOT / "backend" / "agent" / "skills"

# The skill files the agent actually reads: the ones that ship with this copy
SKILLS_DIR = Path(os.environ.get("CRYOGRAM_SKILLS_DIR") or SHIPPED_SKILLS_DIR)

# The workflow bundle: one folder holding everything about one workflow, moved and deleted as a unit
def workflow_dir(workflow_id: str):
    return WORKFLOWS_DIR / workflow_id

# The workflow's structured data, one SQLite file inside the bundle so it travels with it
def workflow_db(workflow_id: str):
    return workflow_dir(workflow_id) / "workflow.db"

# One Chrome profile per workflow, kept outside the bundle - browser state is machine-local, not shareable
def browser_profile_dir(workflow_id: str):
    return DATA_DIR / "browser_profiles" / workflow_id

# Where a long step records each finished item, so it can continue without repeating them
def checkpoint_file(scope: str):
    d = DATA_DIR / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(scope))[:120] or "step"
    return d / f"{safe}.json"
BLOBS_DIR = DATA_DIR / "blobs"

LEARNINGS_DIR = DATA_DIR / "learnings"

APP_DB = DATA_DIR / "app.db"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
