# The pause channel: a question or approval blocks the turn here until the user answers or it parks
from __future__ import annotations

import os
import threading
import uuid
from typing import Any, Optional

CARD_PATIENCE = float(os.environ.get("CRYOGRAM_CARD_PATIENCE", "1500"))

_WAKE_SLICE = 0.5

_lock = threading.Lock()
_pending: dict[str, dict] = {}

# Registers a question from a synchronous turn; the answer arrives on another thread and wakes the waiter
def create_sync(kind: str, payload: dict, iid: Optional[str] = None) -> str:
    iid = iid or f"int_{uuid.uuid4().hex[:8]}"
    with _lock:
        _pending[iid] = {"kind": kind, "payload": dict(payload or {}),
                         "workflow_id": (payload or {}).get("workflow_id"),
                         "event": threading.Event(), "answer": None}
    return iid

# Blocks the turn until the user answers or the patience window ends; a late answer still reaches a parked card
def wait_sync(iid: str, timeout: Optional[float] = None, on_tick=None) -> Optional[Any]:
    import time as _time
    with _lock:
        entry = _pending.get(iid)
    if entry is None or "event" not in entry:
        return None
    limit = CARD_PATIENCE if timeout is None else timeout
    pid = entry.get("workflow_id") or ""
    deadline = _time.monotonic() + limit
    while not entry["event"].wait(min(_WAKE_SLICE, max(0.0, limit))):
        if _time.monotonic() >= deadline:
            break
        if on_tick:
            try:
                on_tick()
            except Exception:
                pass
        if pid:
            try:
                import turns
                if turns.should_stop(pid):
                    break
            except Exception:
                pass
    with _lock:
        _pending.pop(iid, None)
        return entry.get("answer")

# Parks unanswered questions whose turn is gone, so a late click rides the recovery path instead of a dead thread
def park_workflow(workflow_id: str) -> list[str]:
    parked = []
    with _lock:
        for iid, entry in list(_pending.items()):
            if entry.get("workflow_id") == workflow_id and "event" in entry:
                _pending.pop(iid, None)
                entry["answer"] = None
                entry["event"].set()
                parked.append(iid)
    return parked

# Writes the answer under the lock, so a click can never race the timeout and vanish
def resolve(iid: str, answer: Any) -> bool:
    with _lock:
        entry = _pending.get(iid)
    if entry is None:
        return False

    try:
        from agent import chatlog
        payload = entry.get("payload") or {}
        pid = entry.get("workflow_id")
        if pid:
            shown = "(provided)" if payload.get("secret") else _log_shape(answer)
            chatlog.append(pid, f"answer:{entry.get('kind', 'ask')}",
                           {"question": payload.get("title") or payload.get("question", ""),
                            "answer": shown})
    except Exception:
        pass

    with _lock:
        if iid not in _pending:
            return False
        entry["answer"] = answer
    entry["event"].set()
    return True

# Logs an answer's text field; secret-bearing answers never reach here
def _log_shape(answer: Any):
    if isinstance(answer, dict):
        return str(answer.get("text") or "")[:300]
    return answer

# The open questions, for a page that reloads mid-turn
def pending(workflow_id: Optional[str] = None) -> list[dict]:
    with _lock:
        return [{"id": iid, "kind": e["kind"], "payload": e["payload"]}
                for iid, e in _pending.items()
                if workflow_id is None or e.get("workflow_id") == workflow_id]
