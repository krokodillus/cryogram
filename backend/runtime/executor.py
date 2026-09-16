# Runs a built workflow forward-only, no AI involved; a value never crosses a step boundary until it passes that step's checks
from __future__ import annotations

import os
import ast
import copy
import json
import hashlib
import re
import uuid
from typing import Any, Optional

from runtime import capability
import config
from runtime import corpus
from storage import deliverables
from storage import deps
from runtime import env_checks
from storage import environments
from runtime import port_checks
from runtime import run_state
from runtime import diskguard
from runtime import sandbox
from storage import secrets_store
import threading
import time
from storage import store
from runtime import verifier
from models import canonicalise_refs, humanise_name, step_type
import step_types
from step_types import carries_code, reaches_outside, writes_outside

def _run_id() -> str:
    return f"run_{uuid.uuid4().hex[:8]}"

def _by_id(workflow: dict) -> dict:
    return {n["id"]: n for n in workflow["nodes"]}

# The steps in the order they run: a branch runs to its end before the next branch starts
def _execution_order(workflow: dict) -> list[str]:
    nodes = [n["id"] for n in workflow["nodes"]]
    index = {n: i for i, n in enumerate(nodes)}
    indeg = {n: 0 for n in nodes}
    succ: dict[str, list[str]] = {n: [] for n in nodes}
    for e in workflow.get("edges", []):
        if e["src"] in indeg and e["dst"] in indeg:
            succ[e["src"]].append(e["dst"])
            indeg[e["dst"]] += 1
    ready = [n for n in nodes if indeg[n] == 0]
    order = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        fresh = []
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                fresh.append(m)
        ready[:0] = sorted(fresh, key=index.get)

    return order + [n for n in nodes if n not in order]

def _entry_name(code: str) -> Optional[str]:
    m = re.search(r"def\s+(\w+)\s*\(", code or "")
    return m.group(1) if m else None

def _incoming_map(workflow: dict) -> dict[str, list]:
    inc: dict[str, list] = {}
    for e in workflow.get("edges", []) or []:
        inc.setdefault(e.get("dst"), []).append(e.get("src"))
    return inc

# Which steps came before each step along its own path; only those may supply its inputs
def _ancestors_map(workflow: dict) -> dict[str, set]:
    inc = _incoming_map(workflow)
    out: dict[str, set] = {}
    for n in workflow.get("nodes", []) or []:
        nid = n.get("id")
        seen: set = set()
        stack = list(inc.get(nid, []))
        while stack:
            s = stack.pop()
            if s in seen:
                continue
            seen.add(s)
            stack.extend(inc.get(s, []))
        out[nid] = seen
    return out

# Inputs resolve by name from steps that ran earlier on this step's own path, then run entries, then settings
def _gather_inputs(node: dict, run_id: str, entry: dict,
                   ancestors: Optional[set] = None,
                   varmap: Optional[dict] = None) -> tuple[Optional[dict], list]:
    rec = run_state.load(run_id) or {}
    done = rec.get("steps", {})
    path = rec.get("path", []) or []
    varmap = varmap or {}
    inputs: dict[str, Any] = {}
    missing: list[str] = []

    pool = list(path) if ancestors is None else [p for p in path if p in ancestors]
    overrides = rec.get("overrides") or {}
    for port in node.get("inputs", []):
        name = port["name"]
        if port.get("type") == "secret":
            inputs[name] = name
            continue

        if name in overrides:
            inputs[name] = overrides[name]
            continue
        resolved = False
        for pid in reversed(pool):
            if pid in done:
                up = run_state.output_of(run_id, pid)
                if isinstance(up, dict) and name in up:
                    inputs[name] = up[name]
                    resolved = True
                    break
        if resolved:
            continue
        if name in entry:
            inputs[name] = entry[name]
            continue
        if name in varmap:
            val = varmap[name]
            if val not in (None, ""):
                val, understood = environments.coerce_to_type(val, port.get("type"))
                if not understood:
                    missing.append(name)
                    continue
                inputs[name] = val
            elif port.get("optional"):
                inputs[name] = None
            else:
                missing.append(name)
            continue

        if port.get("optional"):
            inputs[name] = None
            continue

        return None, [name]
    return inputs, missing

# A file outside the kinds a port was built for, said in one sentence naming both kinds and the file
def _wrong_file_kind(node: dict, inputs: dict) -> str:
    from agent import steps as _steps
    from runtime.capability import _file_kind
    from storage import blobstore
    words = {"pdf": "a PDF", "image": "an image", "spreadsheet": "a spreadsheet",
             "document": "a Word or PowerPoint file", "text": "a text file", "other": "another kind of file"}
    for port in node.get("inputs") or []:
        if not isinstance(port, dict) or port.get("type") != "file":
            continue
        kinds = _steps.port_file_kinds(port)
        v = inputs.get(port.get("name"))
        if not kinds or not (isinstance(v, str) and v.startswith("blob:") and blobstore.exists(v)):
            continue
        st = blobstore.stat(v) or {}
        kind = _file_kind(st.get("mime") or "", st.get("name") or port.get("name") or "")
        if kind in kinds:
            continue
        built = " or ".join(words.get(k, k) for k in kinds)
        return (f"This workflow was built for {built}, and the file given is "
                f"{words.get(kind, kind)} ({st.get('name') or port.get('name')}). "
                f"Give it {built}, or ask in chat to change the workflow.")
    return ""

# The missing-value ask in the user's language - the port's own label and description, never a raw key
def _missing_fields(workflow: dict, node: dict, names: list,
                    secret: bool = False) -> list:
    ports = {p.get("name"): p for p in node.get("inputs", []) or []}
    varlabels = {v.get("name"): v.get("label")
                 for v in workflow.get("variables", []) or []}
    out = []
    for n in names:
        p = ports.get(n) or {}
        out.append({
            "name": n,
            "label": (str(p.get("label") or "").strip()
                      or str(varlabels.get(n) or "").strip()
                      or humanise_name(n)),
            "description": str(p.get("description") or "").strip(),
            "type": p.get("type") or "text",
            "options": p.get("options") or [],
            "secret": bool(secret or p.get("type") == "secret"),
        })
    return out

# The form for a send whose result never came back: fields the step's own checks pin down are prefilled and locked
def unverified_output_fields(node: dict, run_id: str) -> list:
    from runtime import safe_eval
    known: dict = {}
    for t in node.get("tests") or []:
        for a in (t.get("asserts") or []):
            known.update(safe_eval.known_equalities(a))
    ran_with = {}
    try:
        for earlier in (_load_inputs_for(run_id, node) or {}).items():
            ran_with[earlier[0]] = earlier[1]
    except Exception:
        ran_with = {}
    out = []
    for p in node.get("outputs", []) or []:
        name = p.get("name")
        if not name:
            continue
        fixed = known.get(name) or {}
        value, locked = "", False
        if "value" in fixed:
            value, locked = fixed["value"], True
        elif "input" in fixed and fixed["input"] in ran_with:
            value, locked = ran_with[fixed["input"]], True
        out.append({
            "name": name,
            "label": str(p.get("label") or "").strip() or humanise_name(name),
            "description": str(p.get("description") or "").strip(),
            "type": p.get("type") or "text",
            "options": p.get("options") or [],
            "value": value,
            "locked": locked,
        })
    return out

