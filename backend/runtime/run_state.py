# The resume buffer: completed steps checkpoint here so a paused run continues without repeating what already ran
from __future__ import annotations
import threading
import time

import config
from typing import Any, Optional

from storage import db

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()

def _load(run_id: str) -> Optional[dict]:
    with _cache_lock:
        rec = _cache.get(run_id)
    if rec is not None:
        return rec
    rec = db.runstate_get(run_id)
    if rec is not None:
        with _cache_lock:
            _cache[run_id] = rec
    return rec

def _save(rec: dict) -> None:
    with _cache_lock:
        _cache[rec["run_id"]] = rec
    db.runstate_put(rec)

def _drop_cached(run_id: str) -> None:
    with _cache_lock:
        _cache.pop(run_id, None)

# Remembers what an approval popup showed for a step, so the yes is for those values
def note_preview(run_id: str, node_id: str, digest: str) -> None:
    rec = _load(run_id)
    if rec is None:
        return
    rec.setdefault("previews", {})[node_id] = digest
    _save(rec)

def preview_digest(run_id: str, node_id: str) -> Optional[str]:
    rec = _load(run_id)
    return ((rec or {}).get("previews") or {}).get(node_id)

# Startup cleanup: buffers that are not sitting at a resumable pause are deleted
def sweep_stale(days: int = 30) -> int:
    cutoff = time.time() - days * 86400
    n = 0
    live = set()
    for rec in db.runstate_all():
        if rec.get("status") == "running" and rec.get("started", 0) >= cutoff:
            rec["status"] = "halted"
            rec["halted_at"] = rec.get("halted_at") or ""
            rec["reason"] = "interrupted"
            rec["paused"] = True
            _save(rec)
            live.add(rec["run_id"])
            continue
        if rec.get("status") != "halted" or rec.get("started", 0) < cutoff:
            db.runstate_delete(rec["run_id"])
            _drop_cached(rec["run_id"])
            n += 1
        else:
            live.add(rec["run_id"])
    _sweep_checkpoints(live)
    return n

# An item ledger belongs to a paused run; once the buffer is gone it is dropped with it
def _sweep_checkpoints(live_runs: set) -> None:
    import config
    d = config.DATA_DIR / "checkpoints"
    if not d.is_dir():
        return
    for f in d.glob("run_*.json"):
        run_id = "_".join(f.stem.split("_")[:2])
        if run_id not in live_runs:
            try:
                f.unlink()
            except OSError:
                pass

def start(workflow_id: str, run_id: str, entry_inputs: Optional[dict] = None) -> dict:
    rec = {"run_id": run_id, "workflow_id": workflow_id, "status": "running",
           "started": time.time(), "entry_inputs": entry_inputs or {},
           "steps": {}, "path": [], "halted_at": None, "reason": None}
    _save(rec)
    return rec

# Saves a completed step's output to app.db so a resume reads it instead of re-running the step
def record_step(run_id: str, node_id: str, output: Any, fired: bool = False,
                usage: Any = None, trace: Any = None) -> None:
    rec = _load(run_id)
    rec["steps"][node_id] = {"output": output, "fired": bool(fired), "ts": time.time()}
    if usage:
        rec["steps"][node_id]["usage"] = usage
    if trace:
        rec["steps"][node_id]["trace"] = trace
    if node_id not in rec["path"]:
        rec["path"].append(node_id)
    rec["status"] = "running"
    rec["halted_at"] = rec["reason"] = None
    rec.pop("progress", None)
    _save(rec)

# Remembers that a step did not run this time, and why, so the run's record can say so
def note_skip(run_id: str, node_id: str, reason: str) -> None:
    rec = _load(run_id)
    if rec is None:
        return
    rec.setdefault("skips", {})[node_id] = {"reason": str(reason or ""),
                                            "ts": time.time()}
    _save(rec)

