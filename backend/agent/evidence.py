# One view per step over everything recorded about it: exploration runs, tests and real-run cases
from __future__ import annotations

from typing import Any, Optional

from agent import previews

# The recorded proving run as the drawer's Evidence view, scrubbed and size-bounded
def recorded_sample(workflow: dict, node: dict) -> Optional[dict]:
    from agent import cells
    rec = cells.latest_ok(workflow["id"], str(node.get("name") or ""))
    if not rec:
        return None
    return {"inputs": previews.guard(rec.get("inputs") or {}),
            "outputs": previews.guard(rec.get("output")),
            **({"model": rec["model"]} if rec.get("model") else {})}

# Upstream recorded outputs in, downstream field reads out - the contract handed to a fixing turn
def step_contract(workflow: dict, node: dict) -> str:
    from agent import cells
    from agent import gate
    from agent import plan_logic
    lines = []
    by_name = {n.get("name"): n for n in workflow.get("nodes", [])}
    ancestors = plan_logic.ancestors_by_name({}, workflow)
    name = node.get("name")
    outs = {p.get("name") for p in node.get("outputs", [])}

    needs = {p.get("name") for p in node.get("inputs", [])
             if p.get("type") != "secret"}
    ups = sorted({a for a in ancestors.get(name, ())
                  if needs & {q.get("name")
                              for q in (by_name.get(a) or {}).get("outputs") or []}})
    for uname in ups:
        rec = cells.latest_ok(workflow["id"], uname)
        if rec is not None:
            lines.append(f'UPSTREAM "{uname}" recorded output: '
                         + str(previews.guard(rec.get("output")))[:400])

    for d in workflow.get("nodes", []):
        if name not in ancestors.get(d.get("name"), ()):
            continue
        taken = sorted({p.get("name") for p in d.get("inputs", [])
                        if p.get("name") in outs})
        if not taken:
            continue
        reads: set = set()
        for pn in taken:
            reads |= gate.record_field_reads(
                d.get("config", {}).get("code", ""), pn)
        lines.append(f'DOWNSTREAM "{d.get("name")}" consumes: '
                     + ", ".join(taken)
                     + (f" (reads fields: {', '.join(sorted(reads))})" if reads else ""))
    if not lines:
        return ""
    return ("STEP CONTRACT (fix only this segment; re-prove with run_cell against these):\n" + "\n".join("- " + ln for ln in lines))

# A value-free description of a failing case - names, types, sizes and the failed check only
def structural_summary(value: Any, error_class: str = "",
                       failed_check: str = "") -> dict:
    def shape(v: Any, depth: int = 0) -> Any:
        if depth > 3:
            return type(v).__name__
        if isinstance(v, dict):
            return {k: shape(x, depth + 1) for k, x in list(v.items())[:30]}
        if isinstance(v, list):
            return {"list": len(v),
                    "of": shape(v[0], depth + 1) if v else "empty"}
        if isinstance(v, str):
            return f"text({len(v)})"
        return type(v).__name__
    return {"redaction": "structural", "shape": shape(value),
            **({"error_class": error_class} if error_class else {}),
            **({"failed_check": failed_check} if failed_check else {})}
