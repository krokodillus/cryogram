# The per-workflow debug log: what happened and when, exportable, rotated, secrets scrubbed
from __future__ import annotations

import json
import os
import time

import config

_SKIP = {"delta"}
_MAX_BYTES = 5 * 1024 * 1024

def append(workflow_id: str, kind: str, data: dict) -> None:
    try:
        d = config.DATA_DIR / "logs"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{workflow_id}.jsonl"
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            os.replace(path, d / f"{workflow_id}.1.jsonl")
        rec = {"ts": time.time(), "kind": kind, **(data or {})}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass

# Rewrites the log files to remove a value that should never have been in them
def redact(workflow_id: str, value: str, replacement: str) -> None:
    try:
        if not value:
            return
        d = config.DATA_DIR / "logs"
        forms = [(value, replacement)]
        esc = json.dumps(value)[1:-1]
        if esc != value:
            forms.append((esc, json.dumps(replacement)[1:-1]))
        for path in (d / f"{workflow_id}.jsonl", d / f"{workflow_id}.1.jsonl"):
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            new = text
            for a, b in forms:
                new = new.replace(a, b)
            if new != text:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(new, encoding="utf-8")
                os.replace(tmp, path)
    except Exception:
        pass

def event(workflow_id: str, ev: dict) -> None:
    if (ev or {}).get("type") in _SKIP:
        return
    e = dict(ev)

    if isinstance(e.get("workflow"), dict) and "chat" in e["workflow"]:
        e["workflow"] = {k: v for k, v in e["workflow"].items() if k != "chat"}
    append(workflow_id, f"event:{e.pop('type', '?')}", e)
