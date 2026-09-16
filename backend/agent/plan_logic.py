# Validates a plan's logic before any code exists, so design gaps surface at save time, not at build time
from __future__ import annotations

import ast
import keyword
import re
from typing import Any, Optional

from storage import deps, environments
import config
from models import code_key, step_type
import step_types
from step_types import CODE_TYPES, OUTSIDE_TYPES, carries_code, reaches_outside
from runtime import verifier

# Edge endpoints normalise to a step name, whether given as plan name, node id or node name
def _ref_to_name(ref: str, plan_names: set, id_to_name: dict,
                 existing_names: set) -> Optional[str]:
    if ref in plan_names or ref in existing_names:
        return ref
    return id_to_name.get(ref)

# The plan as the checks read it: every step reference an id at rest, a name in here
def named(plan: dict, workflow: Optional[dict] = None) -> dict:
    workflow = workflow or {}
    id_to_name = {n.get("id"): n.get("name") for n in workflow.get("nodes") or []}
    id_to_name.update({n.get("id"): n.get("name") for n in plan.get("nodes") or []
                       if n.get("id")})
    def nm(ref):
        return id_to_name.get(ref, ref) if isinstance(ref, str) else ref
    out = dict(plan)
    out["edges"] = [{**e, "src": nm(e.get("src")), "dst": nm(e.get("dst"))}
                    for e in plan.get("edges") or [] if isinstance(e, dict)]
    ch = plan.get("changes")
    if isinstance(ch, dict):
        out["changes"] = {k: ([nm(x) for x in v] if isinstance(v, list) else v)
                          for k, v in ch.items()}
    return out

# Which steps may run before each one, names on both sides
def incoming_by_name(plan: dict, workflow: Optional[dict] = None) -> dict:
    workflow = workflow or {}
    plan_names = {n.get("name") for n in plan.get("nodes") or []}
    id_to_name = {n.get("id"): n.get("name") for n in workflow.get("nodes") or []}
    id_to_name.update({n.get("id"): n.get("name") for n in plan.get("nodes") or []
                       if n.get("id")})
    existing_names = {n.get("name") for n in workflow.get("nodes") or []}
    inc: dict = {}
    for e in (plan.get("edges") or []) + (workflow.get("edges") or []):
        s = _ref_to_name(str(e.get("src") or ""), plan_names, id_to_name,
                         existing_names)
        d = _ref_to_name(str(e.get("dst") or ""), plan_names, id_to_name,
                         existing_names)
        if s and d:
            inc.setdefault(d, set()).add(s)
    return inc

# Per step, every step that can come before it - the one ancestry every check and resolver shares
def ancestors_by_name(plan: dict, workflow: Optional[dict] = None) -> dict:
    inc = incoming_by_name(plan, workflow)
    out: dict = {}
    for name in set(inc) | {n.get("name") for n in plan.get("nodes") or []} \
            | {n.get("name") for n in (workflow or {}).get("nodes") or []}:
        seen: set = set()
        stack = list(inc.get(name, ()))
        while stack:
            s = stack.pop()
            if s in seen:
                continue
            seen.add(s)
            stack.extend(inc.get(s, ()))
        out[name] = seen
    return out

# The names a routing condition references, to catch one mentioning an output nothing produces
def _when_names(expr: str) -> set:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and not keyword.iskeyword(n.id)}

# Per-step details the build needs but that are harmless to store - gaps, not refusals, so the fix is a one-step amend
def _step_content_gaps(nm: str, node: dict) -> list[str]:
    from agent import steps
    out: list[str] = []

    pol = node.get("on_invalid_items")
    if pol not in (None, "", "stop", "proceed", "log"):
        out.append(f'the "{nm}" step\'s on_invalid_items is {pol!r} - it must '
                   "be one of stop (default), proceed or log")
    if reaches_outside(node) \
            and not str(node.get("external_impact") or "").strip():
        out.append(f'the "{nm}" step reaches an outside system but does not '
                   "say what it changes there - add external_impact in plain language (it is what the user approves), and read_only: true if it only reads")
    for kind_, key in (("input", "inputs"), ("output", "outputs")):
        for p in node.get(key) or []:
            pn = p.get("name")
            if not pn:
                continue

            if p.get("type") and p.get("type") not in steps.PORT_TYPES:
                out.append(f'the "{nm}" step\'s {kind_} "{pn}" has type '
                           f"{p.get('type')!r}, which is not one of "
                           f"{sorted(steps.PORT_TYPES)}")
            elif p.get("type") == "enum" and not p.get("options"):
                out.append(f'the "{nm}" step\'s {kind_} "{pn}" is a choice '
                           "but lists no options - say what may be chosen")
            for f in p.get("item_fields") or []:
                if not isinstance(f, dict) or not f.get("name"):
                    out.append(f'the "{nm}" step\'s {kind_} "{pn}": every '
                               "item_fields entry needs a name, type and description")
                elif f.get("type") and f.get("type") not in steps.PORT_TYPES:
                    out.append(f'the "{nm}" step\'s {kind_} "{pn}" item field '
                               f'"{f["name"]}" has type {f.get("type")!r}, '
                               f"which is not one of {sorted(steps.PORT_TYPES)}")
    for t in node.get("tests") or []:
        if t.get("expect", "ok") not in steps.EXPECT:
            out.append(f'the "{nm}" step\'s test {t.get("name")!r}: expect '
                       "must be ok or reject")
        out += [f'the "{nm}" step\'s test {t.get("name")!r}: {p}'
                for p in steps.assert_problems(t.get("asserts"))]
    for perr in deps.validate(node.get("packages") or []):
        out.append(f'the "{nm}" step\'s packages: {perr}')
    for d in node.get("domains") or []:
        if not str(d).startswith("169.254.") and not steps.DOMAIN.match(str(d)):
            out.append(f'the "{nm}" step\'s domains: {d!r} must be a bare '
                       "hostname (e.g. api.example.com - no scheme or path)")

    if node.get("domains") and not step_types.may(step_type(node), "app"):
        out.append(f'the "{nm}" step declares domains, but only a connector '
                   "step reaches outside addresses: a browser step reaches its page through the window, and a code step reaches nothing outside this computer")

    declared = step_types.function(step_type(node), "declaration_gaps")
    if declared:
        out.extend(declared(nm, node))

    if node.get("browser_options") is not None:
        rule = step_types.function(step_type(node), "window_options_problems")
        if rule:
            out.extend(rule(nm, node.get("browser_options")))
        else:
            out.append(f'the "{nm}" step declares browser_options, but only a '
                       "browser step opens a window")

    import os as _os
    paths = node.get("paths") or []
    if not isinstance(paths, list):
        out.append(f'the "{nm}" step\'s paths must be a list of folders')
        paths = []
    for x in paths:
        px = _os.path.expanduser(str(x or "").strip())
        if not px or not _os.path.isabs(px):
            out.append(f'the "{nm}" step\'s paths: {x!r} must be an absolute '
                       "folder path (or start with ~)")
        elif _os.path.abspath(px).startswith(str(config.DATA_DIR)):
            out.append(f'the "{nm}" step\'s paths: {x!r} is inside the app\'s '
                       "own data folder - a workflow never reads the app's store or another workflow's files")
    return out

