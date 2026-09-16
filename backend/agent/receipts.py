# Re-proves every built step through the run's own machinery, side-effect-free, before a build goes green
from __future__ import annotations

import json as _json
import time

from agent import steps
from agent import gate
import step_types
from runtime import verifier
from step_types import reaches_outside

# Re-proves the built workflow through the run's own machinery without side effects, before it goes green
def check(workflow: dict, plan: dict) -> dict:
    findings: list[str] = []
    notes: list[str] = []
    by_name = {n.get("name"): n for n in workflow.get("nodes", [])}
    for pn in plan.get("nodes") or []:
        node = by_name.get(pn.get("name"))
        if not node:
            continue
        kind = step_types.contract(node.get("type")).get("receipt")
        if kind == "rehearsal":
            findings += _rehearse(workflow, node, notes)
        elif kind == "request-assembly":
            findings += _ai_request(node, notes)
        elif kind == "replay":
            from agent import cells as _cells
            if not _cells.latest_ok(workflow["id"],
                                    str(node.get("name") or ""), kind="code"):
                notes.append(f'"{node.get("name")}" was built without a '
                             "trial run - its first real run is the first test")
    findings += _seam_walk(workflow, plan, notes)
    findings += _settings_fit(workflow)
    scan = _credential_scan(workflow)
    findings += scan.pop("findings")
    return {"ok": not findings, "findings": findings, "notes": notes,
            "credential_scan": scan}

# The last thing every build does: scan every step for keys or passwords written in, and say the result out loud
def _credential_scan(workflow: dict) -> dict:
    from storage import secrets_store
    try:
        known = secrets_store.all_values()
    except Exception:
        known = []
    findings, scanned = [], 0
    for node in workflow.get("nodes", []) or []:
        cfg = node.get("config") or {}
        name = node.get("name") or node.get("id")

        for text in (cfg.get("code"), cfg.get("prompt")):
            if not str(text or "").strip():
                continue
            scanned += 1
            for hit in gate.find_hardcoded_secrets(text, known):
                findings.append(
                    f'step "{name}" has {hit} written into it. Store it as a '
                    "saved value and read it with get_secret(\"NAME\") - a credential in a step travels with the workflow, into its history and into anywhere it is published.")
        for t in cfg.get("tests") or []:
            blob = _json.dumps(t.get("inputs") or {}, default=str)
            for hit in gate.find_hardcoded_secrets(blob, known):
                findings.append(
                    f'the saved example for step "{name}" contains {hit}. '
                    "Replace the example value - a stored example travels with the workflow.")
    return {"ok": not findings, "steps": scanned, "findings": findings,
            "ts": time.time()}

# The stored value that will feed an input goes through the run's own input check at build time
def _settings_fit(workflow: dict) -> list[str]:
    from runtime import port_checks
    from storage import environments
    out: list[str] = []
    seen: set = set()
    resolution = environments.resolve(workflow)
    for n in workflow.get("nodes", []) or []:
        ports, values = [], {}
        for p in n.get("inputs", []) or []:
            name = p.get("name")
            r = resolution.get(name) if name else None
            if not name or name in seen or not r \
                    or r.get("secret") or r.get("ambiguous") \
                    or not environments.value_is_set(r.get("value")):
                continue
            ports.append(p)
            values[name] = r["value"]
        for prob in port_checks.check_ports(ports, values):
            name = prob.get("port")
            seen.add(name)
            p = next((q for q in ports if q.get("name") == name), {})
            label = (str(p.get("label") or "").strip()
                     or steps.humanise_name(name))
            out.append(f'step "{n.get("name")}" needs {label} to be a '
                       f'{prob.get("type")}, but it is set to '
                       f'{values.get(name)!r} - correct the value on the '
                       "Inputs tab, or change what the step expects")
    return out

# A pure read: notes what was tried and what was not - the build executes nothing
def _rehearse(workflow: dict, node: dict, notes: list) -> list[str]:
    from agent import cells
    from models import code_key
    name = str(node.get("name") or "")
    rec = cells.latest_ok(workflow["id"], name, kind="code")
    if not rec:
        notes.append(f'"{name}" was built without a trial run - its '
                     "first real run is the first test")
        return []
    if code_key(rec.get("code")) != code_key(
            node.get("config", {}).get("code", "")):
        notes.append(f'"{name}" changed since its trial run - the next real '
                     "run is what tries it")
    return []

