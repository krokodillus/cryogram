# The record of what the user was shown - append-only, never rewritten after the fact
from __future__ import annotations

import time
import json
import uuid
from typing import Any, Optional

def items(workflow: dict) -> list[dict]:
    return workflow.setdefault("transcript", [])

def _append(workflow: dict, item: dict, cid: str = "") -> dict:
    seq = int(workflow.get("transcript_seq") or 0) + 1
    workflow["transcript_seq"] = seq
    item = {"seq": seq, "at": time.time(),
            **({"cid": str(cid)[:64]} if cid else {}), **item}
    items(workflow).append(item)
    try:
        import turns
        turns.shown(workflow.get("id") or "", item)
    except Exception:
        pass
    return item

# The one sanctioned edit of the record: a pasted secret is replaced by its name marker everywhere it appeared
def redact_value(workflow: dict, value: str, replacement: str) -> int:
    from agent import steps
    n = 0
    for it in items(workflow):
        for key in ("text",):
            if isinstance(it.get(key), str) and value in it[key]:
                it[key] = it[key].replace(value, replacement)
                n += 1
        pl = it.get("payload")
        if isinstance(pl, dict):
            dumped = json.dumps(pl)
            if value in dumped:
                it["payload"] = json.loads(dumped.replace(
                    json.dumps(value)[1:-1], json.dumps(replacement)[1:-1]))
                n += 1
    return n

def new_iid(prefix: str = "req") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

# True while a question or approval is open; nothing may land in the chat under an open question
def awaiting_user(workflow: dict) -> bool:
    for item in items(workflow):
        if item.get("kind") != "request":
            continue
        if asks_nothing(item):
            continue
        if item.get("iid") not in answered(workflow):
            return True
    return False

# An assistant message while a card is waiting is refused here - one enforcement point every path goes through
def append_message(workflow: dict, sender: str, text: str,
                   files: Optional[list] = None,
                   sample: str = "", cid: str = "") -> dict:
    if sender != "user" and awaiting_user(workflow):
        try:
            from agent import chatlog
            chatlog.append(workflow.get("id") or "", "card-tail-dropped",
                           {"content": str(text or "")[:400]})
        except Exception:
            pass
        return {}
    return _append(workflow, {
        "kind": "message", "from": "user" if sender == "user" else "assistant",
        "text": str(text or ""),

        **({"files": [f if isinstance(f, dict) else str(f)
                      for f in files]} if files else {}),
        **({"sample": sample} if sample else {})}, cid=cid)

# A card is stored exactly as rendered; its settled look is derived from the paired answer later
def append_request(workflow: dict, request: str, payload: dict,
                   iid: str = "") -> dict:
    return _append(workflow, {
        "kind": "request", "request": request,
        "iid": iid or new_iid(request[:3]), "payload": dict(payload or {})})

# What the user did about a request; never the value of a secret
def append_answer(workflow: dict, to: str, text: str,
                  shown: Any = None, cid: str = "") -> dict:
    return _append(workflow, {
        "kind": "answer", "to": str(to or ""), "text": str(text or ""),
        **({"shown": shown} if shown is not None else {})}, cid=cid)

def answered(workflow: dict) -> set:
    return {i.get("to") for i in items(workflow) if i.get("kind") == "answer"}

# A card that is a record, not a question - never open, never awaited
def asks_nothing(item: dict) -> bool:
    return item.get("request") == "blueprint" \
        and (item.get("payload") or {}).get("head") == "built"

# Requests with no answer yet, oldest first
def open_requests(workflow: dict, request: str = "") -> list[dict]:
    done = answered(workflow)
    return [i for i in items(workflow)
            if i.get("kind") == "request" and i.get("iid") not in done
            and not asks_nothing(i)
            and (not request or i.get("request") == request)]

def last_request(workflow: dict, request: str = "") -> Optional[dict]:
    for i in reversed(items(workflow)):
        if i.get("kind") == "request" and (not request
                                           or i.get("request") == request):
            return i
    return None

# What the agent re-reads is the same record the user saw - it cannot recall something never shown
def replay_lines(workflow: dict) -> list[dict]:
    out = []
    for i in items(workflow):
        kind = i.get("kind")
        if kind == "message":
            out.append({"role": i.get("from"), "text": i.get("text", ""),
                        "item": i})
        elif kind == "request":
            p = i.get("payload") or {}
            text = str(p.get("question") or p.get("title")
                       or p.get("summary") or "")
            lead = str(p.get("lead") or "")
            out.append({"role": "assistant",
                        "text": (lead + "\n" + text).strip() if lead else text,
                        "item": i})
        elif kind == "answer":
            out.append({"role": "user", "text": i.get("text", ""), "item": i})
    return out