# What the paused step was handed, read back from this run's checkpoints
def _load_inputs_for(run_id: str, node: dict) -> dict:
    rec = run_state.load(run_id) or {}
    pool: dict = {}
    for step in (rec.get("steps") or {}).values():
        out = step.get("output")
        if isinstance(out, dict):
            pool.update(out)
    pool.update(rec.get("entry_inputs") or {})
    return pool

# Every value the run would pause for, computed before it starts so the form can ask once
def missing_at_start(workflow: dict) -> list:
    from storage import environments
    resolution = environments.resolve(workflow)
    secret_map = environments.secret_owners(workflow)
    entryish: set = set()
    for n in workflow.get("nodes", []):
        t = n.get("type")
        t = t.value if hasattr(t, "value") else str(t)
        if t == "user-input":
            entryish.update(p.get("name") for p in n.get("outputs", []) or [])
    by_id = {n["id"]: n for n in workflow.get("nodes", [])}
    produced: set = set()
    out, seen = [], set()
    for nid in _execution_order(workflow):
        node = by_id.get(nid)
        if node is None:
            continue
        t = node.get("type")
        t = t.value if hasattr(t, "value") else str(t)
        if t != "user-input":
            code = (node.get("config") or {}).get("code", "")
            names = ({p.get("name") for p in node.get("inputs", []) or []
                      if p.get("type") == "secret"} | _secret_literals(code))
            for s in sorted(x for x in names if x):
                if s not in seen and not _secret_val(s, None, secret_map):
                    seen.add(s)
                    out.append(_missing_fields(workflow, node, [s],
                                               secret=True)[0])
        for p in node.get("inputs", []) or []:
            name = p.get("name")
            if (not name or name in seen or p.get("type") == "secret"
                    or p.get("optional")
                    or name in produced or name in entryish):
                continue
            r = resolution.get(name)
            if r and not r["secret"] and not r["ambiguous"] \
                    and r["value"] in (None, ""):
                seen.add(name)
                out.append(_missing_fields(workflow, node, [name])[0])
        produced.update(p.get("name")
                        for p in node.get("outputs", []) or [])
    return out

def _criteria(node: dict) -> list:
    return node.get("config", {}).get("criteria", []) or []

# Fits a step's raw result to its declared outputs, wrapping a lone value into a single port
def _shape_output(node: dict, raw: Any) -> dict:
    shape = step_types.function(node["type"], "shape_output")
    if shape:
        return shape(node, raw)
    ports = node.get("outputs", [])
    if len(ports) == 1:
        if isinstance(raw, dict) and set(raw.keys()) == {ports[0]["name"]}:
            return raw
        return {ports[0]["name"]: raw}
    if isinstance(raw, dict):
        return raw
    return {p["name"]: raw for p in ports}

def _secret_literals(code: str) -> set:
    return set(environments.SECRET_LITERAL_RE.findall(code or ""))

# Resolves a secret by name at the last moment: per-run values first, then the encrypted store
def _secret_val(name: str, secret_inputs: Optional[dict],
                secret_map: Optional[dict] = None) -> Optional[str]:
    si = secret_inputs or {}
    if name in si and si[name] not in (None, ""):
        return si[name]
    owner = (secret_map or {}).get(name)
    if not owner:
        return None
    return secrets_store.get_secret(name, owner)

_PROSE_SCAN_CHARS = 600

# The items whose side effects have already happened - what makes the next run continue instead of re-sending
def _progress(path) -> dict:
    import json as _json
    try:
        with open(path) as f:
            got = _json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}

# How far a per-item step got before it stopped: done items, remaining items and where the list came from
def _partial_progress(node: dict, inputs: dict, path, run_id: str,
                      entry: dict) -> dict:
    pi = (node.get("config") or {}).get("per_item") or {}
    name, key = pi.get("input"), pi.get("key")
    items = (inputs or {}).get(name) if name else None
    if not isinstance(items, list) or not items:
        return {}
    done = _progress(path)

    def _k(item):
        if isinstance(item, dict) and key:
            return str(item.get(key))
        return str(item)

    keys = [_k(x) for x in items]
    done_keys = [k for k in keys if k in done]
    remaining = [k for k in keys if k not in done]
    prog = {"input": name, "key": key, "done": len(done_keys), "of": len(keys),
            "remaining": len(remaining), "done_keys": done_keys}
    rec = run_state.load(run_id) or {}
    src = None
    for pid in reversed(rec.get("path") or []):
        out = (rec.get("steps") or {}).get(pid, {}).get("output")
        if isinstance(out, dict) and name in out:
            src = {"step": pid}
            break
    if src is None and name in (entry or {}):
        src = {"entry": True}
    if src is None:
        src = {"variable": True}
        prog["items"] = items
    prog["source"] = src
    return prog

# Stamps how far a per-item step got onto the halt and the buffer
def _attach_progress(halt: dict, node: dict, inputs: dict, path, run_id: str,
                     entry: dict) -> dict:
    prog = _partial_progress(node, inputs, path, run_id, entry)
    if prog:
        halt["progress"] = {k: v for k, v in prog.items() if k != "items"}
        run_state.note_progress(run_id, prog)
    return halt

# An already-open browser window on the workflow's profile pauses the run with a plain ask to close it
def _window_lock_halt(run_id: str, nid: str, node: dict, err):
    head = str(err or "")[:_PROSE_SCAN_CHARS]
    if not (reaches_outside(node) and ("ProcessSingleton" in head
                                       or "SingletonLock" in head)):
        return None
    name = node.get("name") or "this step"
    return _pause(run_id, nid, "browser-window-open", [
                f'The step "{name}" needs the browser, but a Chrome window for this '
                "workflow is already open - maybe from an earlier login. Close that window, then run again to continue from this step."])

# An error a step's own type knows how to read - a closed window, a question nobody answered - becomes its pause
def _type_reads_error(run_id: str, nid: str, node: dict, err):
    kind = getattr(err, "kind", "")
    for rule, args in (("read_closed_window", (kind, str(err or "")[:_PROSE_SCAN_CHARS])),
                       ("read_unanswered_question", (kind, str(err or "")))):
        read = step_types.function(node.get("type"), rule)
        decision = read(node, *args) if read else None
        if decision:
            return _carry_out(decision, run_id, "", nid, node, {})
    return None