# Validates a plan's logic before any code exists, so design gaps surface at save time
def check(workflow: dict, plan: dict) -> dict:
    plan = named(plan, workflow)
    findings: list[str] = []
    settings: list[str] = []
    plan_nodes = plan.get("nodes") or []
    if not plan_nodes:
        return {"ok": True, "findings": [], "settings": []}

    existing = workflow.get("nodes") or []
    plan_by_name = {str(n.get("name") or "").strip(): n for n in plan_nodes
                    if str(n.get("name") or "").strip()}
    plan_names = set(plan_by_name)
    existing_by_name = {n.get("name"): n for n in existing if n.get("name")}
    existing_names = set(existing_by_name)
    id_to_name = {n["id"]: n.get("name") for n in existing if n.get("name")}
    node_by_name = {**existing_by_name, **plan_by_name}

    entry_like: set = set()
    for env_doc in environments.attached(workflow):
        for v in env_doc.get("variables", []) or []:
            entry_like.add(v.get("name"))
    for v in workflow.get("variables", []) or []:
        entry_like.add(v.get("name"))

    ambiguous = environments.ambiguous_names(workflow)

    stored = set(environments.resolve(workflow))
    for n in plan_nodes:
        check = step_types.function(step_type(n), "asks_for_a_stored_value")
        if check:
            findings.extend(check(n, stored))

    for n in plan_nodes:
        check = step_types.function(step_type(n), "answer_shape_unenforced")
        if check:
            findings.extend(check(n))
    for n in plan_nodes:
        check = step_types.function(step_type(n), "every_output_is_a_list")
        if check:
            findings.extend(check(n))

    for n in plan_nodes:
        check = step_types.function(step_type(n), "asks_every_run_without_reason")
        if check:
            findings.extend(check(n))
    for n in list(node_by_name.values()):
        if n.get("type") == "user-input":
            for p in n.get("outputs") or []:
                entry_like.add(p.get("name"))

    incoming = incoming_by_name(plan, workflow)
    ancestors = ancestors_by_name(plan, workflow)

    def _ancestors(node_name: str) -> set:
        return ancestors.get(node_name, set())

    # Every earlier step on this step's paths whose outputs carry the name - direct or not
    def _producers_of(node_name: str, port_name: str) -> list:
        return sorted(anc for anc in _ancestors(node_name)
                      if port_name in {p.get("name") for p
                                       in (node_by_name.get(anc) or {})
                                       .get("outputs") or []})

    def _ancestor_outputs(node_name: str) -> set:
        names: set = set()
        for a in _ancestors(node_name):
            for p in (node_by_name.get(a) or {}).get("outputs") or []:
                names.add(p.get("name"))
        return names

    read_anywhere = {p.get("name") for n in node_by_name.values()
                     for p in n.get("inputs") or []}

    for e in (plan.get("edges") or []) + (workflow.get("edges") or []):
        read_anywhere.update(
            re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(e.get("when") or "")))
    kept_outputs = {d.get("port") for d in workflow.get("deliverables") or []}

    # Ancestor outputs nothing reads and no saved result keeps
    def _orphan_outputs(node_name: str) -> list:
        out = []
        for a in _ancestors(node_name):
            for prt in (node_by_name.get(a) or {}).get("outputs") or []:
                o = prt.get("name")
                if o and o not in read_anywhere and o not in kept_outputs:
                    out.append((a, o))
        return out

    for nm, node in plan_by_name.items():
        ntype = node.get("type")

        reads_pathway = any(_producers_of(nm, ip.get("name"))
                            for ip in node.get("inputs") or [] if ip.get("name"))
        for p in node.get("inputs") or []:
            pn = p.get("name")
            if not pn:
                continue
            if p.get("type") == "secret":
                continue
            producers = _producers_of(nm, pn)
            if producers:
                if len(producers) > 1:
                    findings.append(
                        f'the "{nm}" step needs "{pn}", which more than one '
                        f'earlier step produces ({" and ".join(producers)}) '
                        "- rename one of the outputs so it is clear which value this step reads")
                continue

            if any(pn in {o.get("name") for o in bn.get("outputs") or []}
                   for bn in existing_by_name.values()):
                continue
            if pn in entry_like:
                if pn in ambiguous:
                    findings.append(
                        f'"{pn}" is set in more than one attached environment '
                        f'({" and ".join(ambiguous[pn])}) - ask the user which '
                        "one this workflow should use, record the choice, and save again")
                continue
            if ntype == "user-input" and not incoming.get(nm):
                continue

            near = [o for o in _ancestor_outputs(nm)
                    if o and o != pn and _norm_name(o) == _norm_name(pn)]
            if near:
                findings.append(
                    f'the "{nm}" step needs "{pn}", but an earlier step '
                    f'produces "{near[0]}" - the same name spelled '
                    "differently. Align them (rename one) so the value actually arrives")
                continue

            orphans = _orphan_outputs(nm) if not reads_pathway else []
            if orphans:
                a, o = orphans[0]
                more = (f' (its other unread outputs: '
                        f'{", ".join(x for _, x in orphans[1:4])})'
                        if len(orphans) > 1 else "")
                findings.append(
                    f'the "{nm}" step reads "{pn}", which nothing produces - '
                    f'while its earlier step "{a}" produces "{o}", which '
                    f"nothing reads{more}. If these are the same value, use "
                    "ONE name on both sides of the seam; a value the workflow gathers is never supplied by the user. (A genuine steering value the user keeps is declared with declare_variables instead)")
                continue

            ptype = str(p.get("type") or "").strip()
            if ptype and not environments.setting_type(ptype):
                findings.append(
                    f'the "{nm}" step reads "{pn}" as {ptype!r}, but nothing '
                    "produces it - so it would have to be a setting the user keeps, and a setting can only be text, a number, a date, true/false, a file or a folder. Either declare it as one of those, or collect it with a user-input step")
                continue
            settings.append(pn)

    for nm, node in plan_by_name.items():
        t = node.get("type")

        cfg_sketch = (str(node.get("code") or "").strip()
                      or str(node.get("code_sketch") or "").strip())
        if t in CODE_TYPES and not cfg_sketch:
            findings.append(f'the "{nm}" step has no description of what it does '
                            "- add the proven code from your exploration, or a plain sketch of how it turns inputs into outputs")
        no_prompt = step_types.function(step_type(node), "missing_prompt")
        if no_prompt:
            findings.extend(no_prompt(nm, node))
        if not (node.get("outputs") or []) and t != "user-input":
            findings.append(f'the "{nm}" step produces no output - every step must '
                            "declare what it hands on")

        if t in CODE_TYPES and str(node.get("code") or "").strip():
            from agent import gate as _pgate
            from runtime import sandbox as _sb
            roots = _sb.workflow_roots(workflow, node.get("paths") or [])
            for v in _pgate.check_outside_paths(str(node.get("code")), roots):
                findings.append(f'the "{nm}" step {v}')
        findings += _step_content_gaps(nm, node)

    for nm, node in plan_by_name.items():
        declared_in = {p.get("name") for p in node.get("inputs") or []}
        for tst in node.get("tests") or []:
            for k in (tst.get("inputs") or {}):
                if k not in declared_in:
                    findings.append(
                        f'the "{nm}" step\'s example uses "{k}", which is not one '
                        "of its inputs - the example and the step disagree")

    for e in plan.get("edges") or []:
        w = str(e.get("when") or "").strip()
        if not w:
            continue
        s = _ref_to_name(e.get("src", ""), plan_names, id_to_name, existing_names)
        if not s:
            continue
        src_outs = {p.get("name") for p in (node_by_name.get(s) or {}).get("outputs") or []}
        for ident in _when_names(w):
            all_outs = {p.get("name") for nd in node_by_name.values()
                        for p in nd.get("outputs") or []}
            if ident not in src_outs and ident not in all_outs and ident not in _SAFE:
                findings.append(
                    f'the route out of "{s}" tests "{ident}", which "{s}" does not '
                    "produce - branch on one of its outputs")
                break

    for d in workflow.get("deliverables") or []:
        dn = d.get("node")
        target = node_by_name.get(dn) or (id_to_name.get(dn) and
                                          node_by_name.get(id_to_name.get(dn)))
        if target is None:
            continue
        outs = {p.get("name") for p in target.get("outputs") or []}
        if d.get("port") and d.get("port") not in outs:
            findings.append(f'a saved result points at "{d.get("port")}" from '
                            f'"{dn}", which that step no longer produces')

    from agent import gate as _gate
    for n in plan_nodes:
        code = str(n.get("code") or "")
        if not carries_code(n) or not code:
            continue
        declared = [p.get("name") for p in n.get("inputs") or []]
        secret_ports = [p.get("name") for p in n.get("inputs") or []
                        if p.get("type") == "secret"]

        for name in sorted(_gate.read_input_names(code) - set(declared)):
            findings.append(
                f'step "{n.get("name")}" reads "{name}" with read_input but does '
                "not declare it as an input - a run hands a step only its declared "
                f'inputs, so add "{name}" to this step\'s inputs')

        for name, number in _gate.literal_item_caps(code):
            findings.append(
                f'step "{n.get("name")}" cuts "{name}" to its first {number} items '
                "in its code - a step that loops over items reads its maximum "
                f"from a setting (for example max_{name}) and never has the number "
                "written in; while building, pass 2 for that setting in the cell's inputs")

        proven = False
        try:
            from agent import cells as _cells
            rec = _cells.latest_ok(workflow.get("id") or "",
                                   str(n.get("name") or ""), kind="code")
            proven = bool(rec) and code_key(rec.get("code")) == code_key(code)
        except Exception:
            proven = False
        if not proven:
            for v in (_gate.check(code, declared)
                      + _gate.check_fake(code, declared)
                      + _gate.check_undefined_names(code)
                      + _gate.check_recording(code)
                      + _gate.check_silent_loop(code)):
                findings.append(f'step "{n.get("name")}": {v}')
        for v in _gate.check_secret_ports(code, secret_ports):
            findings.append(f'step "{n.get("name")}": {v} (or drop the '
                            "port and keep the stored-name approach - declare NO secret port and use get_secret)")
        for v in _gate.check_secret_value_use(code, secret_ports):
            findings.append(f'step "{n.get("name")}": {v}')

        for v in _gate.check_detached_browser(code):
            findings.append(f'step "{n.get("name")}": {v}')

    from agent import cells as _cells2
    from agent import gate as _gate2
    for nm, dst in plan_by_name.items():
        dtype = dst.get("type")
        code = str(dst.get("code") or (dst.get("config") or {}).get("code") or "")
        for p in dst.get("inputs") or []:
            pn = p.get("name")
            if not pn or p.get("type") == "secret":
                continue
            producers = _producers_of(nm, pn)
            if len(producers) != 1:
                continue
            srcname = producers[0]
            src = node_by_name.get(srcname) or {}
            out_type = next((q.get("type") for q in src.get("outputs") or []
                             if q.get("name") == pn), None)
            a, b = _TYPE_SHAPES.get(out_type), _TYPE_SHAPES.get(p.get("type"))
            if a is not None and b is not None and a != b:
                findings.append(
                    f'"{pn}" is produced by "{srcname}" as {out_type!r} but '
                    f'"{nm}" consumes it as {p.get("type")!r} - one shape per '
                    "value; make both ends agree")
            rec = _cells2.latest_ok(workflow["id"], srcname)
            recorded = (rec.get("output") if rec
                        and isinstance(rec.get("output"), dict) else {})
            v = recorded.get(pn)
            if v is None:
                continue
            if dtype == "user-input" and isinstance(v, list) and v \
                    and isinstance(v[0], dict) and len(v[0]) > 1:
                for q in dst.get("outputs") or []:
                    if q.get("type") not in ("record", "list", "secret", "") \
                            and not q.get("value_field"):
                        findings.append(
                            f'step "{nm}" picks from records but its output '
                            f'"{q.get("name")}" declares no value_field - name '
                            "which field of the picked item becomes the value")
            if dtype not in CODE_TYPES or not code:
                continue
            if isinstance(v, dict):
                available = set(v.keys())
            elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                available = set().union(*(x.keys() for x in v))
            else:
                continue
            for k in sorted(_gate2.record_field_reads(code, pn) - available):
                findings.append(
                    f'step "{nm}" reads field {k!r} of "{pn}", but the value '
                    f'"{srcname}" records carries {sorted(available)} - fix '
                    "the field name or the producing step")

    from runtime import safe_eval as _se
    from runtime import verifier as _ver
    _known_funcs = set(_ver._SAFE_BUILTINS) | set(_ver._LIBRARY)
    for n in plan_nodes:
        nm = str(n.get("name") or "").strip()
        available = {str(p.get("name")) for p in (n.get("outputs") or [])
                     if p.get("name")} | \
                    {str(p.get("name")) for p in (n.get("inputs") or [])
                     if p.get("name")}
        for c in n.get("criteria") or []:
            expr = str((c.get("expr") if isinstance(c, dict) else "") or "")
            if not expr:
                continue
            unknown = sorted(_se.free_names(expr) - available - _known_funcs)
            if not unknown:
                continue
            hint = ""
            for miss in unknown:
                owner = next((o for o in (n.get("outputs") or [])
                              if any(f.get("name") == miss
                                     for f in (o.get("item_fields") or []))),
                             None)
                if owner:
                    hint = (f' - {miss!r} is a field of "{owner.get("name")}", '
                            f'so write {owner.get("name")}[{miss!r}]')
                    break
            findings.append(
                f'step "{nm}": the check {expr!r} reads '
                + ", ".join(repr(u) for u in unknown)
                + f", which this step neither takes in nor produces (it has "
                  f"{sorted(available) or 'nothing declared'}){hint}. A check "
                  "that cannot be evaluated fails the run, so fix the name or drop the check")

    findings += _recorded_type_mismatches(workflow["id"], plan_nodes)
    findings += _settings_that_cannot_mean_their_type(workflow, plan)

    findings += _output_name_mismatches(workflow["id"], plan_nodes)

    findings += _per_item_findings(workflow["id"], plan_nodes)

    findings += _one_kind_of_work(plan_nodes)

    findings += _when_evidence_findings(workflow, plan, node_by_name,
                                        plan_names, id_to_name, existing_names)

    findings += _reachability(plan, plan_by_name, incoming, entry_like)
    findings += _redundant_order_lines(plan, incoming)
    findings += _lines_between_unrelated_steps(plan, workflow, node_by_name)
    findings += _repeated_siblings(plan, plan_by_name, incoming)
    findings += _unplaced_steps(plan, plan_by_name)
    findings += _ai_echoes_its_input(workflow["id"], plan_by_name)
    findings += _dead_pathways(workflow, plan, node_by_name, plan_names,
                               id_to_name, existing_names)
    findings += _environment_copies(workflow, plan_nodes)

    return {"ok": not findings, "findings": findings,
            "settings": sorted(set(settings))}

