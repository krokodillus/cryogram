# Questions an installed integration needs answered before the builder starts on a workflow
from __future__ import annotations

from typing import Optional

import extensions
from agent import steps
from agent import transcript

# How many options a question may carry; past this it is a design mistake, not a card
MAX_OPTIONS = 6

# The payload key that marks a card as one of these, and names which question it answers
KEY_FIELD = "opening_key"

# Every opening question an integration has declared for this workflow, cleaned
def _declared(workflow: dict) -> list[dict]:
    out: list[dict] = []
    for group in extensions.every("opening_questions", workflow):
        if not isinstance(group, (list, tuple)):
            continue
        for q in group:
            clean = _clean(q)
            if clean and clean["key"] not in {c["key"] for c in out}:
                out.append(clean)
    return out

# One declared question, or None when it is not usable as a card
def _clean(q) -> Optional[dict]:
    if not isinstance(q, dict):
        return None
    key = str(q.get("key") or "").strip()
    question = str(q.get("question") or "").strip()
    if not key or not question:
        return None
    options = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
    return {"key": key, "question": question, "options": options[:MAX_OPTIONS]}

# The questions this workflow has already been shown, by key
def asked(workflow: dict) -> set:
    out = set()
    for item in transcript.items(workflow):
        if item.get("kind") != "request":
            continue
        key = (item.get("payload") or {}).get(KEY_FIELD)
        if key:
            out.add(str(key))
    return out

# The first question still owed for this workflow, or None when there is nothing to ask
def owed(workflow: dict) -> Optional[dict]:
    seen = asked(workflow)
    for q in _declared(workflow):
        if q["key"] not in seen:
            return q
    return None

# What the user answered, or None while it is unanswered or was never asked
def answer(workflow: dict, key: str) -> Optional[str]:
    iid = ""
    for item in transcript.items(workflow):
        if item.get("kind") == "request" \
                and str((item.get("payload") or {}).get(KEY_FIELD) or "") == key:
            iid = str(item.get("iid") or "")
    if not iid:
        return None
    for item in transcript.items(workflow):
        if item.get("kind") == "answer" and str(item.get("to") or "") == iid:
            return str(item.get("text") or "") or None
    return None

# Puts the question on screen as a card; the turn waits for the next message rather than starting
def raise_card(workflow: dict, q: dict) -> list:
    seq0 = int(workflow.get("transcript_seq") or 0)
    transcript.append_request(workflow, "ask",
                              {"question": q["question"],
                               "options": q["options"],
                               KEY_FIELD: q["key"]})
    steps.save(workflow)
    return [i for i in transcript.items(workflow)
            if int(i.get("seq") or 0) > seq0]