def _read_input_literal(expr) -> Optional[str]:
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) \
            and expr.func.id == "read_input" and expr.args \
            and isinstance(expr.args[0], ast.Constant) \
            and isinstance(expr.args[0].value, str):
        return expr.args[0].value
    return None

# Reads the step's code to find which list inputs it loops over - those are its work lists
def iterated_input_names(code: str) -> set:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return set()
    assigned: dict = {}
    assigns = [n for n in ast.walk(tree)
               if isinstance(n, ast.Assign) and len(n.targets) == 1
               and isinstance(n.targets[0], ast.Name)]
    for n in assigns:
        name = _read_input_literal(n.value)
        if name:
            assigned[n.targets[0].id] = name

    def _alias_of(expr):
        direct = _read_input_literal(expr)
        if direct:
            return direct
        if isinstance(expr, ast.IfExp):
            return _alias_of(expr.body) or _alias_of(expr.orelse)
        if isinstance(expr, ast.BoolOp):
            for v in expr.values:
                found = _alias_of(v)
                if found:
                    return found
            return None
        while isinstance(expr, ast.Subscript):
            expr = expr.value
        direct = _read_input_literal(expr)
        if direct:
            return direct
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) \
                and expr.func.id in ("enumerate", "sorted", "list", "reversed") and expr.args:
            return _alias_of(expr.args[0])
        if isinstance(expr, ast.Name):
            return assigned.get(expr.id)
        return None

    for _ in range(len(assigns) + 1):
        grew = False
        for n in assigns:
            if n.targets[0].id in assigned:
                continue
            via = _alias_of(n.value)
            if via:
                assigned[n.targets[0].id] = via
                grew = True
        if not grew:
            break

    def _named(expr):
        return _alias_of(expr)

    out = set()
    for n in ast.walk(tree):
        iters = []
        if isinstance(n, ast.For):
            iters = [n.iter]
        elif isinstance(n, (ast.ListComp, ast.SetComp, ast.GeneratorExp,
                            ast.DictComp)):
            iters = [g.iter for g in n.generators]
        for it in iters:
            name = _named(it)
            if name:
                out.add(name)
    return out

# Which steps produce these input names - for saying which step found nothing
def _producers_of(workflow: dict, node: dict, names: list) -> list:
    out = []
    for n in workflow.get("nodes", []) or []:
        if n.get("id") == node.get("id"):
            continue
        if any(p.get("name") in names for p in (n.get("outputs") or [])):
            out.append(n.get("name") or n.get("id"))
    return out

# A sending step that was given items and produced nothing, with no text saying why, stops the run
def _no_effect(node: dict, inputs: dict, output) -> bool:
    if not isinstance(output, dict):
        return False
    given = [v for v in (inputs or {}).values() if isinstance(v, (list, tuple)) and v]
    if not given:
        return False
    outs = node.get("outputs") or []
    if not outs:
        return False
    def _empty(v):
        return v is None or v == [] or v == {} or v == 0 or v is False \
            or (isinstance(v, str) and not v.strip())
    for p in outs:
        v = output.get(p.get("name"))
        if not _empty(v):
            return False
    return True

# A service that pushed back or never answered, read by the step's own type, becomes its pause
def _service_halt(run_id: str, nid: str, node: dict, err):
    head = str(err or "")[:_PROSE_SCAN_CHARS]
    for rule in ("read_slow_down", "read_no_answer"):
        read = step_types.function(node.get("type"), rule)
        decision = read(node, head, _service_said(err)) if read else None
        if decision:
            return _carry_out(decision, run_id, "", nid, node, {})
    return None

# What the step saw and had written when it failed, scrubbed, for the halt and the issue
def _what_it_saw(err) -> dict:
    detail = getattr(err, "detail", None) or {}
    saw = dict(_scrub(detail.get("saw")) or {}) if detail.get("saw") else {}
    written = detail.get("written")
    if isinstance(written, dict) and written:
        saw["written"] = {k: deliverables.trace_preview(_scrub(v))
                          for k, v in written.items()}

    files = detail.get("files")
    if isinstance(files, dict) and files:
        saw["files"] = {str(k): str(v) for k, v in files.items()}
    return saw

# The first line of what the service actually said, so a pause never hides the real message
def _service_said(err) -> str:
    head = str(err or "").strip().splitlines()
    head = head[0].strip() if head else ""
    head = str(_scrub(head))[:200]
    return f" (The service said: {head})" if head else ""

# A step value as the run history shows it: scrubbed of secrets, then capped with visible markers
def step_preview(value, owner: Optional[str] = None):
    return deliverables.trace_preview(_scrub(value), owner=owner)

# Attaches the actual rows that missed the expected shape, bounded, so the popup shows data instead of codes
def _offending(problems: list, output) -> list:
    out = []
    for prob in problems or []:
        port = prob.get("port")
        val = (output or {}).get(port) if isinstance(output, dict) else None
        idx = prob.get("failing_items")
        if isinstance(idx, list) and isinstance(val, list):
            per = {}
            for d in prob.get("detail") or []:
                head = str(d.get("path") or "").split(" > ")[0]
                per.setdefault(head, []).append(d.get("problem"))
            rows = []
            for i in idx:
                rows.append({"item": i + 1, "value": _scrub(val[i]),
                             "problems": per.get(f"item {i + 1}") or []})
            out.append({"port": port, "total": len(idx), "of": len(val),
                        "rows": rows})
        else:
            out.append({"port": port, "total": 1, "of": 1,
                        "rows": [{"value": _scrub(val),
                                  "problems": [d.get("problem")
                                               for d in prob.get("detail") or []]
                                  or [prob.get("problem")]}]})
    return out

# A found or expected value as a person would read it
def _plain_list(v) -> str:
    if isinstance(v, dict):
        v = list(v.keys())
    if isinstance(v, (list, tuple)):
        names = [str(x) for x in v]
        if not names:
            return "nothing"
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    s = str(v)
    return s if len(s) <= 120 else s[:117] + "..."

# Carries out a stop or a pause that a step type decided on
def _carry_out(decision: dict, run_id: str, corpus_pid: str, nid: str,
               node: dict, inputs: dict) -> dict:
    if "pause" in decision:
        p = decision["pause"]
        return _pause(run_id, nid, p["reason"], p["lines"])
    h = decision["halt"]
    res = _halt(run_id, corpus_pid, node, inputs, verdict=h["verdict"],
                reason=h["reason"], **({"output": h["output"]} if "output" in h else {}))
    if decision.get("ai_failure"):
        res["ai_failure"] = _ai_failure(decision["ai_failure"], node, inputs,
                                        decision.get("elapsed"))
    return res