ENV_VALUE_MIN = 8

# A step or setting carrying an attached environment's value written into it is a design gap; the value is never quoted
def _environment_copies(workflow: dict, plan_nodes: list) -> list[str]:
    held: list[tuple[str, str, str]] = []
    for env in environments.attached(workflow):
        for v in env.get("variables") or []:
            if v.get("secret"):
                continue
            sval = str(v.get("value") if v.get("value") is not None else "").strip()
            if len(sval) >= ENV_VALUE_MIN:
                held.append((env.get("name") or env.get("id"), v.get("name"), sval))
    if not held:
        return []
    out: list[str] = []
    for n in plan_nodes:
        texts = [str(n.get("code") or ""), str(n.get("prompt") or "")]
        for env_name, var_name, sval in held:
            if any(sval in t for t in texts):
                out.append(f'step "{n.get("name")}" carries the value of environment '
                           f'"{env_name}"\'s {var_name} written into it - a step reads '
                           f'it by name (read_input("{var_name}")), never a copy')
                break
    for v in workflow.get("variables") or []:
        if v.get("secret"):
            continue
        sval = str(v.get("value") if v.get("value") is not None else "").strip()
        if not sval:
            continue
        for env_name, var_name, held_val in held:
            if held_val in sval:
                out.append(f'the setting "{v.get("name")}" holds a copy of environment '
                           f'"{env_name}"\'s {var_name} - the environment supplies that '
                           "value by name; ask the user, then remove the workflow's own copy (remove_variables, confirmed:true)")
                break
    return out

