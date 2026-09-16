# The working memory: what each builder turn said, called and got back, kept whole and handed to the next turn
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Optional

from storage import db

TRAIL_WINDOW_CHARS = 360_000

_live: dict[str, dict] = {}
_lock = threading.Lock()

# Opens a turn's trail with the message that started it
def begin(workflow_id: str, turn_id: Optional[str], user_message: str,
          kind: str = "chat") -> str:
    tid = str(turn_id or f"t_{uuid.uuid4().hex[:8]}")
    with _lock:
        _live[workflow_id] = {"turn_id": tid, "seg": []}
    db.trail_add(workflow_id, tid, "user",
                 {"text": str(user_message or ""), "turn_kind": str(kind or "chat")})
    return tid

def _flush(workflow_id: str) -> None:
    with _lock:
        live = _live.get(workflow_id)
        seg = "".join(live["seg"]) if live else ""
        if live:
            live["seg"] = []
    if live and seg.strip():
        db.trail_add(workflow_id, live["turn_id"], "text", {"text": seg})

# A fragment of the model's own text; segments flush whole at the next tool call or the turn's end
def text(workflow_id: str, frag: str) -> None:
    if not frag:
        return
    with _lock:
        live = _live.get(workflow_id)
        if live:
            live["seg"].append(frag)

# A tool call and the envelope the model got back, exactly as it got it; answers the item's id
def tool(workflow_id: str, name: str, args: dict, envelope) -> Optional[int]:
    with _lock:
        live = _live.get(workflow_id)
    if not live:
        return None
    _flush(workflow_id)
    item = {"name": str(name or ""), "args": args if isinstance(args, dict) else {},
            "result": envelope}
    rid = db.trail_add(workflow_id, live["turn_id"], "tool", item)

    if isinstance(envelope, dict):
        db.trail_set_payload(workflow_id, rid, {**item, "result": {**envelope, "result_id": rid}})
    return rid

# Closes the turn's trail; every turn stays on disk, there is no keep count
def end(workflow_id: str) -> None:
    _flush(workflow_id)
    with _lock:
        _live.pop(workflow_id, None)

# The one line an earlier tool result becomes: which it was, whether it passed, its size and what it held
def _result_line(it: dict) -> str:
    res = it.get("result")
    size = len(json.dumps(res, default=str))
    ok = not (isinstance(res, dict) and (res.get("ok") is False or res.get("error")))
    held = []
    data = res.get("data") if isinstance(res, dict) else None
    if isinstance(data, dict):
        out = data.get("output")
        if isinstance(out, dict):
            held.append("outputs: " + ", ".join(str(k) for k in out))
        rest = [str(k) for k in data if k != "output"]
        if rest:
            held.append("also: " + ", ".join(rest))
    return (f"result #{it.get('id')}: {'passed' if ok else 'failed'}, {size:,} characters"
            + (f"; {'; '.join(held)}" if held else "")
            + f" - open it with read_earlier_result({it.get('id')})")

def _render_item(it: dict) -> str:
    k = it.get("kind")
    if k == "user":
        return "user: " + str(it.get("text") or "")
    if k == "text":
        return "you said: " + str(it.get("text") or "")
    if k == "tool":
        head = ("you called " + str(it.get("name") or "")
                + " with " + json.dumps(it.get("args") or {}, default=str))

        if str(it.get("name") or "") == "load_skill":
            return head + "\nresult: " + json.dumps(it.get("result"), default=str)
        return head + "\n" + _result_line(it)
    return ""

def _when(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError):
        return "?"

# The newest turns, whole, for the next turn's opening; returns the text and when the replayed span starts
def replay(workflow_id: str, window: Optional[int] = None) -> tuple[str, Optional[float], str]:
    if window is None:
        window = TRAIL_WINDOW_CHARS
    with _lock:
        live_id = (_live.get(workflow_id) or {}).get("turn_id")
    try:
        turns = [t for t in db.trail_turns(workflow_id) if t["turn_id"] != live_id]
    except Exception:
        return "", None, ""
    if not turns:
        return "", None, ""
    blocks: list[tuple[dict, list[str]]] = []
    spent = 0
    trimmed_note = ""
    for t in reversed(turns):
        items = db.trail_items(workflow_id, t["turn_id"])
        lines = [_render_item(i) for i in items]
        lines = [l for l in lines if l]
        size = sum(len(l) + 1 for l in lines)
        if spent + size <= window:
            blocks.append((t, lines))
            spent += size
            continue
        if not blocks:
            kept: list[str] = []
            for l in reversed(lines):
                if spent + len(l) + 1 > window:
                    break
                kept.insert(0, l)
                spent += len(l) + 1
            dropped = len(lines) - len(kept)
            if dropped:
                kept.insert(0, f"[the first {dropped} items of this turn are "
                               "not shown - the window is full]")
            blocks.append((t, kept))
        older = len(turns) - len(blocks)
        if older > 0:
            trimmed_note = (f"[{older} earlier turn{'s' if older != 1 else ''} "
                            "not replayed in full - the conversation replay below covers what the user saw there]")
        break
    blocks.reverse()
    out = ["\n"
           "[working memory] What you did in your most recent turns - your own words and every tool call as you made it. Each result is one line saying what it held; open one exactly as you saw it with read_earlier_result(id) rather than doing the same work again. A guide you loaded is shown whole."]
    if trimmed_note:
        out.append(trimmed_note)
    for t, lines in blocks:
        out.append(f"=== earlier turn, started {_when(t['ts'])} ===")
        out.extend(lines)
    out.append("=== end of working memory ===\n")
    first = db.trail_items(workflow_id, blocks[0][0]["turn_id"])
    first_msg = str(first[0].get("text") or "") if first and first[0].get("kind") == "user" else ""
    return "\n".join(out), float(blocks[0][0]["ts"]), first_msg

# An earlier tool result exactly as the model received it, by its trail id
def result_of(workflow_id: str, item_id) -> Optional[dict]:
    try:
        it = db.trail_item(workflow_id, int(item_id))
    except (TypeError, ValueError):
        return None
    if not it or it.get("kind") != "tool":
        return None
    return {"id": it["id"], "tool": it.get("name"), "args": it.get("args"),
            "result": it.get("result")}

# Scrubs a value the user pasted into chat from every stored item
def redact(workflow_id: str, value: str, replacement: str) -> int:
    if not value:
        return 0
    with _lock:
        live = _live.get(workflow_id)
        if live:
            live["seg"] = [f.replace(value, replacement) for f in live["seg"]]
    return db.trail_redact(workflow_id, value, replacement)

# What the trail holds, for tests and inspection
def turns(workflow_id: str) -> list[dict]:
    return [{**t, "items": db.trail_items(workflow_id, t["turn_id"])}
            for t in db.trail_turns(workflow_id)]
