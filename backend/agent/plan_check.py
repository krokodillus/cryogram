# Post-build validation: did the build produce exactly what the plan says? Pure code, runs every build
from __future__ import annotations

from typing import Optional

from agent import steps
from agent import node_tools
from step_types import reaches_outside

def _resolve(workflow: dict, ref: str) -> Optional[dict]:
    for n in workflow.get("nodes", []):
        if n.get("id") == ref or n.get("name") == ref:
            return n
    return None

def _check_ports(plan_node: dict, built: dict, findings: list) -> None:
    nm = plan_node.get("name")
    for side in ("inputs", "outputs"):
        declared = {p["name"]: p for p in built.get(side) or []}
        for p in plan_node.get(side) or []:
            got = declared.get(p.get("name"))
            if got is None:
                findings.append(f"node \"{nm}\": planned {side[:-1]} port "
                                f"{p.get('name')!r} was not declared")
            elif p.get("type") and got.get("type") != p.get("type"):
                findings.append(f"node \"{nm}\": port {p.get('name')!r} is declared "
                                f"{got.get('type')!r} but the plan says {p.get('type')!r}")

# Did the build produce exactly what the plan says? Pure code, runs on every build
def check(workflow: dict, plan: dict, prior_node_ids: Optional[set] = None) -> dict:
    steps.canonicalise_refs(workflow)
    findings: list[str] = []
    plan_nodes = plan.get("nodes") or []
    planned_names = {n.get("name") for n in plan_nodes}

    for pn in plan_nodes:
        nm = pn.get("name")
        built = _resolve(workflow, nm)
        if built is None:
            findings.append(f"planned node \"{nm}\" was not built")
            continue
        if pn.get("type") and built.get("type") != pn.get("type"):
            findings.append(f"node \"{nm}\" was planned as {pn.get('type')!r} but "
                            f"built as {built.get('type')!r}")
        if reaches_outside(pn) and not str(built.get("external_impact") or "").strip():
            findings.append(f"connector \"{nm}\" declares no external_impact - the "
                            "user cannot see what they would be approving")
        _check_ports(pn, built, findings)

        if built.get("type") == "code":
            v = node_tools.validate_node(workflow, built["id"])
            if not v.get("ok"):
                findings.append(f"node \"{nm}\" is not validate-green: missing "
                                f"{', '.join(v.get('missing', []))}")
        else:
            cfg = built.get("config", {})

            impl = (bool(cfg.get("prompt"))
                    if built.get("type") == "ai" else bool(cfg.get("code"))
                    or built.get("type") == "user-input")

            structural = {
                "declared_outputs": bool(built.get("outputs")),
                "implementation": impl,

            }
            missing = [k for k, ok in structural.items() if not ok]
            if missing:
                findings.append(f"node \"{nm}\" is structurally incomplete: missing "
                                f"{', '.join(missing)} (tests not replayed - "
                                f"{built.get('type')} nodes are never re-run here)")

    ids = {n["id"]: n for n in workflow.get("nodes", [])}

    if isinstance(plan.get("edges"), list) and (plan.get("edges") or plan.get("built_ts")):
        def _id(ref):
            n = _resolve(workflow, ref or "")
            return n["id"] if n else None
        plan_ids = {_id(pn.get("name")) for pn in plan.get("nodes") or []}
        plan_ids.discard(None)
        wanted = {(_id(e.get("src")), _id(e.get("dst"))) for e in plan.get("edges") or []}
        for e in workflow.get("edges", []) or []:
            pair = (e.get("src"), e.get("dst"))
            if pair[0] in plan_ids and pair[1] in plan_ids and pair not in wanted:
                findings.append(f'connection "{ids.get(pair[0], {}).get("name", pair[0])}" -> '
                                f'"{ids.get(pair[1], {}).get("name", pair[1])}" is not in the plan')
    for e in plan.get("edges") or []:
        src, dst = _resolve(workflow, e.get("src", "")), _resolve(workflow, e.get("dst", ""))
        if src is None or dst is None:
            findings.append(f"planned edge {e.get('src')!r} -> {e.get('dst')!r}: one "
                            "end does not exist in the built workflow")
            continue
        built_edge = next((be for be in workflow.get("edges", [])
                           if be["src"] == src["id"] and be["dst"] == dst["id"]), None)
        if built_edge is None:
            findings.append(f"planned edge \"{src.get('name')}\" -> "
                            f"\"{dst.get('name')}\" was not wired")
        elif (e.get("when") or "") != (built_edge.get("when") or ""):
            findings.append(f"edge \"{src.get('name')}\" -> \"{dst.get('name')}\": "
                            f"routing condition differs from the plan "
                            f"({built_edge.get('when')!r} vs {e.get('when')!r})")

    if prior_node_ids is not None:
        for nid, n in ids.items():
            if nid not in prior_node_ids and n.get("name") not in planned_names:
                findings.append(f"node \"{n.get('name')}\" was built but is not in "
                                "the plan")

    if plan_nodes and not workflow.get("deliverables"):
        findings.append("no step's output is kept for the user - declare the run's results with set_deliverables (guided by the intent: what does the user get out of a run?)")

    for ref in (plan.get("changes") or {}).get("removed") or []:
        if _resolve(workflow, ref) is not None:
            findings.append(f"node \"{ref}\" was marked removed but still exists - "
                            "delete it")

    return {"ok": not findings, "findings": findings}