# A valued variable no step reads is a requirement dropped silently - warned on the card
def unread_valued_variables(workflow: dict, nodes: list) -> list[str]:
    read = {p.get("name") for n in nodes for p in (n.get("inputs") or [])}
    produced = {p.get("name") for n in nodes for p in (n.get("outputs") or [])}
    out = []
    for v in workflow.get("variables") or []:
        name = v.get("name")
        if (not name or v.get("secret")
                or not str(v.get("value") if v.get("value") is not None
                           else "").strip()):
            continue

        if name in read and name not in produced:
            continue
        out.append(v.get("label") or name)
    return out

_TYPE_SHAPES = {"record": (dict,), "boolean": (bool,), "list": (list,),
                "number": (int, float), "text": (str,), "longtext": (str,)}

# A step with no line into it, that is not a legitimate starting step, simply never runs - said out loud here
def _reachability(plan: dict, plan_by_name: dict, incoming: dict,
                  entry_like: set) -> list[str]:
    out: list[str] = []
    if len(plan_by_name) < 2:
        return out
    known = set(plan_by_name)
    for e in plan.get("edges") or []:
        for end in ("src", "dst"):
            ref = str(e.get(end) or "")
            if ref and ref not in known and ref not in incoming:
                out.append(f'the line {e.get("src")!r} -> {e.get("dst")!r} '
                           f"names {ref!r}, which is not a step in this plan "
                           "- fix the name or remove the line")
    touched = {str(e.get(end) or "") for e in plan.get("edges") or []
               for end in ("src", "dst")}
    for nm, node in plan_by_name.items():
        if incoming.get(nm):
            continue

        if (plan.get("edges") and nm not in touched
                and node.get("id") not in touched):
            continue
        needs = [p.get("name") for p in node.get("inputs") or []
                 if p.get("name") and p.get("type") != "secret"
                 and p.get("name") not in entry_like]
        if needs:
            out.append(f'nothing runs the "{nm}" step - no step comes before '
                       f"it, and it needs {', '.join(sorted(needs))} from "
                       "one. Put it after the step that produces what it reads, or make it a starting step")
    return out