# A structured record of how an AI step failed: mode, timing, sizes and model, for the fix that follows
def _ai_failure(mode: str, node: dict, inputs: dict,
                elapsed: Any = None) -> dict:
    import json as _json
    lists = [len(v) for v in (inputs or {}).values()
             if isinstance(v, (list, tuple))]
    block = {"mode": mode,
             "timeout_s": capability.step_ai_timeout(node),
             "max_tokens": capability.step_max_tokens(node),
             "input_chars": len(_json.dumps(_scrub(inputs), default=str)),
             "batch_items": max(lists) if lists else None,
             "model": ((node.get("config") or {}).get("model") or {})
             .get("model", "")}
    if elapsed is not None:
        block["elapsed_s"] = elapsed
    return block

# One spend row per live AI call - counts only, never money; telemetry never breaks a run
def _record_ai_usage(node: dict, workflow_id: str, run_id: str, usage: dict) -> None:
    try:
        import providers
        from storage import db
        ref = (node.get("config") or {}).get("model") or {}
        model = ref.get("model", "")
        owner = providers.find_model(model, for_node=True,
                                     provider_id=ref.get("provider_id", ""))[0]
        db.ai_usage_add(provider_id=owner.get("id", ""), model=model,
                        workflow_id=workflow_id, run_id=run_id,
                        node_id=node.get("id", ""),
                        tokens_in=usage.get("in", 0),
                        tokens_out=usage.get("out", 0), source="run")
    except Exception:
        pass

# Runs one step: AI steps call the model in this process, everything else runs in the sandbox subprocess
def _execute(node: dict, inputs: dict, entry: dict,
             python_exe: Optional[str] = None,
             profile_dir: Optional[str] = None,
             blob_owner: Optional[str] = None,
             egress_extra: Optional[list] = None,
             secret_inputs: Optional[dict] = None,
             secret_map: Optional[dict] = None,
             should_stop=None, rehearse: bool = False,
             checkpoint_file: Optional[str] = None,
             on_progress=None,
             path_roots: Optional[list] = None,
             on_user_request=None, report: Optional[dict] = None) -> tuple[Any, bool]:
    ntype = node["type"]

    own_run = step_types.function(ntype, "run")
    if own_run:
        return own_run(node, inputs, entry, capability)

    code = node.get("config", {}).get("code", "")
    secret_vals = {p["name"]: _secret_val(p["name"], secret_inputs, secret_map)
                   for p in node.get("inputs", []) if p.get("type") == "secret"}

    for n in _secret_literals(code):
        if n not in secret_vals:
            v = _secret_val(n, secret_inputs, secret_map)
            if v is not None:
                secret_vals[n] = v

    roots = sorted(set(path_roots or [])
                   | {os.path.abspath(os.path.expanduser(str(x)))
                      for x in ((node.get("config") or {}).get("paths") or [])
                      if str(x).strip()})

    may_network = step_types.may(node.get("type") or "code", "network")
    report = report if isinstance(report, dict) else {}
    out = sandbox.run(code, _entry_name(code), inputs, secret_vals,
                      python_exe=python_exe, report=report,
                      profile_dir=profile_dir, blob_owner=blob_owner,
                      extra_allowlist=egress_extra, network=may_network,
                      browser=step_types.may(node.get("type") or "code", "browser"),
                      browser_url=str((node.get("config") or {}).get("url") or ""),
                      browser_options=dict((node.get("config") or {}).get("browser_options") or {}),
                      output_ports=node.get("outputs") or [],
                      should_stop=should_stop,
                      rehearse=rehearse, checkpoint_file=checkpoint_file,
                      on_progress=on_progress, path_roots=roots,
                      on_user_request=on_user_request,
                      timeout=sandbox.clamp_timeout(
                          node.get("config", {}).get("timeout_seconds")))

    fired = writes_outside(node) and _send_verdict(report.get("calls"))["fired"]
    return (out, fired)

SEND_METHODS = ("POST", "PUT", "PATCH", "DELETE")

# The sends of a step judged by kind of request: the kinds refused outright, the kinds partly refused, and whether anything fired
def _send_verdict(calls) -> dict:
    groups = (calls or {}).get("groups") if isinstance(calls, dict) else None
    out = {"refused": [], "partial": [], "ok": [], "counted": False, "fired": True}
    if not isinstance(groups, dict):
        return out
    for key, g in groups.items():
        if str(g.get("method") or "").upper() not in SEND_METHODS or not g.get("sent"):
            continue
        out["counted"] = True
        sent, ok = int(g.get("sent") or 0), int(g.get("ok") or 0)
        statuses = dict(g.get("statuses") or {})
        unanswered = sent - sum(int(n) for n in statuses.values())
        row = {"kind": key, "host": g.get("host") or "", "sent": sent, "ok": ok,
               "statuses": statuses, "unanswered": max(0, unanswered)}
        if ok == 0:
            out["refused"].append(row)
        elif ok < sent:
            out["partial"].append(row)
        else:
            out["ok"].append(row)
    if out["counted"]:
        out["fired"] = not out["refused"] and bool(out["ok"] or out["partial"])
        if out["refused"] and out["partial"]:
            out["fired"] = True
    return out

def _codes_phrase(row: dict) -> str:
    parts = [f"HTTP {code} on {n} of {row['sent']}"
             for code, n in sorted(row["statuses"].items(), key=lambda kv: -kv[1])
             if not (str(code).isdigit() and 200 <= int(code) < 300)]
    if row.get("unanswered"):
        parts.append(f"no answer on {row['unanswered']} of {row['sent']} (remote end closed the connection)")
    return ", ".join(parts)

# One sentence naming what the refused or partly refused sends met, in the words the service readers route on
def _send_sentence(verdict: dict) -> str:
    bits = []
    for row in verdict["refused"]:
        bits.append(f"every send of {row['kind']} was refused: {_codes_phrase(row)}")
    for row in verdict["partial"]:
        bits.append(f"{row['sent'] - row['ok']} of {row['sent']} sends of {row['kind']} failed: {_codes_phrase(row)}")
    hosts = {r["host"] for r in verdict["refused"] + verdict["partial"] if r.get("host")}
    return "; ".join(bits) + (f" (from {', '.join(sorted(hosts))})" if hosts else "")

# The run-history line for a sending step: per kind of send, how many answered 2xx and what the rest met
def _sends_line(calls: dict) -> str:
    verdict = _send_verdict(calls)
    rows = verdict["ok"] + verdict["partial"] + verdict["refused"]
    if not rows:
        total, ok = int(calls.get("total") or 0), int(calls.get("ok") or 0)
        return f"{ok} of {total} requests answered 2xx"
    parts = []
    for row in rows:
        line = f"{row['kind']}: {row['ok']} of {row['sent']} answered 2xx"
        rest = _codes_phrase(row)
        if rest:
            line += f"; {rest}"
        parts.append(line)
    return " | ".join(parts)

