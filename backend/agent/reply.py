# The reply seam: everything the user sends lands through here, and the server decides how
from __future__ import annotations

import time
from typing import Optional

from agent import steps
from agent import interactions
from agent import transcript

KINDS = ("typed", "click")

# Normalises a /say body into the one entry shape every landing path reads
def entry_from(body: dict) -> dict:
    body = body or {}
    kind = str(body.get("kind") or "typed")
    if kind not in KINDS:
        kind = "typed"
    files = body.get("files")
    answers = body.get("answers")
    return {"cid": str(body.get("cid") or "")[:64],
            "kind": kind,
            "text": str(body.get("text") or ""),
            "iid": str(body.get("iid") or ""),

            "issue": str(body.get("issue") or "")[:40],
            "files": [str(n) for n in files]
                     if isinstance(files, list) and files else [],
            "answers": dict(answers) if isinstance(answers, dict) else None}

# Resolves a paused card or parks on the running turn; None means nothing runs and the caller starts a turn
def deliver(workflow_id: str, entry: dict) -> Optional[dict]:
    pend = interactions.pending(workflow_id)
    target = None
    if entry["iid"]:
        target = next((x for x in pend if x["id"] == entry["iid"]), None)
    elif entry["kind"] == "typed":
        asks = [x for x in pend if x.get("kind") == "ask"]
        target = asks[-1] if asks else None
    if target is not None:
        via = "click" if entry["kind"] == "click" else "typed"
        if via == "typed" and option_match({"payload": target.get("payload")},
                                           entry["text"]) is not None:
            via = "click"
        answer = {"text": entry["text"],
                  "via": via,
                  "cid": entry["cid"],
                  **({"answers": entry["answers"]} if entry["answers"] else {}),
                  **({"files": entry["files"]} if entry["files"] else {})}
        if interactions.resolve(target["id"], answer):
            return {"landed": "resolved"}
    import turns
    if turns.interject(workflow_id, entry):
        if entry["kind"] == "typed":
            turns.request_stop(workflow_id)
        return {"landed": "parked"}
    return None

# Lands an entry on the workflow dict the caller owns; returns (driving text, the items appended)
def land(workflow: dict, entry: dict) -> tuple[str, list]:
    from agent import chatlog
    pid = workflow.get("id") or ""
    seq0 = int(workflow.get("transcript_seq") or 0)
    drive = ""
    if entry["kind"] == "click" and entry["iid"]:
        req = next((r for r in transcript.open_requests(workflow)
                    if r.get("iid") == entry["iid"]), None)
        if req is not None:
            drive = settle_at_rest(workflow, req, entry)
        else:
            drive = ""
        if entry["files"]:
            transcript.append_message(workflow, "user", "", files=entry["files"],
                                      cid=entry["cid"])

        steps.settle_open_asks(workflow)
    else:
        text = entry["text"]

        hit = next((r for r in transcript.open_requests(workflow)
                    if r.get("request") in ("ask", "blueprint")
                    and option_match(r, text) is not None), None)
        if hit is not None:
            drive = settle_at_rest(workflow, hit, {**entry, "kind": "click"})
            steps.settle_open_asks(workflow)
        else:
            transcript.append_message(workflow, "user", text,
                                      files=entry["files"] or None,
                                      cid=entry["cid"])
            steps.settle_open_asks(workflow, cid=entry["cid"])
            if steps.supersede_open_plan(workflow, cid=entry["cid"]):
                chatlog.append(pid, "plan-answered-by-message", {})
            drive = text
    if entry.get("issue"):
        from agent import turnstate
        turnstate.of(workflow).fix_issue_id = entry["issue"]
    if drive:
        chatlog.append(pid, "user", {"content": drive,
                                     **({"issue": entry["issue"]} if entry.get("issue") else {})})
        try:
            from agent import orchestrator
            orchestrator.mark_issue_in_progress(workflow, drive, entry.get("issue"))
        except Exception:
            pass
    items = [i for i in transcript.items(workflow)
             if int(i.get("seq") or 0) > seq0]
    return drive, items

# Which option a typed reply IS: exact text of one of the card's options, else none
def option_match(req: dict, text: str):
    payload = (req or {}).get("payload") or {}
    t = str(text or "").strip()
    for o in payload.get("options") or []:
        label = o if isinstance(o, str) else (o or {}).get("label") or (o or {}).get("value")
        if label and t == str(label).strip():
            return str(label)
    return None