# In a plan that declares any order, a step no edge touches is a gap: from its own perspective, what comes next
def _unplaced_steps(plan: dict, plan_by_name: dict) -> list[str]:
    steps = [n for n in plan.get("nodes") or [] if n.get("name")]

    if len(steps) < 2:
        return []
    touched: set = set()
    for e in plan.get("edges") or []:
        touched.add(str(e.get("src") or "")); touched.add(str(e.get("dst") or ""))
    out = []
    for n in steps:
        nm = str(n.get("name"))
        if nm not in touched and n.get("id") not in touched:
            out.append(f'the "{nm}" step is not placed in the order - no line '
                       "says what runs before or after it. Add the edge(s): from this step's own perspective, what runs after it? An independent step is drawn into the first step that needs it, beside the other branches, never in front of a step it has nothing to do with (its VALUES already travel by name; the line is the ORDER the user reads)")
    return out

# A branch nothing reads, routes through or keeps is flagged whole - usually a replaced route left behind
def _dead_pathways(workflow: dict, plan: dict, node_by_name: dict,
                   plan_names: set, id_to_name: dict,
                   existing_names: set) -> list[str]:
    kept = {d.get("port") for d in workflow.get("deliverables") or []}
    if not kept:
        return []

    ch = plan.get("changes")
    removed: set = set()
    for r in (ch.get("removed") or []) if isinstance(ch, dict) else []:
        if isinstance(r, dict):
            r = r.get("name") or r.get("id") or ""
        r = str(r)
        removed.add(id_to_name.get(r, r))
    graph = {nm: n for nm, n in node_by_name.items() if nm not in removed}

    when_edges = []
    for e in (plan.get("edges") or []) + (workflow.get("edges") or []):
        names = _when_names(str(e.get("when") or "")) if e.get("when") else set()
        if names:
            d = _ref_to_name(str(e.get("dst") or ""), plan_names, id_to_name,
                             existing_names)
            if d and d not in removed:
                when_edges.append((d, names))

    def _exempt(n: dict) -> bool:
        t = n.get("type")
        t = t.value if hasattr(t, "value") else str(t)
        if t == "user-input":
            return True
        if t in OUTSIDE_TYPES and not n.get("read_only", False):
            return True
        return not [p for p in n.get("outputs") or [] if p.get("name")]

    reads = {r: {p.get("name") for p in (graph.get(r) or {})
                 .get("inputs") or []} for r in graph}
    dead: set = set()
    while True:
        newly = []
        for nm, n in graph.items():
            if nm in dead or _exempt(n):
                continue
            outs = {p.get("name") for p in n.get("outputs") or []
                    if p.get("name")}
            if outs & kept:
                continue
            alive = any(o in reads[r] for r in reads
                        if r != nm and r not in dead for o in outs) \
                or any(o in names for d, names in when_edges
                       if d not in dead for o in outs)
            if not alive:
                newly.append(nm)
        if not newly:
            break
        dead.update(newly)
    if not dead:
        return []
    ordered = [nm for nm in graph if nm in dead]
    listed = ", ".join(f'"{nm}"' for nm in ordered)
    return [f"nothing uses what these steps produce: {listed} - no later "
            "step reads their outputs and no saved result keeps them, so they would run every time and everything they do would be thrown away. If newer steps replaced them, remove them (changes.removed); if they still matter, connect what they produce to a step that reads it, or keep it as a saved result"]