# The whole run, with the provider registry pinned for its duration
def run_workflow(workflow: dict, entry_inputs: dict, run_id: Optional[str] = None,
                 approvals: Optional[set] = None, emit=None, should_stop=None,
                 secret_inputs: Optional[dict] = None,
                 run_values: Optional[dict] = None,
                 on_user_request=None) -> dict:
    import providers
    _note_run_secrets(secret_inputs)
    try:
        with providers.pinned_registry():
            return _run_workflow(workflow, entry_inputs, run_id, approvals, emit,
                                 should_stop, secret_inputs, run_values,
                                 on_user_request)
    finally:
        _note_run_secrets(None)

# The run body: fresh or resumed, with per-step progress events and a clean stop between steps
def _run_workflow(workflow: dict, entry_inputs: dict, run_id: Optional[str] = None,
                  approvals: Optional[set] = None, emit=None, should_stop=None,
                  secret_inputs: Optional[dict] = None,
                  run_values: Optional[dict] = None,
                  on_user_request=None) -> dict:
    approvals = approvals or set()
    emit = emit or (lambda ev: None)
    should_stop = should_stop or (lambda: False)

    canonicalise_refs(workflow)
    by_id = _by_id(workflow)
    pid = workflow["id"]

    corpus_pid = workflow.get("corpus_workflow_id") or pid
    python_exe = deps.python_for(corpus_pid)

    profile_dir = str(config.browser_profile_dir(corpus_pid))
    _owner = secrets_store.workflow_owner(corpus_pid)

    egress_extra = workflow.get("egress_allowlist")
    if egress_extra is None and pid.startswith("slice_"):
        egress_extra = (store.load(corpus_pid) or {}).get("egress_allowlist")

    resolution = environments.resolve(workflow)

    base_roots = sandbox.workflow_roots(workflow)
    varmap: dict[str, Any] = {n: r["value"] for n, r in resolution.items()
                              if not r["secret"] and not r["ambiguous"]}
    secret_map: dict[str, str] = environments.secret_owners(workflow)

    for n, v in ((run_values or {}).get("values") or {}).items():
        if n not in resolution or resolution[n].get("value") in (None, ""):
            varmap[n] = v
    if (run_values or {}).get("secrets"):
        merged = dict((run_values or {}).get("secrets") or {})
        merged.update({k: v for k, v in (secret_inputs or {}).items()
                       if v not in (None, "")})
        secret_inputs = merged

    varmap, file_problems = environments.stage_files(varmap, workflow)

    if run_id and run_state.load(run_id) \
            and (run_state.load(run_id) or {}).get("workflow_id") != workflow["id"]:
        raise ValueError("run not found")
    resuming = bool(run_id and run_state.load(run_id))
    if resuming:
        if entry_inputs:
            run_state.merge_entry(run_id, entry_inputs)
        entry = run_state.entry_inputs(run_id)
    else:
        run_id = run_id or _run_id()
        run_state.start(pid, run_id, entry_inputs)
        entry = entry_inputs

    env_problems = (file_problems + env_checks.check_run(workflow, entry)
                    + env_checks.check_variables(workflow)
                    + env_checks.check_all_nodes(workflow))
    if env_problems:
        return _pause(run_id, None, "environment-check-failed", env_problems)

    edges_in: dict[str, list] = {}
    for e in workflow.get("edges", []):
        edges_in.setdefault(e["dst"], []).append(e)

    ancestors = _ancestors_map(workflow)

    def _edge_live(e: dict) -> tuple:
        if not run_state.is_done(run_id, e["src"]):
            return False, None
        when = e.get("when") or ""
        if not when:
            return True, None
        out = run_state.output_of(run_id, e["src"])
        ok, err = verifier._evaluate_expr(when, dict(out) if isinstance(out, dict) else {})
        if err is not None:
            return False, err
        return bool(ok), None

    # One forward pass over the order; None means it ran to the end
    def _sweep() -> Optional[dict]:
        for nid in _execution_order(workflow):
            node = by_id[nid]
            if run_state.is_done(run_id, nid):
                emit({"type": "node-done", "node": nid, "cached": True})
                continue
            if run_state.has_fired(run_id, nid) \
                    and not _progress(config.checkpoint_file(f"{run_id}_{nid}")):
                return _pause(run_id, nid, "fired-unverified", [
                    f"step \"{node.get('name', nid)}\" already sent its "
                    "data to the outside system, but the result never passed its checks - resuming would send it AGAIN. Check the outside system for what arrived, then start a fresh run."])

            incoming = edges_in.get(nid, [])
            if incoming:
                lives = [(e, *_edge_live(e)) for e in incoming]
                broken = next(((e, err) for e, live, err in lives if err is not None), None)
                if broken:
                    e, err = broken
                    src_name = by_id.get(e["src"], {}).get("name") or e["src"]
                    return _halt(run_id, corpus_pid, node, {},
                                 verdict={"branch": [
                                     f'The condition on the connection from "{src_name}" to '
                                     f'"{node.get("name", nid)}" could not be decided: {err}. '
                                     "The run stopped rather than skip the step. Ask in the chat to fix the condition."]},
                                 reason="branch-condition-error")
                if not any(live for _e, live, _err in lives):
                    run_state.note_skip(run_id, nid, "branch-not-taken")
                    emit({"type": "node-skip", "node": nid})
                    continue

            inputs, missing = _gather_inputs(node, run_id, entry,
                                             ancestors.get(nid), varmap)
            if inputs is not None:
                wrong = _wrong_file_kind(node, inputs)
                if wrong:
                    return _pause(run_id, nid, "wrong-file-kind", [wrong])
            if inputs is None:
                has_when_in = any((e.get("when") or "").strip()
                                  for e in edges_in.get(nid, []))
                port = (missing or ["its input"])[0]
                if has_when_in:
                    run_state.note_skip(run_id, nid, "upstream-skipped")
                    emit({"type": "node-skip", "node": nid})
                    continue
                return _pause(run_id, nid, "input-unconnected", [
                    f"step \"{node.get('name', nid)}\" never received "
                    f"\"{port}\" - no step or stored value supplies "
                    "it. The workflow's wiring is incomplete: ask in chat to fix the connection."])
            if missing:
                return _pause(run_id, nid, "missing-value", [
                    f"step \"{node.get('name', nid)}\" needs "
                    f"a value for: {', '.join(missing)} - "
                    "provide it to continue this run, or set it under Inputs"],
                    missing_values=missing,
                    missing_fields=_missing_fields(workflow, node, missing))

            ask = step_types.function(node["type"], "ask_mid_run")
            if ask:
                req = ask(node, nid, entry, inputs, bool(incoming),
                          lambda name: bool(_secret_val(name, secret_inputs, secret_map)))
                if req:
                    return _pause(run_id, nid, "input-needed", req["lines"],
                                  input_request=req["input_request"])

            if should_stop():
                return _pause(run_id, nid, "stopped-by-user", ["Stopped by the user."])

            emit({"type": "node-start", "node": nid})

            in_problems = port_checks.check_ports(node.get("inputs", []), inputs)
            if in_problems:
                res = _halt(run_id, corpus_pid, node, inputs,
                            verdict={"standard_input_check":
                                     port_checks.problem_lines(in_problems)},
                            reason="input-check-failed")
                res["offending"] = _offending(in_problems, inputs)
                return res

            node_env = env_checks.check_node(node)
            if node_env:
                return _pause(run_id, nid, "environment-check-failed", node_env)

            missing = [p["name"] for p in node.get("inputs", [])
                       if p.get("type") == "secret"
                       and not _secret_val(p["name"], secret_inputs, secret_map)]

            if carries_code(node):
                missing += [n for n in sorted(_secret_literals(
                                node.get("config", {}).get("code", "")))
                            if n not in missing
                            and not _secret_val(n, secret_inputs, secret_map)]
            if missing:
                return _pause(run_id, nid, "missing-value", [
                    f"step \"{node.get('name', nid)}\" needs a "
                    f"key or password for: {', '.join(missing)}"],
                    missing_secrets=missing,
                    missing_fields=_missing_fields(workflow, node, missing, secret=True))

            if writes_outside(node):
                lists = {k: v for k, v in inputs.items() if isinstance(v, (list, tuple))}

                work = iterated_input_names(
                    node.get("config", {}).get("code", "")) & set(lists)
                judged = {k: lists[k] for k in work} if work else lists
                if judged and all(len(v) == 0 for v in judged.values()):
                    empty = sorted(judged)
                    producers = _producers_of(workflow, node, empty)
                    who = (f'"{producers[0]}"' if len(producers) == 1
                           else "the earlier steps")
                    return _halt(run_id, corpus_pid, node, inputs,
                                 verdict={"nothing_to_do": [
                                     f"{who} found nothing for "
                                     f"\"{node.get('name', nid)}\" to act on ("
                                     + ", ".join(empty) + " came back empty), so the run stopped before sending anything. If that is expected this run - nothing left to do - dismiss this; otherwise check the data it reads, or tell me what to change."]},
                                 reason="nothing-to-do")

            shown_digest = run_state.preview_digest(run_id, nid)
            digest_now = _inputs_digest(inputs)
            approved = nid in approvals and (shown_digest is None or shown_digest == digest_now)
            if writes_outside(node) and not node.get("approval_suppressed") \
                    and not approved:
                run_state.note_preview(run_id, nid, digest_now)
                return _halt(run_id, corpus_pid, node, inputs,
                             verdict={"approval": "write connector awaiting approval",

                                      "sending": sending_preview(node, inputs)},
                             reason="awaiting-approval")

            ckpt = config.checkpoint_file(f"{run_id}_{nid}")
            step_usage = None

            step_notes: list = []
            report: dict = {}
            try:
                raw, fired = _execute(node, inputs, entry, python_exe=python_exe,
                                      report=report,
                                      blob_owner=_owner,
                                      profile_dir=profile_dir,
                                      path_roots=base_roots,
                                      egress_extra=egress_extra,
                                      secret_inputs=secret_inputs,
                                      secret_map=secret_map,
                                      should_stop=should_stop,
                                      checkpoint_file=str(ckpt),
                                      on_progress=step_notes.append,

                                      on_user_request=(
                                          on_user_request
                                          if step_types.may(node.get("type"), "browser")
                                          else None))

                calls = report.get("calls") if isinstance(report.get("calls"), dict) else {}
                verdict = _send_verdict(calls) if writes_outside(node) else None
                if verdict and (verdict["refused"] or verdict["partial"]):
                    raise sandbox.NodeError("SendRefused" if verdict["refused"] else "SendPartial",
                                            _send_sentence(verdict),
                                            detail={"sent": bool(verdict["fired"]), "calls": calls})
                if writes_outside(node) and calls.get("total"):
                    step_notes.append(_sends_line(calls))

                spent = step_types.function(node["type"], "record_usage")
                if spent:
                    spent(node, capability,
                          lambda usage: None if pid.startswith("slice_")
                          else _record_ai_usage(node, corpus_pid, run_id, usage))
                if fired:
                    run_state.record_fired(run_id, nid)
                output = _shape_output(node, raw)
            except NotImplementedError:
                raise
            except sandbox.NodeStopped as e:
                if writes_outside(node) and getattr(e, "sent", False):
                    run_state.record_fired(run_id, nid)

                return _pause(run_id, nid, "stopped-by-user", ["Stopped by the user."])
            except sandbox.NodeError as e:
                if writes_outside(node) and (e.detail or {}).get("sent"):
                    run_state.record_fired(run_id, nid)

                if e.kind == "InputContractError":
                    return _halt(run_id, corpus_pid, node, inputs,
                                 verdict={"standard_input_check": [str(e)]},
                                 reason="input-contract-failed")
                if e.kind == "UnexpectedInputError":
                    found = (e.detail or {}).get("found")
                    expected = (e.detail or {}).get("expected")
                    line = str(e).strip().rstrip(".") or "the input is not the kind of thing this step reads"
                    if expected is not None:
                        line += f" - it needs {_plain_list(expected)}"
                    if found is not None:
                        line += f"; what arrived has {_plain_list(found)}"
                    res = _halt(run_id, corpus_pid, node, inputs,
                                verdict={"input_shape": [line + "."]},
                                reason="input-shape")
                    res["offending"] = [{"port": None, "total": 1, "of": 1,
                                         "rows": [{"value": {"found": _scrub(found),
                                                             "expected": expected},
                                                   "problems": [str(e).strip()]}]}]
                    return res

                if "CERTIFICATE_VERIFY_FAILED" in str(e):
                    return _pause(run_id, nid, "environment-check-failed", [
                        "this computer's Python cannot verify secure connections - on macOS run 'Install Certificates.command' from your Python folder, or install the certifi package, then retry from this step"])

                if diskguard.BLOCK_PREFIX in str(e):
                    return _pause(run_id, nid, "path-blocked", [
                        f"{e} - this workflow may only read the folders "
                        "it declares or you allowed. Ask in chat to allow the folder for this workflow, then retry from this step."])
                if "egress blocked" in str(e):
                    return _pause(run_id, nid, "egress-blocked", [
                        f"{e} - this workflow may only reach addresses "
                        "on its allowed list. Ask in chat to allow the domain for this workflow, or add it to the global list in Admin, then retry from this step."])

                emit({"type": "node-error", "node": nid,
                      "error": str(_scrub(f"{e.kind}: {e}"))[:1200]})
                if e.kind == "NoProgress":
                    secs = (e.detail or {}).get("timeout_seconds") or "the allowed"
                    nm = node.get("name") or "this step"
                    halt = _halt(run_id, corpus_pid, node, inputs,
                                 verdict={"no_progress": [
                                     f'The step "{nm}" reported no progress for '
                                     f"{secs} seconds, the most a step may go "
                                     "quiet, so it was stopped. Run again from this step, or ask in the chat to give it more time. What it has done is kept below."]},
                                 reason="no-progress")
                else:
                    halt = _special_halt(run_id, nid, node, ckpt, e) or _halt(
                        run_id, corpus_pid, node, inputs,
                        verdict={"thrown": f"{e.kind}: {e}"},
                        reason=f"node threw: {e.kind}")
                saw = _what_it_saw(e)
                if saw:
                    halt["saw"] = saw
                return _attach_progress(halt, node, inputs, ckpt, run_id, entry)
            except Exception as e:
                emit({"type": "node-error", "node": nid,
                      "error": str(_scrub(f"{type(e).__name__}: {e}"))[:1200]})
                halt = _special_halt(run_id, nid, node, ckpt, e) or _halt(
                    run_id, corpus_pid, node, inputs,
                    verdict={"thrown": f"{type(e).__name__}: {e}"},
                    reason=f"node threw: {type(e).__name__}")
                return _attach_progress(halt, node, inputs, ckpt, run_id, entry)

            read_failure = step_types.function(node["type"], "read_call_failure")
            failed = read_failure(node, raw, capability) if read_failure else None
            if failed:
                return _carry_out(failed, run_id, corpus_pid, nid, node, inputs)

            out_problems = port_checks.check_ports(node.get("outputs", []), output,
                                                   require_all=True)

            policy = str((node.get("config") or {}).get("on_invalid_items")
                         or "stop")
            if out_problems and policy in ("proceed", "log") \
                    and all(isinstance(pr.get("failing_items"), list)
                            for pr in out_problems):
                aside = _offending(out_problems, output)
                for pr in out_problems:
                    bad = set(pr["failing_items"])
                    output[pr["port"]] = [it for i, it in enumerate(output[pr["port"]])
                                          if i not in bad]
                run_state.note_set_aside(run_id, nid, policy, aside)
                out_problems = []
            if out_problems and all(pr.get("empty_output")
                                    for pr in out_problems):
                ports_e = [pr["port"] for pr in out_problems]
                names_e = ", ".join(f'"{p}"' for p in ports_e)
                res = _halt(run_id, corpus_pid, node, inputs,
                            verdict={"empty_output": [
                                f"\"{node.get('name', nid)}\" found nothing "
                                f"at all - {names_e} came back empty, and "
                                "every earlier run had items. Either what it was given was wrong (a link, a value), the source changed, or it blocked the visit. If an empty result can be normal here, say so and the run will treat it as a quiet day from now on."]},
                            reason="empty-output", output=output)
                res["empty_ports"] = ports_e
                return res
            if out_problems:
                res = _halt(run_id, corpus_pid, node, inputs,
                            verdict={"standard_output_check":
                                     port_checks.problem_lines(out_problems)},
                            reason="output-check-failed (standard)",
                            output=output)
                res["offending"] = _offending(out_problems, output)
                mode = step_types.function(node["type"], "output_check_failure_mode")
                if mode:
                    res["ai_failure"] = _ai_failure(mode(node), node, inputs)
                return res

            if writes_outside(node) and _no_effect(node, inputs, output):
                given = max((len(v) for v in inputs.values()
                             if isinstance(v, (list, tuple))), default=0)
                return _halt(run_id, corpus_pid, node, inputs,
                             verdict={"no_effect": [
                                 f"it was given {given} items and did nothing "
                                 "with any of them - and gave no reason. A step that skips or fails on items must say so per item (sent / skipped / failed and why); silence here usually means the real action quietly failed."]},
                             reason="no-effect", output=output)

            blank = step_types.function(node["type"], "blank_answer")
            empty = blank(node, output) if blank else None
            if empty:
                return _carry_out(empty, run_id, corpus_pid, nid, node, inputs)

            result = verifier.evaluate(output, _criteria(node), inputs=inputs)
            ask_again = step_types.function(node["type"], "ask_again_after_failed_check")
            if not result.ok and ask_again:
                req = ask_again(node, nid, result.hard_failures)
                return _pause(run_id, nid, "input-needed", req["lines"],
                              input_request=req["input_request"])
            if not result.ok:
                return _halt(run_id, corpus_pid, node, inputs,
                             verdict={"hard_failures": result.hard_failures},
                             reason="output-check-failed", soft=result.soft_flags,
                             output=output)

            try:
                ckpt.unlink(missing_ok=True)
            except OSError:
                pass
            step_trace = {"inputs": step_preview(inputs, _owner),
                          "output": step_preview(output, _owner)}
            if step_notes:
                step_trace["notes"] = step_notes[-deliverables.TRACE_NOTES:]
            run_state.record_step(run_id, nid, output, fired=fired,
                                  usage=step_usage, trace=step_trace)
            corpus.record_success(corpus_pid, nid, _scrub(inputs), _scrub(output),
                                  run_id=run_id, meta=_blob_meta(inputs, node, _owner))
            emit({"type": "node-done", "node": nid})
        return None

    halted = _sweep()
    if halted is not None:
        return halted

    rec = run_state.load(run_id)
    outputs = {nid: run_state.output_of(run_id, nid) for nid in rec["steps"]}

    usage = [{"node": nid, "name": by_id.get(nid, {}).get("name") or nid,
              "model": ((by_id.get(nid, {}).get("config") or {})
                        .get("model") or {}).get("model", ""),
              **rec["steps"][nid]["usage"]}
             for nid in rec.get("path", []) if rec["steps"].get(nid, {}).get("usage")]

    path = list(rec.get("path") or [])
    if not pid.startswith("slice_"):
        corpus.record_run_path(corpus_pid, path, run_id=run_id)

        trace = run_state.trace(rec)
        for e in trace:
            n0 = by_id.get(e.get("node")) or {}
            if n0:
                e["name"] = n0.get("name") or e["node"]
                e["type"] = n0.get("type")
        deliverables.persist(workflow, run_id, outputs, path, usage=usage,
                             started=rec.get("started"),
                             paused=bool(rec.get("paused")),

                             entry_inputs=rec.get("entry_inputs") or entry_inputs,
                             set_aside=rec.get("set_aside"),
                             steps=trace,

                             partial=rec.get("partial"))
    run_state.finish(run_id)
    result = {"run_id": run_id, "status": "completed", "path": path, "outputs": outputs}
    if rec.get("set_aside"):
        result["set_aside"] = rec["set_aside"]
    return result