# The run's step story so far, in order: what ran with its previews, and what was skipped
def trace(rec) -> list:
    if not isinstance(rec, dict):
        return []
    out = []
    steps = rec.get("steps") or {}
    for nid, s in steps.items():
        e: dict = {"node": nid, "status": "completed",
                   "_ts": s.get("ts") or 0}
        t = s.get("trace") or {}
        for k in ("inputs", "output", "notes"):
            if t.get(k) is not None:
                e[k] = t[k]
        if t.get("entered_by_user"):
            e["entered_by_user"] = True
        out.append(e)
    for nid, sk in (rec.get("skips") or {}).items():
        if nid in steps:
            continue
        out.append({"node": nid, "status": "skipped",
                    "reason": sk.get("reason") or "", "_ts": sk.get("ts") or 0})
    out.sort(key=lambda e: e["_ts"])
    for e in out:
        e.pop("_ts", None)
    return out

# Records the items a step set aside this run, so a resume keeps them and the manifest carries them
def note_set_aside(run_id: str, node_id: str, policy: str, entries: list) -> None:
    rec = _load(run_id)
    if rec is None:
        return
    rec.setdefault("set_aside", {})[node_id] = {"policy": policy,
                                                "ports": list(entries or [])}
    _save(rec)

# A send is recorded the moment it fires, so a resume can never repeat a write that already happened
def record_fired(run_id: str, node_id: str) -> None:
    rec = _load(run_id)
    if rec is not None:
        rec.setdefault("fired", {})[node_id] = time.time()
        _save(rec)

# Only the user's "it did not arrive" answer may clear a fired marker
def clear_fired(run_id: str, node_id: str) -> None:
    rec = _load(run_id)
    if rec is not None and node_id in rec.get("fired", {}):
        rec["fired"].pop(node_id, None)
        _save(rec)

# Run the whole step again: the fired mark, the item ledger and the progress note go, on the person's word that the other side was cleaned up
def reset_step(run_id: str, node_id: str) -> None:
    clear_fired(run_id, node_id)
    try:
        config.checkpoint_file(f"{run_id}_{node_id}").unlink(missing_ok=True)
    except OSError:
        pass
    rec = _load(run_id)
    if rec is not None and rec.get("halted_at") == node_id and rec.get("progress"):
        rec.pop("progress", None)
        _save(rec)

def has_fired(run_id: str, node_id: str) -> bool:
    rec = _load(run_id)
    return bool(rec and node_id in rec.get("fired", {}))

def output_of(run_id: str, node_id: str) -> Any:
    rec = _load(run_id)
    step = rec["steps"].get(node_id) if rec else None
    return step["output"] if step else None

# Completed in this run - skipped on resume, and never re-entered after an irreversible action
def is_done(run_id: str, node_id: str) -> bool:
    rec = _load(run_id)
    return bool(rec and node_id in rec.get("steps", {}))

def entry_inputs(run_id: str) -> dict:
    rec = _load(run_id)
    return rec.get("entry_inputs", {}) if rec else {}

# Late-supplied values fold into the paused run's inputs; the buffer dies with the run, so run-only scope is free
def merge_entry(run_id: str, extra: dict) -> None:
    rec = _load(run_id)
    if rec and extra:
        rec.setdefault("entry_inputs", {}).update(extra)
        _save(rec)

# A paused run is doing one of three things: asking you, ended by you, or stopped on a problem
ASKING = ("awaiting-approval", "missing-value", "input-needed",
          "send-unverified", "fired-unverified",
          "browser-closed", "not-confirmed")
USER_ENDED = ("stopped-by-user", "approval-declined")

# True when the run is waiting for an answer from the user, not on a problem
def waiting_on_you(reason) -> bool:
    return reason in ASKING

def halt(run_id: str, node_id: str, reason: str) -> None:
    rec = _load(run_id)
    rec["status"] = "halted"
    rec["halted_at"] = node_id
    rec["reason"] = reason
    rec["waiting_on_you"] = waiting_on_you(reason)

    rec["paused"] = True

    rec["halt_ts"] = time.time()

    rec.pop("verdict", None)
    _save(rec)

# How far a per-item step got before it stopped: which items are done, which remain, where the list came from
def note_progress(run_id: str, progress: dict) -> None:
    rec = _load(run_id)
    if rec is not None:
        if progress:
            rec["progress"] = progress
        else:
            rec.pop("progress", None)
        _save(rec)