# N copies of the same step doing the same thing in the same place should be one step
def _repeated_siblings(plan: dict, plan_by_name: dict,
                       incoming: dict) -> list[str]:
    outgoing: dict = {}
    for d, srcs in incoming.items():
        for s in srcs:
            outgoing.setdefault(s, set()).add(d)

    def _shape(node: dict) -> str:
        body = str(node.get("prompt") or node.get("code")
                   or node.get("code_sketch") or "")
        return re.sub(r"\d+", "#", body).strip()

    groups: dict = {}
    for nm, node in plan_by_name.items():
        body = _shape(node)
        if not body:
            continue
        key = (node.get("type"), body,
               frozenset(incoming.get(nm, ())), frozenset(outgoing.get(nm, ())))
        groups.setdefault(key, []).append(nm)
    out: list[str] = []
    for (ntype, _body, _ins, _outs), names in groups.items():
        if len(names) < 2:
            continue
        shown = ", ".join(f'"{n}"' for n in sorted(names)[:4])
        more = f" (and {len(names) - 4} more)" if len(names) > 4 else ""
        out.append(
            f"{len(names)} steps do the same thing in the same place - "
            f"{shown}{more}. Repeating a step is not structure: make it ONE "
            f"{ntype} step whose own code handles every item. Splitting work "
            "across copies of a step fixes the count into the workflow, and a count that came from guessing at volume is not a design")
    return out

# An AI step returns judgement, not a copy of its input - echoing costs tokens and risks a cut-off answer
def _ai_echoes_its_input(workflow_id: str, plan_by_name: dict) -> list[str]:
    from agent import cells as _cells
    out: list[str] = []
    for nm, node in plan_by_name.items():
        if node.get("type") != "ai":
            continue
        fields = [str(f.get("name")) for p in node.get("outputs") or []
                  for f in (p.get("item_fields") or []) if f.get("name")]
        if not fields:
            continue
        rec = _cells.latest_ok(workflow_id, nm, kind="ai")
        given = (rec or {}).get("inputs") or {}

        carried: set = set()
        for v in given.values():
            items = v if isinstance(v, list) else [v]
            if isinstance(v, dict):
                items = [x for sub in v.values()
                         if isinstance(sub, list) for x in sub] or [v]
            for it in items[:5]:
                if isinstance(it, dict):
                    carried |= {str(k) for k in it}
        echoed = sorted(set(fields) & carried)
        if len(echoed) < 2:
            continue
        out.append(
            f'the "{nm}" step copies {", ".join(echoed)} straight back out - '
            "the model re-types what it was just given, which costs time and can cut the answer short. Return one field that identifies the item plus what you decided, and join the rest back in a later step")
    return out

# An unconditional line whose destination already follows by another route says nothing new - reported, never silently removed
def _redundant_order_lines(plan: dict, incoming: dict) -> list[str]:
    plain: dict = {}
    for e in plan.get("edges") or []:
        if str(e.get("when") or "").strip():
            continue
        s, d = str(e.get("src") or ""), str(e.get("dst") or "")
        if s and d:
            plain.setdefault(d, set()).add(s)

    def reaches(src: str, dst: str, skip: tuple) -> bool:
        stack, seen = [dst], set()
        while stack:
            n = stack.pop()
            if n == src:
                return True
            if n in seen:
                continue
            seen.add(n)
            stack.extend(p for p in plain.get(n, ()) if (p, n) != skip)
        return False

    out: list[str] = []
    for e in plan.get("edges") or []:
        if str(e.get("when") or "").strip():
            continue
        s, d = str(e.get("src") or ""), str(e.get("dst") or "")
        if s and d and reaches(s, d, (s, d)):
            out.append(f'the line "{s}" -> "{d}" adds nothing: "{d}" already '
                       f'runs after "{s}" by another route. Remove it, or '
                       "give it a condition if it is a real alternative path")
    return out