def _ai_request(node: dict, notes: list) -> list[str]:
    from runtime import capability
    import providers
    name = str(node.get("name") or "")
    cfg = node.get("config", {}) or {}
    findings = []
    if not str(cfg.get("prompt") or "").strip():
        findings.append(f'step "{name}": no prompt configured')
    model = (cfg.get("model") or {}).get("model", "")
    if not model or not providers.is_ready(cfg.get("model") or {}):
        notes.append(f'"{name}" has no AI model set up yet - add a workflow '
                     "AI provider in Admin before running")
    try:
        capability.schema_from_ports(node.get("outputs") or [])
    except Exception as e:
        findings.append(f'step "{name}": the enforced output schema does not '
                        f"assemble from its ports - {type(e).__name__}: {e}")
    return findings

# Checks that each step's recorded output actually reaches the steps that read it, by name along real paths
def _seam_walk(workflow: dict, plan: dict, notes: list) -> list[str]:
    from agent import cells
    from agent import plan_logic
    from runtime import safe_eval
    findings = []
    by_name = {n.get("name"): n for n in workflow.get("nodes", [])}
    recorded: dict = {}

    # The step's raw recorded output - it speaks the code's own names, which is what the seam question means
    def _out_of(name: str):
        if name not in recorded:
            rec = cells.latest_ok(workflow["id"], str(name or ""))
            out = (rec or {}).get("output")
            if rec is None or (isinstance(out, dict)
                               and "$missing_evidence" in out):
                recorded[name] = False
            else:
                recorded[name] = out if isinstance(out, dict) else None
        return recorded[name]

    for e in plan.get("edges") or []:
        src, dst = e.get("src"), e.get("dst")
        when = str(e.get("when") or "")
        srcn = by_name.get(src)
        if not srcn or not by_name.get(dst):
            continue
        out = _out_of(str(src or ""))
        if out is False:
            if srcn.get("type") != "user-input":
                notes.append(f'the link "{src}" -> "{dst}" could not be '
                             "checked against a recorded value")
            continue
        if when and out is not None:
            try:
                safe_eval.evaluate(when, dict(out), verifier.FUNCTIONS)
            except Exception as ex:
                findings.append(f'seam {src} -> {dst}: the routing condition '
                                f'"{when}" does not evaluate on the recorded '
                                f"output ({type(ex).__name__}: {ex})")

    ancestors = plan_logic.ancestors_by_name(plan, workflow)
    for dstn in workflow.get("nodes", []):
        dname = dstn.get("name")
        for p in dstn.get("inputs", []):
            nm = p.get("name")
            if not nm or p.get("type") == "secret":
                continue
            srcs = [a for a in ancestors.get(dname, ())
                    if nm in {q.get("name")
                              for q in (by_name.get(a) or {}).get("outputs") or []}]
            if len(srcs) != 1:
                continue
            out = _out_of(srcs[0])
            if isinstance(out, dict) and nm not in out:
                findings.append(f'seam {srcs[0]} -> {dname}: the recorded '
                                f'output has no "{nm}" for the step that '
                                "reads it")
    return findings

_REPLAY_CAP = 5

# Exact structural equality with a float tolerance, so rounding never flags
def outputs_agree(candidate, incumbent, tol: float = 1e-9) -> bool:
    return _agree(candidate, incumbent, tol)

def _agree(a, b, tol: float) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_agree(a[k], b[k], tol) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_agree(x, y, tol) for x, y in zip(a, b))
    return a == b

# A changed step must still pass the recorded cases before its fix is accepted
def regression_check(node: dict, overrides=None, extra_cases=None, *,
                     workflow_id: str) -> dict:
    from runtime import corpus, executor
    if reaches_outside(node):
        return {"ok": False, "total": 0, "regressions": [], "changed": [],
                "error": "connectors are not replayed (would re-fire the external side effect); validate the payload node instead"}

    stored = list(corpus.cases(workflow_id, node["id"], "success"))[-_REPLAY_CAP:]
    cases = stored + list(extra_cases or [])

    if steps.node_type_of(node) == "ai":
        import providers
        m = (node.get("config") or {}).get("model") or {}
        mname = m.get("model") if isinstance(m, dict) else m
        if not mname or not providers.is_ready(m if isinstance(m, dict) else {"model": m}):
            return {"ok": True, "total": len(cases), "regressions": [],
                    "changed": [], "unverifiable": len(cases),
                    "note": "ai replays need a ready api-key provider - stored cases could not be verified here"}
    regressions, changed = [], []
    for c in cases:
        res = executor.run_isolated(node, c.get("inputs", {}), overrides,
                                    workflow_id=workflow_id)
        if res.get("status") != "completed":
            regressions.append({"inputs": c.get("inputs"),
                                "reason": res.get("reason", "halted")})
        elif "output" in c and not outputs_agree(res.get("output"), c.get("output")):
            changed.append({"inputs": c.get("inputs"),
                            "expected": c.get("output"), "got": res.get("output")})

    return {"ok": not regressions, "total": len(cases),
            "regressions": regressions, "changed": changed}
