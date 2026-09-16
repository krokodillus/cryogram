# The update check: reads the latest release from the public repository and says whether it is newer
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Optional

import config

# Some Python installs come without CA certificates set up, so the certifi bundle is used when it is available
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = None

# Every request has a timeout, so a slow server can delay the check but cannot block the app
FETCH_TIMEOUT = 30

_checked: Optional[dict] = None

def _update_url() -> str:
    env = os.environ.get("CRYOGRAM_UPDATE_URL")
    if env:
        return env
    from storage import settings
    return ((settings.get().get("update") or {}).get("url") or "").strip()

def _version_tuple(v: str) -> tuple:
    return config.version_tuple(v)

def _http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT,
                                context=_SSL_CTX) as r:
        return json.loads(r.read().decode())

# Whether a newer version is published; an unreachable or odd answer counts as no update
def check(force: bool = False) -> dict:
    global _checked
    if config.BUILD == "dev":
        return {"available": False, "note": "dev checkout",
                "channel_kind": "dev"}
    kind = "source"
    if not force:
        from storage import settings
        if not (settings.get().get("preferences") or {}).get("check_updates", True):
            return {"available": False, "note": "checks are turned off",
                    "channel_kind": kind}
    if _checked is not None and not force:
        return _checked
    url = _update_url()
    if not url:
        _checked = {"available": False, "note": "no update source set",
                    "channel_kind": kind}
        return _checked
    try:
        v = _http_json(url)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"available": False, "note": f"check failed ({e})",
                "channel_kind": kind}
    if not isinstance(v, dict):
        return {"available": False, "note": "check failed (shape)",
                "channel_kind": kind}
    latest = str(v.get("tag_name") or "").lstrip("v")
    _checked = {
        "available": bool(latest) and
        _version_tuple(latest) > _version_tuple(config.VERSION),
        "latest": latest,
        "changelog": str(v.get("body") or ""),
        "page": str(v.get("html_url") or ""),
        "channel_kind": kind,
    }
    return _checked