# A plain line between two steps that share nothing orders them for no reason - reported with the fix
def _lines_between_unrelated_steps(plan: dict, workflow: dict,
                                   node_by_name: dict) -> list[str]:
    plan_names = {n.get("name") for n in plan.get("nodes") or []}
    id_to_name = {n.get("id"): n.get("name") for n in workflow.get("nodes") or []}
    id_to_name.update({n.get("id"): n.get("name") for n in plan.get("nodes") or []
                       if n.get("id")})
    existing_names = {n.get("name") for n in workflow.get("nodes") or []}
    out: list[str] = []
    for e in plan.get("edges") or []:
        if str(e.get("when") or "").strip():
            continue
        s = _ref_to_name(str(e.get("src") or ""), plan_names, id_to_name,
                         existing_names)
        d = _ref_to_name(str(e.get("dst") or ""), plan_names, id_to_name,
                         existing_names)
        if not s or not d or s == d:
            continue
        a, b = node_by_name.get(s), node_by_name.get(d)
        if not a or not b:
            continue
        produced = {_norm_name(str(p.get("name")))
                    for p in a.get("outputs") or [] if p.get("name")}
        if not produced:
            continue
        if step_types.writes_outside(a):
            continue
        if step_type(a) == "browser" and step_type(b) == "browser":
            continue
        if a.get("paths") or b.get("paths"):
            continue
        read = {_norm_name(str(p.get("name")))
                for p in b.get("inputs") or [] if p.get("name")}
        if produced & read:
            continue
        out.append(f'the line "{s}" -> "{d}" orders two steps that share '
                   f'nothing: "{d}" reads nothing "{s}" produces, and neither '
                   "changes anything outside. Leave it out and draw each of them into the first step that needs both, so they run as separate branches")
    return out

def _recorded_type_mismatches(workflow_id: str, plan_nodes: list) -> list[str]:
    from agent import cells
    out: list[str] = []
    for n in plan_nodes or []:
        if not carries_code(n):
            continue
        rec = cells.latest_ok(workflow_id, str(n.get("name") or ""), kind="code")
        recorded = (rec or {}).get("output")
        if not isinstance(recorded, dict):
            continue
        for p in n.get("outputs") or []:
            v = recorded.get(p.get("name"))

            if (p.get("type") == "file" and isinstance(v, str)
                    and not v.startswith("blob:")):
                out.append(f'step "{n.get("name")}" output "{p.get("name")}" is '
                           "a file port, but the recorded run produced inline text - produce the file with write_file(name, data) and output the reference it returns")
                continue
            shapes = _TYPE_SHAPES.get(p.get("type"))
            if shapes is None or v is None:
                continue
            if isinstance(v, bool) and p.get("type") == "number":
                v_ok = False
            else:
                v_ok = isinstance(v, shapes)
            if not v_ok:
                kind = ("a list" if isinstance(v, list) else
                        f"a {type(v).__name__}")
                out.append(f'step "{n.get("name")}" output "{p.get("name")}": '
                           f"the recorded run produced {kind}, but the plan "
                           f"declares it {p.get('type')!r} - declare a type "
                           "that matches what actually ran"
                           + (" (a list is type 'list')"
                              if isinstance(v, list) else ""))

        from runtime import port_checks as _ports
        said = {p.get("name") for p in n.get("outputs") or []
                if any(f'output "{p.get("name")}"' in line for line in out)}

        typed = [p for p in n.get("outputs") or []
                 if p.get("type") and p.get("type") != "file"
                 and p.get("name") not in said]
        for prob in _ports.check_ports(typed, recorded, require_all=True):
            out.append(f'step "{n.get("name")}" output "{prob.get("port")}": in the '
                       f"step's try, {prob.get('problem')} - the try is judged as "
                       "a run: make the step produce it, or mark the output optional on the plan step if it may genuinely be empty")
    return out

# A stored setting whose value cannot mean the type of the input that reads it
def _settings_that_cannot_mean_their_type(workflow: dict, plan: dict) -> list[str]:
    from agent import steps as _steps
    out: list[str] = []
    for v in workflow.get("variables") or []:
        if v.get("secret") or not environments.value_is_set(v.get("value")):
            continue
        name = str(v.get("name") or "")
        t = _steps.setting_type_for(workflow, plan, name)
        if not t:
            continue
        _, understood = environments.coerce_to_type(v.get("value"), t)
        if not understood:
            out.append(f'the setting "{name}" is read as a {t}, but its stored '
                       f"value {v.get('value')!r} is not one - correct the value "
                       "on the Inputs tab, or change the input's type")
    return out

# One kind of work per step: what the code does must match the step's declared type
def _one_kind_of_work(plan_nodes: list) -> list[str]:
    from agent import gate as _gate
    out: list[str] = []
    for n in plan_nodes or []:
        t = n.get("type")
        code = str(n.get("code") or "")
        if t not in CODE_TYPES or not (code.strip() or n.get("domains")):
            continue
        nm = n.get("name") or "?"
        if not step_types.may(step_type(n), "browser") and "confirm_with_user(" in code:
            out.append(f'the "{nm}" step asks the person at the machine to do '
                       "something (confirm_with_user), which only a browser step can - it is the window on their screen that makes that possible. A value the run lacks pauses with its own form, a choice partway is a user-input step, and a send is fronted by its approval")
        sig = _gate.work_signature(code)
        services = list(sig["services"])
        for d in n.get("domains") or []:
            svc = _gate._service_of(str(d))
            if svc not in services:
                services.append(svc)

        mismatch = step_types.function(step_type(n), "work_mismatch")
        if mismatch:
            out.extend(mismatch(nm, sig, services))
        if sig["ai"]:
            out.append(f'the "{nm}" step calls ai_call from step code - judgement '
                       "is an AI step of its own")
    return out

