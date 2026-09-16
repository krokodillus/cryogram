# The user-input step: the person supplies values, on the run form or partway through a run
from __future__ import annotations

TYPE = "user-input"

MAY = {"code": False, "network": False, "app": False,
       "browser": False, "model": False, "process": False}

RECEIPT = None
EVIDENCE = None
BAR = "declared outputs only"

# A plan finding for each value this step asks for that the workflow already stores
def asks_for_a_stored_value(node: dict, stored: set) -> list[str]:
    out = []
    for p in node.get("outputs") or []:
        if p.get("name") in stored:
            out.append(
                f'step "{node.get("name")}" asks the user for '
                f'"{p.get("name")}" every run, but the workflow already '
                "stores that value - use the stored variable/secret instead of re-collecting it (if runs cannot read it, fix that; never demote a stored value to a per-run ask)")
    return out

# A plan finding when this step would ask on every run with no stated reason
def asks_every_run_without_reason(node: dict) -> list[str]:
    if (node.get("inputs") or []) or str(node.get("per_run_reason") or "").strip():
        return []
    return [
        f'step "{node.get("name")}" would ask the user for a value on '
        "EVERY run. IF THE USER ASKED for that (they want to give the value each run), KEEP the step and set per_run_reason to their wish - never delete a step the user asked for to pass this check. Only when the value rarely changes make it a variable instead (declare_variables, with the value if known) and drop the step - an input no earlier step produces resolves from the same-named variable at run time"]

# Warnings about ports the run form cannot render well
def port_warnings(node: dict) -> list[str]:
    out = []
    vague = [p["name"] for p in node["outputs"] if not p.get("type")]
    if vague:
        out.append(f"user-input ports {vague} have no type - give them concrete "
                   "types so the run form renders the right fields")
    unlabelled = [p["name"] for p in node["outputs"] if not p.get("label")]
    if unlabelled:
        out.append(f"ports {unlabelled} have no human-readable label")
    return out

# What makes this step ready to build: declared outputs, and nothing to implement
def readiness(node: dict) -> dict:
    return {"declared_outputs": bool(node.get("outputs")),
            "implementation": True}

# Why this step cannot be run on its own
def refuse_run_alone(node: dict) -> str:
    return ("a user-input node has no behaviour to run - it just carries the values the user supplies")

# The line the build summary shows for this step
def run_summary(node: dict, has_incoming: bool) -> str:
    ports = ",".join(p["name"] for p in node.get("outputs") or [])
    return (f"pauses MID-RUN to ask for: {ports}" if has_incoming
            else f"asked on the RUN FORM before start: {ports}")

# The step's outputs: what the person typed, and a secret by its name only
def run(node: dict, inputs: dict, entry: dict, capability=None) -> tuple:
    return ({p["name"]: (p["name"] if p.get("type") == "secret" else entry.get(p["name"]))
             for p in node.get("outputs", [])}, False)

# Its outputs are already keyed by port name, so nothing is reshaped
def shape_output(node: dict, raw):
    return raw

# The choices shown for one field: a record shows a readable label and submits one field
def _options(port: dict, dyn) -> list:
    out = []
    for o in (port.get("options") or dyn or []):
        if isinstance(o, dict):
            label = " - ".join(str(v) for v in o.values() if v not in (None, ""))
            vf = port.get("value_field")
            if vf and vf in o:
                value = str(o[vf])
            elif len(o) == 1:
                value = str(next(iter(o.values())))
            else:
                value = label
            out.append({"label": label, "value": value})
        else:
            out.append(str(o))
    return out

# What to ask the person partway through a run, or None when nothing is missing
def ask_mid_run(node: dict, nid: str, entry: dict, inputs: dict,
                has_incoming: bool, secret_supplied) -> dict | None:
    if not has_incoming:
        return None
    need = [p for p in node.get("outputs", [])
            if (not secret_supplied(p["name"]) if p.get("type") == "secret"
                else entry.get(p["name"]) in (None, ""))]
    if not need:
        return None
    dyn = next((v for v in (inputs or {}).values() if isinstance(v, list)), None)
    fields = [{"name": p["name"], "label": p.get("label") or p["name"],
               "type": p.get("type", "text"),
               "secret": p.get("type") == "secret",
               "options": _options(p, dyn)}
              for p in need]
    return {"lines": [f"step \"{node.get('name', nid)}\" needs your input to continue"],
            "input_request": {"prompt": node.get("description") or node.get("name")
                              or "Your choice", "fields": fields}}

# What to ask again when a typed value fails the step's own check
def ask_again_after_failed_check(node: dict, nid: str, failures: list) -> dict:
    fields = [{"name": p["name"],
               "label": p.get("label") or p["name"],
               "type": p.get("type", "text"),
               "secret": p.get("type") == "secret",
               "options": p.get("options") or []}
              for p in node.get("outputs", [])]
    return {"lines": [f"step \"{node.get('name', nid)}\" needs a "
                      "different value: " + "; ".join(failures)],
            "input_request": {"prompt": node.get("description")
                              or node.get("name") or "Your choice",
                              "fields": fields}}