# Proceed with the saved results: the run splits into the half that is done and the half that is not
def fork_partial(run_id: str) -> Optional[tuple[str, Optional[str]]]:
    import copy
    import uuid
    rec = _load(run_id)
    prog = (rec or {}).get("progress") or {}
    if (not rec or rec.get("status") != "halted" or not prog
            or not prog.get("done")):
        return None
    name, key = prog.get("input"), prog.get("key")
    done_keys = set(str(k) for k in prog.get("done_keys") or [])

    def _key(item):
        if isinstance(item, dict) and key:
            return str(item.get(key))
        return str(item)

    def _split(items):
        done = [x for x in items if _key(x) in done_keys]
        rest = [x for x in items if _key(x) not in done_keys]
        return done, rest

    src = prog.get("source") or {}
    items = None
    if isinstance(src, dict) and src.get("step") and src["step"] in rec.get("steps", {}):
        out = rec["steps"][src["step"]].get("output")
        if isinstance(out, dict) and isinstance(out.get(name), list):
            items = out[name]
            where = ("step", src["step"])
    if items is None and isinstance((rec.get("entry_inputs") or {}).get(name), list):
        items = rec["entry_inputs"][name]
        where = ("entry", None)
    if items is None and isinstance(prog.get("items"), list):
        items = prog["items"]
        where = ("variable", None)
    if items is None:
        return None
    done, rest = _split(items)
    if not done:
        return None

    def _rewrite(r: dict, subset: list) -> None:
        if where[0] == "step":
            r["steps"][where[1]]["output"][name] = subset
        elif where[0] == "entry":
            r.setdefault("entry_inputs", {})[name] = subset
        else:
            r.setdefault("overrides", {})[name] = subset

    total = len(items)
    sibling = None
    if rest:
        sibling = f"run_{uuid.uuid4().hex[:8]}"
        b = copy.deepcopy(rec)
        b["run_id"] = sibling
        b.pop("progress", None)
        b.pop("fired", None)
        _rewrite(b, rest)
        b["partial"] = {"half": "remaining", "count": len(rest), "of": total,
                        "sibling": run_id}
        _save(b)
    _rewrite(rec, done)
    rec.pop("progress", None)
    rec["partial"] = {"half": "done", "count": len(done), "of": total,
                      "sibling": sibling}
    _save(rec)
    return run_id, sibling

# The user has seen this run's outcome; the page stops reopening it on arrival
def mark_seen(run_id: str) -> bool:
    rec = _load(run_id)
    if rec is None:
        return False
    rec["seen"] = True
    _save(rec)
    return True

# The user's No on the approval popup: the run ends there but stays in Run history to pick up later
def decline_approval(run_id: str) -> bool:
    rec = _load(run_id)
    if (not rec or rec.get("status") != "halted"
            or rec.get("reason") != "awaiting-approval"):
        return False
    rec["reason"] = "approval-declined"
    _save(rec)
    return True

# Keeps a pause's user-facing explanation on the buffer, so the banner can show it after a reload
def note_verdict(run_id: str, verdict: dict) -> None:
    rec = _load(run_id)
    if rec is not None and verdict:
        rec["verdict"] = verdict
        _save(rec)

# The workflow's most recent run that is waiting for an answer from the user, if one is still standing
def latest_halt(workflow_id: str) -> Optional[dict]:
    def _ts(rec):
        return rec.get("halt_ts") or rec.get("started") or 0
    best = None
    for rec in db.runstate_all():
        if rec.get("workflow_id") == workflow_id and rec.get("status") == "halted":
            if not waiting_on_you(rec.get("reason")):
                continue
            if best is None or _ts(rec) > _ts(best):
                best = rec
    return best

# A completed run's buffer is deleted; nothing about a run outlives it here
def finish(run_id: str, discard: bool = True) -> list[str]:
    rec = _load(run_id)
    path = rec["path"] if rec else []
    if rec:
        rec["status"] = "done"
        _save(rec)
    if discard:
        db.runstate_delete(run_id)
        _drop_cached(run_id)
    return path

def load(run_id: str) -> Optional[dict]:
    return _load(run_id)