# The halts a raised error can mean on its own, tried in one order: a window someone else holds, a window closed by hand, a person who never confirmed, a long step that got partway, a service that did not answer
def _special_halt(run_id: str, nid: str, node: dict, ckpt, e) -> Optional[dict]:
    return (_window_lock_halt(run_id, nid, node, e)
            or _type_reads_error(run_id, nid, node, e)
            or _service_halt(run_id, nid, node, e))

# A pause the run resumes from with nothing recorded: no corpus case, no issue; the lines say what to do
def _pause(run_id: str, nid: Optional[str], reason: str, lines: list, **verdict_extra) -> dict:
    run_state.halt(run_id, nid or "", reason)
    return {"run_id": run_id, "status": "halted", "halted_at": nid,
            "reason": reason, "verdict": {**verdict_extra, "environment": list(lines)}}

# Records why and where the run paused or stopped, with the step's inputs and output kept as evidence
def _halt(run_id: str, corpus_pid: str, node: dict, inputs: dict, verdict: dict,
         reason: str, soft: Optional[list] = None, output: Any = None) -> dict:
    nid = node["id"]

    case_id = None
    if reason != "awaiting-approval":
        case_id = corpus.record_failure(
            corpus_pid, nid, _scrub(inputs), run_id=run_id, verdict=_scrub(verdict),
            cause=reason,
            observed={"node_type": node["type"], "soft_flags": _scrub(soft or [])},
            meta=_blob_meta(inputs, node, secrets_store.workflow_owner(corpus_pid)),

            output=_scrub(output) if output is not None else None)
    run_state.halt(run_id, nid, reason)
    return {"run_id": run_id, "status": "halted", "halted_at": nid,
            "reason": reason, "case_id": case_id, "verdict": verdict}

