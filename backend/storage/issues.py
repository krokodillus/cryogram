# What a run's outcome changes on its workflow: the wait the page shows, the issue a stop raises, the setup notice and the run stamp
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from runtime import run_state
from storage import store

# One issue shape, whatever raised it
def new_ticket(*, run_id: Optional[str], node_id: Optional[str], reason: str,
               verdict: dict, case_id: Optional[str] = None, fired: bool = False,
               entry_inputs: Optional[dict] = None, **extra: Any) -> dict:
    return {"id": f"tkt_{uuid.uuid4().hex[:8]}", "ts": time.time(),
            "status": "open", "run_id": run_id, "node_id": node_id,
            "case_id": case_id, "reason": reason, "fired": fired,
            "entry_inputs": dict(entry_inputs or {}),
            "verdict": verdict or {}, "notes": "", **extra}

# A step that went through on a later try closes the issue its earlier failure raised
def close_passed_issues(workflow: dict, result: dict) -> None:
    run_id = result.get("run_id")
    if not run_id:
        return
    if result.get("status") == "completed":
        done = set(result.get("path") or [])
    else:
        done = set((run_state.load(run_id) or {}).get("path") or [])
    if not done:
        return
    changed = False
    for t in workflow.get("tickets") or []:
        if (t.get("run_id") == run_id and t.get("node_id") in done
                and t.get("status") in ("open", "in-progress")
                and not t.get("user_outputs")):
            t["status"] = "closed"
            t["resolution_note"] = "went through on a later try"
            changed = True
    if changed:
        store.save(workflow)

# Applies a run's result to a fresh copy of its workflow and returns that copy
def record_run_outcome(workflow_id: str, workflow: dict, result: dict) -> dict:
    workflow = store.load(workflow_id) or workflow
    status, reason = result.get("status"), result.get("reason")
    if status == "halted" and result.get("verdict") and reason != "stopped-by-user":
        run_state.note_verdict(result.get("run_id") or "", result["verdict"])
    if status == "halted":
        result["waiting_on_you"] = run_state.waiting_on_you(reason)
    close_passed_issues(workflow, result)
    pre_run = reason == "environment-check-failed" and not result.get("halted_at")
    if pre_run:
        workflow["setup_block"] = {
            "problems": (result.get("verdict") or {}).get("environment") or [],
            "ts": time.time()}
        store.save(workflow)
    elif status == "completed" and workflow.get("setup_block"):
        workflow.pop("setup_block", None)
        store.save(workflow)

    if status == "completed":
        workflow["last_run_ts"] = time.time()

        new_ids = []
        for nid, e in (result.get("set_aside") or {}).items():
            if e.get("policy") != "proceed":
                continue
            ports = e.get("ports") or []
            lines = [f"{o.get('total')} of {o.get('of')} items on "
                     f"\"{o.get('port')}\" did not fit the expected shape "
                     "and were set aside; the rest went through"
                     for o in ports]
            ticket = new_ticket(run_id=result.get("run_id"), node_id=nid,
                                reason="items-set-aside", verdict={"set_aside": lines},
                                offending=ports)
            workflow.setdefault("tickets", []).append(ticket)
            new_ids.append(ticket["id"])
        if new_ids:
            result["set_aside_tickets"] = new_ids
        store.save(workflow)
    elif (status == "halted" and not pre_run

          and reason not in run_state.ASKING and reason not in run_state.USER_ENDED):
        snap = run_state.load(result.get("run_id") or "") or {}
        extra = {}

        for key in ("saw", "ai_failure", "empty_ports", "offending"):
            if result.get(key):
                extra[key] = result[key]
        ticket = new_ticket(run_id=result.get("run_id"), node_id=result.get("halted_at"),
                            reason=reason, verdict=result.get("verdict") or {},
                            case_id=result.get("case_id"),
                            fired=run_state.has_fired(result.get("run_id") or "",
                                                      result.get("halted_at") or ""),
                            entry_inputs=snap.get("entry_inputs"), **extra)
        workflow.setdefault("tickets", []).append(ticket)
        store.save(workflow)
        result["ticket_id"] = ticket["id"]
    return workflow
