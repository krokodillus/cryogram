# The deterministic build: a proven plan becomes code from its recorded evidence - no model involved
from __future__ import annotations

from agent import steps

# Builds every step from its recorded evidence and wires the plan's edges; edges the plan dropped are removed
def assemble(workflow: dict, plan: dict, emit) -> list[str]:
    from agent import node_tools
    errors: list[str] = []
    for pn in plan.get("nodes") or []:
        name = str(pn.get("name") or "")
        emit({"type": "phase", "text": f'Finishing "{name}"', "node": name})

        from storage import deps as _deps
        need = _deps.unsatisfied(workflow["id"], pn.get("packages") or [])
        if need:
            emit({"type": "phase", "node": name,
                  "text": f"Installing {', '.join(need)} (can take a moment)"})
        r = node_tools.build_step(workflow, name)
        if isinstance(r, dict) and r.get("ok"):
            emit({"type": "node-built", "nodes": [name]})
        else:
            why = (r or {}).get("errors") if isinstance(r, dict) else None
            why = "; ".join(str(e) for e in why if e) if isinstance(why, list) else ""
            errors.append(f'step "{name}": '
                          + (why or str((r or {}).get("error") or "could not be built")))
    edges = [{"src": e.get("src"), "dst": e.get("dst"),
              "when": e.get("when") or ""} for e in plan.get("edges") or []]

    if edges or (isinstance(plan.get("edges"), list) and plan.get("built_ts")):
        stale = _stale_edges(workflow, plan, edges)
        if stale:
            emit({"type": "tool",
                  "text": f"removing {len(stale)} connection"
                          f"{'' if len(stale) == 1 else 's'} no longer in the plan"})
            workflow["edges"] = [e for e in workflow.get("edges", [])
                                if e not in stale]
        emit({"type": "tool", "text": f"connecting the planned steps ({len(edges)})"})
        r = node_tools.connect_nodes(workflow, edges=edges)
        for k, v in (r.get("edges") or {}).items():
            if isinstance(v, dict) and not v.get("ok"):
                errors.append(f"connection {k}: {v.get('error')}")
    for nm in (plan.get("changes") or {}).get("removed") or []:
        node = next((n for n in workflow.get("nodes", [])
                     if n.get("name") == nm), None)
        if node:
            emit({"type": "tool", "text": f'removing "{nm}"'})
            node_tools.delete_node(workflow, node["id"])

    steps.canonicalise_refs(workflow)

    steps.unify_field_labels(workflow)
    return errors

# Stored edges the plan no longer declares - dropped so the plan's edges are the order
def _stale_edges(workflow: dict, plan: dict, plan_edges: list) -> list:
    from agent import node_tools
    steps.canonicalise_refs(workflow)
    plan_ids: set = set()
    for pn in plan.get("nodes") or []:
        n = (steps.find_node(workflow, str(pn.get("id") or ""))
             or steps.find_node(workflow, str(pn.get("name") or "")))
        if n:
            plan_ids.add(n["id"])
    declared: dict[tuple, str] = {}
    for e in plan_edges:
        a = steps.find_node(workflow, str(e.get("src") or ""))
        b = steps.find_node(workflow, str(e.get("dst") or ""))
        if a and b:
            declared[(a["id"], b["id"])] = str(e.get("when") or "")
    stale = []
    for e in workflow.get("edges", []):
        if e.get("src") not in plan_ids or e.get("dst") not in plan_ids:
            continue
        want = declared.get((e.get("src"), e.get("dst")))
        if want is None or want != str(e.get("when") or ""):
            stale.append(e)
    return stale