# ONE settle for every answer to a card: click or typed, live or at rest - the same items, stamps and words
def settle(workflow: dict, req: dict, got, at_rest: bool = False) -> dict:
    payload = req.get("payload") or {}
    kind = req.get("request")
    iid = req.get("iid")
    text = str((got.get("text") if isinstance(got, dict) else got) or "")
    via = (got.get("via") if isinstance(got, dict) else "") or "click"
    cid = str(got.get("cid") or "") if isinstance(got, dict) else ""
    files = (got.get("files") if isinstance(got, dict) else None) or []
    raw = got.get("answers") if isinstance(got, dict) else None
    matched = option_match(req, text)
    if matched is not None:
        text, via = matched, "click"
    clicked = via != "typed"
    secret = bool(payload.get("secret"))
    if not clicked:
        transcript.append_message(workflow, "user", text,
                                  files=files or None, cid=cid)
        shown: object = {"via_message": True}
    else:
        if files:
            transcript.append_message(workflow, "user", "", files=files, cid=cid)
        if kind == "approval":
            text = text if text in ("allow", "always") else "deny"
            shown = text
        elif secret and text == "(provided)":
            shown = "(provided)"
            name = payload.get("secret_name")
            if name:
                var = next((v for v in workflow.setdefault("variables", [])
                            if v.get("name") == name), None)
                if var is None:
                    workflow["variables"].append({"name": name, "value": True,
                                                 "secret": True, "persistent": True})
                else:
                    var["value"] = True
                    var["secret"] = True
        elif payload.get("fields") and isinstance(raw, dict):
            shown = steps.clean_ask_answers(workflow, payload["fields"], raw)
        else:
            shown = str(text)[:200]
        if kind == "blueprint" and payload.get("head") != "built":
            plan = workflow.get("plan") or {}
            if payload.get("fix"):
                if plan.get("ticket_id") == payload["fix"]:
                    plan["fix_approved_ts"] = time.time()

                    plan["approved_changes"] = sorted(steps.declared_change_names(workflow, plan))
                    workflow["plan"] = plan
            elif workflow.get("nodes"):
                plan["change_approved_ts"] = time.time()
                plan["approved_changes"] = sorted(steps.declared_change_names(workflow, plan))
                workflow["plan"] = plan
            else:
                workflow["plan_approved_ts"] = time.time()

                workflow["approved_steps"] = steps.approved_steps_of(plan)
        if kind == "approval" and text in ("allow", "always") and at_rest:
            grants = workflow.setdefault("approval_grants", [])
            grant = {"step": payload.get("step") or "",
                     "title": payload.get("title") or "",
                     "ts": time.time()}
            if text == "always" and payload.get("holds"):
                grant.update({"kind": payload["holds"], "always": True,
                              **({"domain": payload["domain"]} if payload.get("domain") else {})})
            grants.append(grant)
    drive = driving_text(req, "(provided)" if secret and clicked else text, shown)
    transcript.append_answer(workflow, iid, drive, shown=shown, cid=cid)
    return {"drive": drive, "shown": shown, "clicked": clicked, "text": text}

# A click on a card whose turn already ended: the same settle, at rest
def settle_at_rest(workflow: dict, req: dict, entry: dict) -> str:
    got = {"text": entry["text"], "via": "click" if entry["kind"] == "click" else "typed",
           "cid": entry["cid"],
           **({"answers": entry["answers"]} if entry.get("answers") else {}),
           **({"files": entry["files"]} if entry.get("files") else {})}
    return settle(workflow, req, got, at_rest=True)["drive"]

# Lands every reply parked on a running or paused turn - the one landing, shared by the turn's event seam and the pause
def land_parked(workflow: dict) -> list:
    import turns
    from agent import chatlog
    pid = workflow.get("id") or ""
    landed = []
    for entry in turns.take_interjections_to_land(pid):
        try:
            entry["drive"], _ = land(workflow, entry)
        except Exception as e:
            chatlog.append(pid, "reply-land-error",
                           {"error": f"{type(e).__name__}: {e}"})
            entry["drive"] = entry.get("text") or ""
        landed.append(entry)
    if landed:
        steps.save(workflow)
    return landed

# The words a click stands for - what the model reads, composed once, here
def driving_text(req: dict, ans: str, shown=None) -> str:
    payload = req.get("payload") or {}
    kind = req.get("request")
    if kind == "approval":
        title = payload.get("title") or "that"
        return (f'Yes to "{title}" - go ahead and try that step again.'
                if ans in ("allow", "always") else f'No to "{title}".')
    if payload.get("secret") and ans == "(provided)":
        return f'I\'ve provided "{payload.get("secret_name") or "the value"}" securely.'
    if payload.get("fields") and isinstance(shown, dict):
        parts, missing = [], []
        for f in payload["fields"]:
            name = f.get("name")
            label = f.get("label") or name
            v = shown.get(name)
            if v:
                parts.append(f"{label}: {v}")
            else:
                missing.append(str(label))
        if not parts:
            return ans
        return (f"Here are the details - {'; '.join(parts)}."
                + (f" Not provided: {', '.join(missing)}." if missing else ""))
    return ans