# The names a piece of code calls as functions
def _called_names(code: str) -> set:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            out.add(n.func.id)
    return out

# A step that loops over a list input declares the list and its item key, and records each item as it lands
def _per_item_findings(workflow_id: str, plan_nodes: list) -> list[str]:
    from runtime.executor import iterated_input_names
    from agent import cells
    out: list[str] = []
    for n in plan_nodes or []:
        if not carries_code(n):
            continue
        code = str(n.get("code") or "")
        if not code.strip():
            continue
        nm = n.get("name") or "?"
        declared = {p.get("name") for p in n.get("inputs") or []}
        looped = {x for x in iterated_input_names(code) if x in declared}
        pi = n.get("per_item") if isinstance(n.get("per_item"), dict) else None
        if not pi:
            if looped:
                names = ", ".join(sorted(f'"{x}"' for x in looped))
                out.append(f'step "{nm}" works through {names} item by item - '
                           "declare per_item {input, key} on the step, record each item the moment it is done with checkpoint(key, result), and build the outputs from checkpoints(), so a stop keeps what it got and the user can proceed with the saved results")
            continue
        inp, key = str(pi.get("input") or ""), str(pi.get("key") or "")
        if not inp or not key:
            out.append(f'step "{nm}": per_item needs both "input" (the list it '
                       'works through) and "key" (the field that identifies an item)')
            continue
        if inp not in declared:
            out.append(f'step "{nm}": per_item names "{inp}", which is not one of '
                       "the step's inputs")
            continue
        called = _called_names(code)
        if "checkpoint" not in called or "checkpoints" not in called:
            out.append(f'step "{nm}" declares per_item over "{inp}" but its code '
                       "does not record each item as it lands - call checkpoint(key, result) right after each item and build the outputs from checkpoints(), so the step finishes from the record on any subset of the list")
        rec = cells.latest_ok_for(workflow_id, n, kind="code")
        items = ((rec or {}).get("inputs") or {}).get(inp)
        if isinstance(items, list) and items and isinstance(items[0], dict):
            if key not in items[0]:
                have = ", ".join(sorted(str(k) for k in items[0])[:8])
                out.append(f'step "{nm}": per_item key "{key}" is not a field of '
                           f'the recorded "{inp}" items (they carry {have})')
    return out

# The code must write the output names it declares, read from the code the build will keep
def _output_name_mismatches(workflow_id: str, plan_nodes: list) -> list[str]:
    from agent import cells
    from agent import gate
    out: list[str] = []
    for n in plan_nodes or []:
        if not carries_code(n):
            continue
        nm = n.get("name")
        declared = {p.get("name") for p in n.get("outputs") or []}
        if not declared:
            continue
        rec = cells.latest_ok(workflow_id, str(nm or ""), kind="code")
        recorded = (rec or {}).get("output")
        if not (isinstance(recorded, dict)
                and "$missing_evidence" not in recorded):
            recorded = None
        code = str(n.get("code") or "").strip()
        if code:
            written, dynamic = gate.write_output_names(code)
            if recorded is not None and code_key(rec.get("code")) \
                    == code_key(code):
                written |= set(recorded)
                dynamic = False
            if not written:
                continue
        elif recorded is not None:
            written, dynamic = set(recorded), False
        else:
            continue

        missing = sorted(declared - written)
        if missing and not dynamic:
            extra = sorted(written - declared)
            out.append(
                f'step "{nm}" declares {_names(missing)} but never produces '
                f"{'them' if len(missing) > 1 else 'it'}"
                + (f" (the code writes {_names(extra)} instead)" if extra else "")
                + f" - write {'those names' if len(missing) > 1 else 'that name'}, "
                "or drop the output port")
    return out

def _names(items) -> str:
    return ", ".join(f'"{x}"' for x in items)

# Each routing condition is evaluated against the recorded output at save, not first at build
def _when_evidence_findings(workflow: dict, plan: dict, node_by_name: dict,
                            plan_names, id_to_name, existing_names) -> list[str]:
    from agent import cells
    from runtime import safe_eval
    out: list[str] = []
    seen: dict = {}
    for e in plan.get("edges") or []:
        w = str(e.get("when") or "").strip()
        if not w:
            continue
        s = _ref_to_name(e.get("src", ""), plan_names, id_to_name, existing_names)
        if not s or not node_by_name.get(s):
            continue
        if s not in seen:
            rec = cells.latest_ok(workflow["id"], str(s), kind="code")
            raw = (rec or {}).get("output")
            seen[s] = (raw if isinstance(raw, dict)
                       and "$missing_evidence" not in raw else None)
        recorded = seen[s]
        if recorded is None:
            continue
        try:
            safe_eval.evaluate(w, dict(recorded), verifier.FUNCTIONS)
        except Exception:
            out.append(
                f'the route out of "{s}" tests "{w}", which does not work on '
                f"what that step actually produced ({_names(sorted(recorded))}) "
                "- branch on a value it really hands on")
    return out

_SAFE = {"len", "any", "all", "int", "float", "str", "bool", "None", "True",
         "False", "min", "max", "sum", "abs", "sorted", "set", "list", "dict"}

# Spelling noise removed, so customerId and customer_id compare equal
def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())