# Describes blob inputs and stamps which version of the step produced the case, so a rewrite resets its counter
def _blob_meta(inputs: dict, node: Optional[dict] = None,
               owner: Optional[str] = None) -> Optional[dict]:
    from storage import blobstore
    blobs = {}
    for k, v in (inputs or {}).items():
        if isinstance(v, str) and v.startswith("blob:") and blobstore.exists(v):
            st = blobstore.stat(v, owner)
            blobs[k] = {"mime": st.get("mime", ""), "size": st.get("size"),
                        "name": (st.get("meta") or {}).get("name", "")}
    meta: dict = {}
    if blobs:
        meta["blobs"] = blobs
    if node is not None:
        meta["config_hash"] = corpus.config_hash(node)
    return meta or None

_KNOWN_TTL = 5.0
_known_cache: tuple[float, tuple, list] = (0.0, (), [])

# Secret values briefly memoised, keyed by the name list so a new secret refreshes immediately
def _known_secrets() -> list:
    global _known_cache
    from storage import db as _db
    now = time.time()
    rows = tuple((o, n) for o, n, _ in _db.secret_rows())
    if rows != _known_cache[1] or now - _known_cache[0] > _KNOWN_TTL:
        _known_cache = (now, rows, secrets_store.all_values())
    return _known_cache[2]

