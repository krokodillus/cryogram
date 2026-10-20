# Supplies current phase, permitted tools and relevant step guidance before execution
import hashlib
import json

from agent import steps, turnstate

EXECUTION = {"run_cell", "run_ai_step", "build_workflow"}

def availability(workflow):
    plan = workflow.get("plan") or {}
    reason = steps.skeleton_first(workflow)
    phase = "planning" if reason else "working"
    if turnstate.of(workflow).built:
        phase, reason = "finished", "The build completed; this turn is over."
    elif turnstate.of(workflow).ask_open:
        phase, reason = "waiting", "A card is waiting for the user's answer."
    elif turnstate.of(workflow).pending_fix:
        phase, reason = "repair-queued", "The repair is already queued; this turn is over."
    elif not reason and plan.get("ticket_id") and not plan.get("fix_approved_ts"):
        phase, reason = "fix-declaration", "Save the diagnosis/change scope and obtain the fix card approval before execution."
    elif not reason and steps.change_needs_approval(workflow, plan):
        phase, reason = "change-declaration", "Save the change declaration and obtain its card approval before execution."
    elif not reason and steps.plan_steps_need_approval(workflow, plan):
        phase, reason = "plan-approval", "Save the amended step list and obtain the plan card approval before execution."
    from agent import actions
    names = [s[0] for s in actions._specs()] + ["load_skill"]
    blocked = set(EXECUTION) if reason else set()

    unavailable = {n: steps.turn_over_error(workflow, n) or (reason if n in blocked else "")
                   for n in names}
    return {"phase": phase, "available_tools": sorted(n for n in names if not unavailable[n]),
            "unavailable_tools": {n: why for n, why in unavailable.items() if why}}

def refusal(workflow, name):
    return availability(workflow)["unavailable_tools"].get(name, "")

def automatic_ids(workflow):
    nodes = (workflow.get("plan") or {}).get("nodes") or workflow.get("nodes") or []
    ids = set()
    for node in nodes:
        kind = node.get("type")
        if kind == "code":
            ids.add("63-code-practice")
        elif kind == "connector":
            ids.add("61-connector-practice")
        elif kind == "browser":
            ids.update(("browser", "browser/page-state"))
        elif kind == "ai":
            ids.add("62-ai-practice")
            if any(p.get("type") in ("file", "filepath") for p in node.get("inputs") or []):
                ids.add("62-ai-practice/files")
    return ids

def supply(workflow, *, all_current=False, already_delivered=""):
    from agent import active_skills
    current = active_skills.blocks(workflow.get("id") or "", automatic_ids(workflow))
    ids = {sid for sid, _ in current}
    seen = turnstate.of(workflow).setdefault("guidance_seen", {})
    blocks = []
    for sid, body in current:
        digest = hashlib.sha256(body.encode()).hexdigest()
        if (all_current or seen.get(sid) != digest) and sid != already_delivered:
            blocks.append(body)
        seen[sid] = digest
    return "\n\n".join(blocks), ids

def render_state(workflow):
    state = availability(workflow)
    return "\n[current operating state — rechecked before every tool]\n" + json.dumps(state) + "\n"
