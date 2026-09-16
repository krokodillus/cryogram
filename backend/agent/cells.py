# The registry of exploration runs - the recorded proof each built step is assembled from
from __future__ import annotations

from storage import blobstore as _bs

import json
import sqlite3
import time
from contextlib import closing
from typing import Any, Optional

import config

_INLINE_CAP = 64 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'code',
  code TEXT NOT NULL DEFAULT '',
  inputs TEXT NOT NULL DEFAULT 'null',
  output TEXT NOT NULL DEFAULT 'null',
  ok INTEGER NOT NULL DEFAULT 0,
  duration REAL NOT NULL DEFAULT 0,
  model TEXT,
  packages TEXT NOT NULL DEFAULT '[]',
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_name ON runs(name);
"""

_INSERT = ("INSERT INTO runs(name, kind, code, inputs, output, ok, duration, model, packages, ts, step_id, reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)")

def _db_path(workflow_id: str):
    return config.workflow_dir(workflow_id) / "cells.db"

def _conn(workflow_id: str) -> sqlite3.Connection:
    p = _db_path(workflow_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)

    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(_SCHEMA)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if "step_id" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN step_id TEXT")

    if "reason" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN reason TEXT")
    return conn

# Offloaded values stay as their blob references, never re-inlined
def _row_params(r: dict) -> tuple:
    return (r.get("name") or "", r.get("kind") or "code", r.get("code") or "",
            json.dumps(r.get("inputs"), default=str),
            json.dumps(r.get("output"), default=str),
            1 if r.get("ok") else 0, float(r.get("duration") or 0),
            r.get("model"), json.dumps(list(r.get("packages") or [])),
            float(r.get("ts") or 0), r.get("step_id"), r.get("reason") or None)

# Large recorded values move to the blob store, leaving an inline structural sketch
def _offload(value: Any, owner: str = "") -> Any:
    try:
        raw = json.dumps(value, default=str)
    except Exception:
        return value
    if len(raw) <= _INLINE_CAP:
        return value
    from storage import blobstore
    from agent import previews
    ref = blobstore.put(raw.encode(), owner=owner or blobstore.OWNER_APP)
    return {"$evidence_blob": ref, "sketch": previews.sketch(value)}

def _deref(value: Any) -> Any:
    if isinstance(value, dict) and "$evidence_blob" in value:
        from storage import blobstore
        try:
            data = blobstore.get(str(value["$evidence_blob"]))
            return json.loads(data.decode())
        except Exception:
            return {"$missing_evidence":
                    "the recorded value is no longer stored - re-run this step to refresh it",
                    "sketch": value.get("sketch")}
    return value

def _load_raw(workflow_id: str) -> list[dict]:
    if not _db_path(workflow_id).exists():
        return []
    with closing(_conn(workflow_id)) as conn:
        rows = conn.execute(
            "SELECT name, kind, code, inputs, output, ok, duration, model, packages, ts, step_id, reason FROM runs ORDER BY id").fetchall()
    out = []
    for (name, kind, code, inputs, output, ok, duration, model,
         packages, ts, step_id, reason) in rows:
        r = {"name": name, "kind": kind, "code": code,
             "inputs": json.loads(inputs), "output": json.loads(output),
             "ok": bool(ok), "duration": duration,
             "packages": json.loads(packages), "ts": ts}
        if model:
            r["model"] = model
        if step_id:
            r["step_id"] = step_id
        if reason:
            r["reason"] = reason
        out.append(r)
    return out

# Offloaded values come back real, or as their sketch when the blob is gone
def _load(workflow_id: str) -> list[dict]:
    runs = _load_raw(workflow_id)
    for r in runs:
        r["output"] = _deref(r.get("output"))
        r["inputs"] = _deref(r.get("inputs"))
    return runs

CAPTURES_KEPT = 4

# Lets go of the oldest windows' recordings past the newest CAPTURES_KEPT; the runs stay
def trim_captures(workflow_id: str, keep: int = CAPTURES_KEPT) -> int:
    owner = _bs.workflow_owner(workflow_id)
    with_caps = []
    for r in _load_raw(workflow_id):
        out = r.get("output")
        cap = out.get("$capture") if isinstance(out, dict) else None
        if isinstance(cap, dict):
            with_caps.append((r.get("ts") or 0, cap))
    with_caps.sort(key=lambda x: x[0], reverse=True)
    if len(with_caps) <= keep:
        return 0

    def refs(cap):
        out = [cap.get("har"), cap.get("snapshot"), cap.get("calls_file")]
        out += [pg.get("ref") for pg in cap.get("pages") or [] if isinstance(pg, dict)]
        return {r for r in out if isinstance(r, str) and r}
    held = set()
    for _ts, cap in with_caps[:keep]:
        held |= refs(cap)
    gone = 0
    for _ts, cap in with_caps[keep:]:
        for ref in refs(cap) - held:
            try:
                if _bs.forget(ref, owner):
                    gone += 1
            except Exception:
                continue
    return gone

# Saves one test run: the code, inputs and result that later prove the step
def record(workflow_id: str, name: str, code: str, inputs: dict,
           output: Any, ok: bool, duration: float,
           packages: Optional[list] = None, kind: str = "code",
           model: Optional[str] = None, step_id: Optional[str] = None,
           reason: str = "") -> int:
    run = {"name": name, "kind": kind, "code": code,
           "inputs": _offload(inputs, _bs.workflow_owner(workflow_id)),
           "output": _offload(output, _bs.workflow_owner(workflow_id)),
           "ok": bool(ok), "duration": round(duration, 2),
           "model": model, "packages": list(packages or []),
           "ts": time.time(), "step_id": step_id or None,
           "reason": str(reason or "").strip()}
    with closing(_conn(workflow_id)) as conn:
        with conn:
            conn.execute(_INSERT, _row_params(run))
        if step_id:
            n = conn.execute("SELECT COUNT(*) FROM runs WHERE step_id=? OR name=?",
                             (step_id, name)).fetchone()[0]
        else:
            n = conn.execute("SELECT COUNT(*) FROM runs WHERE name=?",
                             (name,)).fetchone()[0]
    if isinstance(output, dict) and isinstance(output.get("$capture"), dict):
        trim_captures(workflow_id)
    return n

# Rows recorded under a name before the step had an id learn the id, so a later rename keeps them
def claim(workflow_id: str, name: str, step_id: str) -> int:
    if not name or not step_id or not _db_path(workflow_id).exists():
        return 0
    with closing(_conn(workflow_id)) as conn:
        with conn:
            cur = conn.execute("UPDATE runs SET step_id=? WHERE name=? AND step_id IS NULL",
                               (step_id, name))
            return cur.rowcount

def _is(r: dict, name: str, step_id: Optional[str]) -> bool:
    return bool(step_id and r.get("step_id") == step_id) or r.get("name") == name

# The most recent successful run of a step's code - what the build assembles from
def latest_ok(workflow_id: str, name: str,
              kind: Optional[str] = None,
              step_id: Optional[str] = None) -> Optional[dict]:
    for r in reversed(_load(workflow_id)):
        if _is(r, name, step_id) and r.get("ok") \
                and (kind is None or r.get("kind", "code") == kind):
            return r
    return None

# The step's latest successful run, looked up by its id first and its name second
def latest_ok_for(workflow_id: str, step: dict,
                  kind: Optional[str] = None) -> Optional[dict]:
    step = step or {}
    return latest_ok(workflow_id, str(step.get("name") or ""), kind=kind,
                     step_id=str(step.get("id") or "") or None)

# Every successful recording's code for one cell, newest first - the plan-lag heal compares against these
def ok_codes(workflow_id: str, name: str, kind: str = "code",
             step_id: Optional[str] = None) -> list:
    out = []
    for r in reversed(_load(workflow_id)):
        if _is(r, name, step_id) and r.get("ok") \
                and r.get("kind", "code") == kind:
            out.append(r.get("code") or "")
    return out

# What a window kept, by the names a later cell chains them under
def capture_refs(output: Any) -> dict:
    cap = output.get("$capture") if isinstance(output, dict) else None
    if not isinstance(cap, dict):
        return {}
    out = {}
    for name, ref in (("har", cap.get("har")),
                      ("page", cap.get("snapshot")),
                      ("calls", cap.get("calls_file"))):
        if isinstance(ref, str) and ref and _bs.exists(ref):
            out[name] = ref
    return out

# Every browser window this build opened that kept a recording, numbered in order, with what it was for
def windows(workflow_id: str) -> list[dict]:
    out: list[dict] = []
    for r in _load(workflow_id):
        if not (r.get("ok") and isinstance(r.get("output"), dict)
                and "$missing_evidence" not in r["output"]):
            continue
        kept = capture_refs(r["output"])
        if kept:
            out.append({"n": len(out) + 1, "name": r.get("name") or "",
                        "reason": r.get("reason") or "", "captured": kept})
    return out

# Every window recording this workflow is holding, as (name, reference) pairs
def capture_held(workflow_id: str) -> list[tuple]:
    seen: list[tuple] = []
    for r in _load(workflow_id):
        cap = r.get("output") if isinstance(r.get("output"), dict) else {}
        cap = cap.get("$capture") if isinstance(cap.get("$capture"), dict) else None
        if not isinstance(cap, dict):
            continue
        refs = [cap.get("har"), cap.get("snapshot"), cap.get("calls_file")]
        refs += [pg.get("ref") for pg in cap.get("pages") or []
                 if isinstance(pg, dict)]
        for ref in refs:
            if isinstance(ref, str) and ref:
                seen.append((r.get("name") or "", ref))
    return seen

# Lets go of the traffic and page copies the build's windows kept; the recorded runs stay as they are
def forget_captures(workflow_id: str) -> int:
    owner = _bs.workflow_owner(workflow_id)
    gone = 0

    for ref in dict.fromkeys(r for _name, r in capture_held(workflow_id)):
        try:
            if _bs.forget(ref, owner):
                gone += 1
        except Exception:
            continue
    return gone

# The latest successful outputs by name - what recorded-input chaining reads from
def latest_outputs(workflow_id: str) -> dict:
    out: dict = {}
    for r in _load(workflow_id):
        if r.get("ok") and isinstance(r.get("output"), dict) \
                and "$missing_evidence" not in r["output"]:
            out.update({k: v for k, v in r["output"].items()
                        if not str(k).startswith("$")})
            out.update(capture_refs(r["output"]))

    for w in windows(workflow_id):
        out.update({f"{k}#{w['n']}": ref for k, ref in w["captured"].items()})
    return out

# Names of everything already proven, listed in the turn context so recorded work is never redone
def inventory(workflow_id: str) -> list[dict]:
    out: dict[str, dict] = {}
    window = 0
    for r in _load(workflow_id):
        nm = r.get("name")
        if not nm:
            continue
        key = r.get("step_id") or nm
        seen = out.setdefault(key, {"name": nm, "kind": r.get("kind", "code"),
                                    "outputs": [], "tries": 0})
        seen["name"] = nm
        if r.get("step_id"):
            seen["id"] = r["step_id"]
        seen["tries"] += 1
        if r.get("ok") and isinstance(r.get("output"), dict) \
                and "$missing_evidence" not in r["output"]:
            seen["kind"] = r.get("kind", "code")
            seen["outputs"] = sorted(k for k in r["output"] if not str(k).startswith("$"))

            kept = sorted(capture_refs(r["output"]))
            if kept:
                seen["captured"] = kept
                window += 1
                seen.setdefault("windows", []).append(
                    {"n": window, "reason": r.get("reason") or "", "captured": kept})
    return [v for v in out.values() if v["outputs"] or v.get("captured")]

def names(workflow_id: str) -> list[str]:
    seen: list[str] = []
    for r in _load(workflow_id):
        if r["name"] not in seen:
            seen.append(r["name"])
    return seen