SENDING_SAMPLE_ITEMS = 2
SENDING_VALUE_CLIP = 180

# What a write step is about to send, scrubbed and clipped, secrets by name only
def sending_preview(node: dict, inputs: dict) -> list[dict]:
    import json as _json

    def _one_line(v) -> str:
        if isinstance(v, dict):
            txt = "; ".join(f"{k}: {vv}" for k, vv in list(v.items())[:6])
        else:
            txt = str(v)
        txt = " ".join(txt.split())
        return txt[:SENDING_VALUE_CLIP] + ("..." if len(txt) > SENDING_VALUE_CLIP
                                           else "")
    def _json_line(v) -> str:
        txt = _json.dumps(v, ensure_ascii=False, default=str)
        return txt[:SENDING_VALUE_CLIP] + ("..." if len(txt) > SENDING_VALUE_CLIP
                                           else "")
    out = []
    for p in node.get("inputs", []) or []:
        name = p.get("name")
        if not name or name not in (inputs or {}):
            continue
        label = str(p.get("label") or "").strip() \
            or name.replace("_", " ").capitalize()
        if p.get("type") == "secret":
            out.append({"label": label, "text": "(a stored secret - used, never shown)"})
            continue
        v = _scrub(inputs[name])
        if isinstance(v, str) and v.startswith("blob:"):
            out.append({"label": label, "text": "a stored file"})
        elif isinstance(v, list):
            n = len(v)
            unit = "row" if n == 1 else "rows"
            lines = [_json_line(x) for x in v[:SENDING_SAMPLE_ITEMS]]
            more = n - min(n, SENDING_SAMPLE_ITEMS)
            out.append({"label": label,
                        "text": "\n".join([f"{n} {unit}", *lines]
                                          + ([f"...and {more} more"] if more > 0 else []))})
        else:
            out.append({"label": label, "text": _one_line(v)})
    return out

# A digest of the resolved inputs a popup showed, so an approval is for those values and no others
def _inputs_digest(inputs: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(_scrub(inputs), sort_keys=True,
                                     default=str).encode()).hexdigest()[:16]

_RUN_SECRETS = threading.local()

def _note_run_secrets(secret_inputs) -> None:
    _RUN_SECRETS.values = [str(v) for v in (secret_inputs or {}).values() if v]

# Substring-wise, recursive redaction of known secret values before anything reaches stored cases
def _scrub(value):
    known = list(_known_secrets()) + list(getattr(_RUN_SECRETS, "values", []) or [])

    def scrub(v):
        if isinstance(v, str):
            for s in known:
                if s in v:
                    v = v.replace(s, "<secret>")
            return v
        if isinstance(v, dict):
            return {k: scrub(x) for k, x in v.items()}
        if isinstance(v, list):
            return [scrub(x) for x in v]
        return v

    return scrub(value)

# Runs one node under a temp id with optional config overrides; replaying a connector would re-fire its side effect
def run_isolated(node: dict, inputs: dict, overrides: Optional[dict] = None,
                 *, workflow_id: str) -> dict:
    tmp = copy.deepcopy(node)
    tmp_id = f"tmp_{node['id']}"
    tmp["id"] = tmp_id
    if overrides:
        cfg = tmp.setdefault("config", {})
        for k in ("code", "prompt", "model", "criteria"):
            if k in overrides:
                cfg[k] = overrides[k]
    sl = {"id": f"slice_{tmp_id}", "corpus_workflow_id": workflow_id,
          "nodes": [tmp], "edges": []}
    try:
        res = dict(run_workflow(sl, dict(inputs or {})))
    finally:
        corpus.purge_node(workflow_id, tmp_id)
    res["output"] = res.get("outputs", {}).get(tmp_id) if res.get("status") == "completed" else None
    return res
