# In-flight turn registry: one turn or run per workflow at a time, buffered so a reloaded page can catch up
from __future__ import annotations

import threading
import time
from typing import Optional

_lock = threading.Lock()
_turns: dict[str, dict] = {}

_EVENT_CAP = 500
_TEXT_CAP = 400_000

# Claims the workflow's one turn slot; False means something is already running and the caller answers 409
def begin(workflow_id: str, kind: str) -> bool:
    with _lock:
        cur = _turns.get(workflow_id)
        if cur and cur["status"] == "running":
            return False
        import uuid
        _turns[workflow_id] = {"kind": kind, "status": "running",
                              "turn_id": f"t_{uuid.uuid4().hex[:8]}",
                              "started": time.time(), "seq": 0, "text": "",
                              "events": [], "truncated": False, "error": None,
                              "stop_requested": False, "emit": None}
        return True

# Binds the running turn's stream writer, so every transcript append is shown through it
def bind_emit(workflow_id: str, fn) -> None:
    with _lock:
        t = _turns.get(workflow_id)
        if t and t["status"] == "running":
            t["emit"] = fn

# Releases the writer at turn end; appends outside a turn are shown by the route that made them
def unbind_emit(workflow_id: str) -> None:
    with _lock:
        t = _turns.get(workflow_id)
        if t:
            t["emit"] = None

# Hands a freshly appended transcript item to the running turn's writer; a no-op with no turn
def shown(workflow_id: str, item: dict) -> bool:
    with _lock:
        t = _turns.get(workflow_id)
        fn = t.get("emit") if t and t["status"] == "running" else None
    if not fn:
        return False
    fn({"type": "shown", "item": item})
    return True

# A stop request carries the id of the turn it targets, so it cannot apply to a newer turn that just took the slot
def request_stop(workflow_id: str, turn_id: Optional[str] = None) -> bool:
    with _lock:
        t = _turns.get(workflow_id)
        if not t or t["status"] != "running":
            return False
        if turn_id and t.get("turn_id") != turn_id:
            return False
        t["stop_requested"] = True
        return True

# The running turn's id, so a Stop can name exactly what it is stopping
def current_turn_id(workflow_id: str) -> str:
    with _lock:
        t = _turns.get(workflow_id)
        return (t.get("turn_id", "") if t and t["status"] == "running" else "")

# Polled between steps; True once a stop was requested for this turn
def should_stop(workflow_id: str) -> bool:
    with _lock:
        t = _turns.get(workflow_id)
        return bool(t and t.get("stop_requested"))

# Saves each streamed event in memory so a page that reloads mid-turn can catch up by polling
def record(workflow_id: str, ev: dict) -> None:
    with _lock:
        t = _turns.get(workflow_id)
        if not t or t["status"] != "running":
            return
        t["seq"] += 1
        if ev.get("type") == "delta":
            t["text"] = (t["text"] + ev.get("text", ""))[-_TEXT_CAP:]
            return
        if ev.get("type") == "done":
            ev = {"type": "done",
                  **{k: ev[k] for k in ("error", "build", "fix", "result",
                                        "unread", "stopped", "asked")
                     if ev.get(k)}}
        t["events"].append({**ev, "seq": t["seq"]})
        if len(t["events"]) > _EVENT_CAP:
            t["events"] = t["events"][-_EVENT_CAP:]
            t["truncated"] = True

def finish(workflow_id: str, error: Optional[str] = None) -> None:
    with _lock:
        t = _turns.get(workflow_id)
        if not t:
            return
        t["status"] = "done"
        t["error"] = error

# True while any workflow has a turn or run in flight; the updater checks this so an update never ends a running build
def any_running() -> bool:
    with _lock:
        return any(t.get("status") == "running" for t in _turns.values())

# The polling view: the full streamed text so far plus the events newer than the caller's cursor
def snapshot(workflow_id: str, after: int = 0) -> dict:
    with _lock:
        t = _turns.get(workflow_id)
        if not t:
            return {"active": False, "last_seq": 0, "events": [], "text": ""}

        return {"active": t["status"] == "running", "kind": t["kind"],
                "turn_id": t.get("turn_id", ""),
                "started": t["started"], "truncated": t["truncated"],
                "error": t["error"], "text": t["text"],
                "events": [dict(e) for e in t["events"] if e["seq"] > after],
                "last_seq": t["seq"]}

# The turn kinds that land a parked reply; anything else holding the slot answers "busy" instead
LANDING_KINDS = ("chat", "investigate")

# The kind of the turn holding the slot, or "" when nothing runs
def running_kind(workflow_id: str) -> str:
    with _lock:
        t = _turns.get(workflow_id)
        return str(t["kind"]) if t and t["status"] == "running" else ""

# A reply sent while a turn runs is parked on it: landed in the transcript on the turn's next event, read at its next tool boundary
def interject(workflow_id: str, entry: dict) -> bool:
    with _lock:
        t = _turns.get(workflow_id)
        if not t or t["status"] != "running" or t["kind"] not in LANDING_KINDS:
            return False
        t.setdefault("interjections", []).append(
            {**dict(entry or {}), "landed": False, "read": False})
        return True

# Entries not yet in the transcript; the turn thread lands them on its next event
def take_interjections_to_land(workflow_id: str) -> list:
    with _lock:
        t = _turns.get(workflow_id)
        out = []
        for it in (t or {}).get("interjections") or []:
            if not it["landed"]:
                it["landed"] = True
                out.append(it)
        return out

# The driving texts the model has not read yet, consumed at a tool boundary
def take_interjections(workflow_id: str) -> list:
    with _lock:
        t = _turns.get(workflow_id)
        out = []
        for it in (t or {}).get("interjections") or []:
            if not it["read"]:
                it["read"] = True
                out.append(it.get("drive") or it.get("text") or "")
        return out

# What was said meanwhile and never consumed - the server drives the next turn on it
def unread_interjections(workflow_id: str) -> list:
    with _lock:
        t = _turns.get(workflow_id)
        return [it.get("drive") or it.get("text") or ""
                for it in (t or {}).get("interjections") or []
                if not it["read"]]

# The text streamed since the last flush - persisted on failure so an explanation never vanishes
def partial_text(workflow_id: str) -> str:
    with _lock:
        t = _turns.get(workflow_id)
        return (t["text"] if t else "").strip()

# Hands over the text streamed since the last flush, so a card lands below the words that introduced it
def flush_text(workflow_id: str) -> str:
    with _lock:
        t = _turns.get(workflow_id)
        if not t:
            return ""
        txt = t["text"].strip()
        t["text"] = ""
        return txt
