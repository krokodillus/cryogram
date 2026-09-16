# The save_plan check list: every rule a plan passes on its way to the workflow, in the one order they apply
from __future__ import annotations

import json
import time
from typing import Callable, Optional

import config
import providers
from runtime import shape as _shape_mod
from storage import environments, store
from agent import steps
from agent import plan_logic, turnstate
import step_types
from models import step_type
from step_types import reaches_outside, writes_outside

class PlanSave:
    def __init__(self, workflow: dict, plan: dict):
        self.workflow = workflow
        self.plan = dict(plan or {})
        self.prev0 = workflow.get("plan") or {}
        self.fix_tid = ""
        self.errors: list[str] = []
        self.nodes: list = []
        self.changes = {}
        self.props: list = []
        self.plan_by_name: dict = {}
        self.built_by_ref: dict = {}
        self.renames: dict = {}
        self.carried: list = []
        self.filled: list = []
        self.seams = None
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.is_change_save = False
        self.warn_nodes: list = []
        self.untested: list = []
        self.stale_names: list = []
        self.retargeted: list = []
        self.dropped_deliverables: list = []
        self.logic: dict = {}
        self.created_vars: list = []
        self.note_mapped = False

    @property
    def turn(self):
        return turnstate.of(self.workflow)

def intake(s: PlanSave) -> Optional[dict]:
    shape_errors = steps.normalise_plan_shapes(s.plan)
    if shape_errors:
        return {"ok": False, "errors": shape_errors}
    return None

def amend(s: PlanSave) -> Optional[dict]:
    plan, prev0 = s.plan, s.prev0
    replace = bool(plan.pop("replace", None))
    plan.pop("amend", None)

    ch = plan.get("changes") if isinstance(plan.get("changes"), dict) else {}
    ren = {steps.humanise_name(str(r.get("from") or "")): steps.humanise_name(str(r.get("to") or ""))
           for r in (ch.get("renamed") or []) if isinstance(r, dict)
           and str(r.get("from") or "").strip() and str(r.get("to") or "").strip()}
    if ren and prev0.get("nodes"):
        prev0 = dict(prev0)
        prev0["nodes"] = [({**n, "name": ren[steps.humanise_name(str(n.get("name") or ""))]}
                           if steps.humanise_name(str(n.get("name") or "")) in ren else n)
                          for n in prev0.get("nodes") or []]
        s.prev0 = prev0
    if not replace:
        if prev0.get("nodes") and prev0.get("status") != "discarded":
            built = {steps.humanise_name(str(n.get("name") or "")): n.get("id")
                     for n in s.workflow.get("nodes") or []}
            prev = dict(prev0)
            prev["nodes"] = [
                ({**n, "id": built[steps.humanise_name(str(n.get("name") or ""))]}
                 if not n.get("id") and steps.humanise_name(str(n.get("name") or "")) in built
                 else n)
                for n in prev0.get("nodes") or []]
            s.plan = steps.merge_amend(prev, plan)
        elif (s.turn.last_plan_attempt or {}).get("nodes"):
            s.plan = steps.merge_amend(s.turn.last_plan_attempt, plan)
    return None

def harness_keys(s: PlanSave) -> Optional[dict]:
    plan, prev0 = s.plan, s.prev0
    plan.pop("approved_ts", None)
    plan.pop("approved_summary", None)
    s.fix_tid = steps.fix_in_progress(s.workflow)
    plan.pop("ticket_id", None)
    if s.fix_tid:
        plan["ticket_id"] = s.fix_tid
        note = plan.get("fix_note") if isinstance(plan.get("fix_note"), dict) else {}
        why = str(note.get("went_wrong") or "").strip()
        what = str(note.get("will_change") or "").strip()
        prev_note = prev0.get("fix_note") if isinstance(prev0.get("fix_note"), dict) else {}
        if (not why or not what) and prev0.get("ticket_id") == s.fix_tid \
                and prev_note.get("went_wrong") and prev_note.get("will_change"):
            why, what = why or prev_note["went_wrong"], what or prev_note["will_change"]
        if not why or not what:
            return {"ok": False, "errors": [
                "a fix plan needs `fix_note`: {\"went_wrong\": \"<one or two plain sentences - what happened, in the user's words>\", \"will_change\": \"<what you will change and why that fixes it - plain words, no code, tool or field names>\"}. The user reads these above the card."]}
        plan["fix_note"] = {"went_wrong": why[:600], "will_change": what[:600]}
        plan.pop("change_note", None)
    else:
        fnote = plan.pop("fix_note", None)
        if isinstance(fnote, dict) and s.workflow.get("nodes") \
                and not isinstance(plan.get("change_note"), dict):
            plan["change_note"] = {"found": str(fnote.get("went_wrong") or "").strip(),
                                   "will_change": str(fnote.get("will_change") or "").strip()}
            s.note_mapped = True

        note = plan.get("change_note") if isinstance(plan.get("change_note"), dict) else {}
        prev_note = prev0.get("change_note") if isinstance(prev0.get("change_note"), dict) else {}
        found = str(note.get("found") or prev_note.get("found") or "").strip()
        what = str(note.get("will_change") or prev_note.get("will_change") or "").strip()
        if s.workflow.get("nodes") and (found or what):
            plan["change_note"] = {"found": found, "will_change": what}
        else:
            plan.pop("change_note", None)
    plan.pop("fix_approved_ts", None)
    if prev0.get("fix_approved_ts") and prev0.get("ticket_id") == plan.get("ticket_id"):
        plan["fix_approved_ts"] = prev0["fix_approved_ts"]

    plan.pop("change_approved_ts", None)
    if prev0.get("change_approved_ts"):
        plan["change_approved_ts"] = prev0["change_approved_ts"]

    plan.pop("approved_changes", None)
    if isinstance(prev0.get("approved_changes"), list) and (
            plan.get("change_approved_ts") or plan.get("fix_approved_ts")):
        plan["approved_changes"] = list(prev0["approved_changes"])
    return None

def identity(s: PlanSave) -> Optional[dict]:
    plan, workflow, errors = s.plan, s.workflow, s.errors
    if not (workflow.get("intent") or {}).get("summary"):
        errors.append("no intent recorded - save the workflow's PURPOSE first with save_intent (see the 07-what-a-plan-contains skill); the post-build check validates the built workflow against it")
    if not str(plan.get("summary") or "").strip():
        errors.append("plan needs a summary (2-4 plain sentences for the opening card)")
    s.nodes = nodes = plan.get("nodes") or []
    for n in nodes:
        if n.get("name"):
            n["name"] = steps.humanise_name(n["name"])
    for e in plan.get("edges") or []:
        for k in ("src", "dst"):
            if e.get(k) and steps.step_by_ref(workflow, plan, e[k]) is None:
                e[k] = steps.humanise_name(e[k])
    s.changes = plan.get("changes") or {}
    if not nodes and not s.changes:
        errors.append("plan needs nodes to build and/or a changes section")

    import models as _models
    from agent import cells as _cells
    prev_steps = [x for x in (s.prev0.get("nodes") or []) if x.get("id")] \
        if s.prev0.get("status") != "discarded" else []
    present = {str(x.get("id") or "") for x in nodes} \
        | {steps.humanise_name(str(x.get("name") or "")) for x in nodes}
    raw_ch = s.changes if isinstance(s.changes, dict) else {}
    removed_refs = {str(x) for x in (raw_ch.get("removed") or []) if isinstance(x, str)}
    gone = [x for x in prev_steps
            if (str(x.get("id")) in removed_refs
                or steps.humanise_name(str(x.get("name") or "")) in removed_refs
                or (str(x.get("id")) not in present
                    and steps.humanise_name(str(x.get("name") or "")) not in present))]

    known_ids = {str(x.get("id")) for x in prev_steps} \
        | {str(b.get("id")) for b in workflow.get("nodes") or [] if b.get("id")}
    for n in nodes:
        if n.get("id") and str(n["id"]) in known_ids:
            continue
        outs = {str(q.get("name")) for q in (n.get("outputs") or []) if isinstance(q, dict)}
        for old in gone:
            if old.get("type") != n.get("type"):
                continue
            old_outs = {str(q.get("name")) for q in (old.get("outputs") or []) if isinstance(q, dict)}
            if not (outs & old_outs):
                continue
            has_old = bool(_cells.latest_ok_for(workflow["id"], old)) or steps.is_declined(workflow, old)

            has_new = bool(_cells.latest_ok_for(workflow["id"], n)) if not n.get("id") else False
            if has_old and not has_new:
                n["id"] = old["id"]
                s.notes.append(f'"{n.get("name")}" is "{old.get("name")}" renamed - '
                               "its recorded run and the user's earlier answer follow it (carry the step's id on every save; a new name on the same id is a rename).")
                gone.remove(old)
                break
    built_ids = {steps.humanise_name(str(b.get("name") or "")): b.get("id")
                 for b in workflow.get("nodes") or []}
    for n in nodes:
        if not n.get("id"):
            n["id"] = built_ids.get(steps.humanise_name(str(n.get("name") or ""))) \
                or _models.new_step_id()

        _cells.claim(workflow["id"], str(n.get("name") or ""), str(n["id"]))
    names = set()
    for i, n in enumerate(nodes):
        nm = str(n.get("name") or "").strip()
        if not nm:
            errors.append(f"node {i}: needs a name")
            continue
        if nm in names:
            errors.append(f"two steps share the name {nm!r} - every step "
                          "needs its own name (rename one)")
        names.add(nm)
        if n.get("type") not in steps.PORT_TYPE_NAMES:
            errors.append(f"node {nm!r}: type must be one of {sorted(steps.PORT_TYPE_NAMES)} "
                          "(a node is exactly one type, never mixed)")
        for kind_, key in (("input", "inputs"), ("output", "outputs")):
            steps.canon_ports(n.get(key))
            for p in n.get(key) or []:
                if not p.get("name"):
                    errors.append(f"node {nm!r}: every {kind_} port needs a name")
        for d in n.get("domains") or []:
            if str(d).startswith("169.254."):
                errors.append(f"node {nm!r} domains: {d!r} is a link-local "
                              "address (machine metadata, not a public service) - name the real service's hostname")
        _, cerrs = steps.clean_criteria(n.get("criteria") or [], "criterion")
        errors += [f"node {nm!r} {e}" for e in cerrs]
    s.plan_by_name = {n.get("name"): n for n in nodes}
    for n in workflow.get("nodes", []):
        s.built_by_ref[n.get("name")] = n
        s.built_by_ref[n.get("id")] = n
        s.built_by_ref[steps.humanise_name(n.get("name"))] = n
    for e in plan.get("edges") or []:
        if steps.step_by_ref(workflow, plan, e.get("src")) is None \
                or steps.step_by_ref(workflow, plan, e.get("dst")) is None:
            errors.append(f"edge {e.get('src')!r} -> {e.get('dst')!r}: both ends must "
                          "be plan nodes or existing nodes (by name or id)")
            continue
        if e.get("when") and not steps.expr_ok(e["when"]):
            errors.append(f"edge {e.get('src')!r} -> {e.get('dst')!r}: when must be a "
                          "valid python expression over the src outputs")
    s.props = plan.get("proposals") or []
    for i, p in enumerate(s.props):
        if not str(p.get("suggestion") or p.get("rationale") or "").strip():
            errors.append(f"proposal {i}: needs a suggestion (what to change and why)")
        p.setdefault("id", f"prop_{i}")
        if p.get("node_id"):
            p["node_id"] = steps.step_id_for(workflow, p["node_id"], plan) or p["node_id"]

    for e in plan.get("edges") or []:
        for k in ("src", "dst"):
            if isinstance(e.get(k), str):
                e[k] = steps.step_id_for(workflow, e[k], plan) or e[k]
    return None

def continuity(s: PlanSave) -> Optional[dict]:
    plan, workflow, nodes, errors = s.plan, s.workflow, s.nodes, s.errors
    prev_plan = workflow.get("plan") or {}
    by_node_id = {n["id"]: n for n in workflow.get("nodes") or []}
    for n in nodes:
        node = by_node_id.get(str(n.get("id") or ""))
        if node is not None and n.get("name") \
                and steps.humanise_name(node.get("name")) != n["name"]:
            s.renames[steps.humanise_name(node.get("name"))] = n["name"]
    raw_ch0 = s.changes if isinstance(s.changes, dict) else {}
    for r in raw_ch0.get("renamed") or []:
        if isinstance(r, dict) and str(r.get("from") or "").strip() \
                and str(r.get("to") or "").strip():
            s.renames[steps.humanise_name(r["from"])] = steps.humanise_name(r["to"])
    prev_steps = ([(steps.humanise_name(n.get("name")), str(n.get("id") or ""))
                   for n in (prev_plan.get("nodes") or [])
                   if str(n.get("name") or "").strip()]
                  if prev_plan.get("status") != "discarded" else [])
    if not prev_steps:
        prev_steps = [(steps.humanise_name(n.get("name")), str(n.get("id") or ""))
                      for n in ((s.turn.last_plan_attempt or {}).get("nodes") or [])
                      if str(n.get("name") or "").strip()]
    if nodes and prev_steps:
        raw_ch = s.changes if isinstance(s.changes, dict) else {}
        removed = {steps.humanise_name(x) for x in (raw_ch.get("removed") or [])
                   if str(x or "").strip()}
        new_names = {steps.humanise_name(n.get("name")) for n in nodes
                     if str(n.get("name") or "").strip()}
        new_ids = {str(n.get("id")) for n in nodes if n.get("id")}
        vanished = [nm for nm, pid in prev_steps
                    if nm not in new_names and nm not in removed
                    and nm not in s.renames
                    and not (pid and pid in new_ids)]
        if vanished:
            errors.append("these steps vanished from the plan: "
                          + ", ".join(f'"{v}"' for v in vanished)
                          + " - carry them forward (amend:true resends only the changed steps), or list them under changes.removed if the user agreed to drop them")
    if errors:
        if nodes:
            bad_edges = any(str(e).startswith("edge ") for e in errors)
            s.turn.last_plan_attempt = {"nodes": nodes,
                                        "summary": plan.get("summary"),
                                        "edges": [] if bad_edges
                                        else (plan.get("edges") or [])}
        return {"ok": False, "errors": errors}
    s.turn.last_plan_attempt = None
    return None

def evidence(s: PlanSave) -> Optional[dict]:
    workflow, plan, nodes = s.workflow, s.plan, s.nodes
    steps.normalise_changes(workflow, plan)
    if isinstance(plan.get("changes"), dict):
        plan["changes"] = steps.changes_to_ids(workflow, plan)
    s.carried = steps.drop_recorded_duplicates(workflow["id"], nodes)
    s.filled = steps.fill_from_cells(workflow["id"], nodes)
    s.seams = steps.derive_seam_types(workflow["id"], plan)

    steps.retype_settings(workflow, plan)
    return None

CHANGE_NOTE_NEEDED = (
    "a change to a built workflow needs `change_note`: {\"found\": \"<one or two plain sentences - what you found about what the user asked or noticed, in their words>\", \"will_change\": \"<what you will change and why - plain words, no code, tool or field names>\"}. The user reads these at the top of the card.")

def change_note(s: PlanSave) -> Optional[dict]:
    if steps.change_needs_approval(s.workflow, s.plan):
        note = s.plan.get("change_note") or {}
        if not note.get("found") or not note.get("will_change"):
            return {"ok": False, "errors": [CHANGE_NOTE_NEEDED]}
    return None

def scope(s: PlanSave) -> Optional[dict]:
    ch = s.plan.get("changes") if isinstance(s.plan.get("changes"), dict) else {}
    changed = {str(x) for x in ((ch.get("modified") or []) + (ch.get("added") or []))
               if isinstance(x, str)}
    s.is_change_save = bool(changed or (ch.get("removed") or []))
    s.warn_nodes = [n for n in s.nodes
                    if n.get("name") in changed or str(n.get("id") or "") in changed] \
        if s.is_change_save else s.nodes
    return None

def note_judgement(s: PlanSave) -> Optional[dict]:
    judgement = ("summari", "oppsummer", "classif", "klassifi", "prioriti",
                 "draft", "vurder", "judge", "most important", "viktigste")
    asked = (str((s.workflow.get("intent") or {}).get("summary") or "")
             + " " + str(s.plan.get("summary") or "")).lower()
    if any(v in asked for v in judgement) and not any(n.get("type") == "ai" for n in s.nodes):
        s.notes.append("the request sounds like a judgement task (summarise/classify/draft) but no step uses AI - a code-only approximation is usually a worse result, never a saving; add an ai step or confirm code truly covers it")
    return None

def note_untested(s: PlanSave) -> Optional[dict]:
    s.untested = steps.untested_steps(s.workflow, s.nodes)
    if not s.untested:
        return None
    stale = [u for u in s.untested if steps.CHANGED_MARK in u]
    never = [u for u in s.untested if steps.CHANGED_MARK not in u]
    if never:
        s.notes.append("not yet tried for real: "
                       + ", ".join(f'"{u}"' for u in never)
                       + " - run each as a cell before build_workflow.")
    if stale:
        s.stale_names = [u.split(steps.CHANGED_MARK, 1)[0] for u in stale]
        s.notes.append("code changed since the last recorded run: "
                       + ", ".join(f'"{n}"' for n in s.stale_names)
                       + " - re-run those cells so the recording matches (a send step's card lets the user decline; their No settles the step). A genuinely MECHANICAL revision (a renamed input, launch flags, a tidied line) may instead declare minor_revision: \"<one sentence why no re-test is needed>\" on that step - it builds untested and the built card says so.")
    return None

def note_instructions_early(s: PlanSave) -> Optional[dict]:
    if len(s.nodes) >= 2 and not str((s.workflow.get("intent") or {})
                                     .get("instructions") or "").strip():
        s.notes.append("the workflow has no user instructions yet - write them (save_intent, instructions=...) before the final build so it is not refused for this at the end.")
    return None

def note_identity_checks(s: PlanSave) -> Optional[dict]:
    conn_reads = {str(p.get("name")) for n in s.nodes
                  if reaches_outside(n)
                  for p in (n.get("inputs") or [])
                  if isinstance(p, dict) and p.get("name")}
    for n in s.warn_nodes:
        if n.get("type") != "user-input" or n.get("criteria"):
            continue
        steered = [str(p.get("name")) for p in (n.get("outputs") or [])
                   if isinstance(p, dict)
                   and str(p.get("type") or "text") in ("text", "longtext")
                   and str(p.get("name")) in conn_reads]
        if steered:
            s.notes.append(
                f'"{n.get("name")}" collects {", ".join(steered)}, which '
                "steers an outside call, but has no check of its own. If the workflow's identity makes something certain about the value (a URL family, a required marker), write it as a criterion on THIS step - a wrong value then stops here, in this step's name, before anything runs on it.")
    return None

def warn_ai_model(s: PlanSave) -> Optional[dict]:
    if any(n.get("type") == "ai" for n in s.warn_nodes) and not providers.any_node_model_ready():
        s.warnings.append("This workflow has an AI step, but no AI model is ready to run it (no workflow provider has a working key switched on). Add a key in Admin - or ask me to do this without AI and I'll say what that would cost.")
    return None

def unread_values(s: PlanSave) -> Optional[dict]:
    try:
        unread = plan_logic.unread_valued_variables(s.workflow, s.nodes)
    except Exception:
        return None
    if unread:
        listed = ", ".join(f'"{u}"' for u in unread)
        line = (f"The stored value{'s' if len(unread) > 1 else ''} "
                f"{listed} feed{'s' if len(unread) == 1 else ''} "
                "no step - if a step should honour "
                f"{'them' if len(unread) > 1 else 'it'}, it needs "
                "wiring before the build.")
        (s.notes if s.is_change_save else s.warnings).append(line)
    return None

def warn_ai_tuning(s: PlanSave) -> Optional[dict]:
    for n in s.warn_nodes:
        if n.get("type") != "ai":
            continue
        tuned = []
        if n.get("max_tokens") is not None \
                and providers.clamp_tokens(n.get("max_tokens")) != config.AI_MAX_TOKENS:
            tuned.append(f"more room for its answer "
                         f"({providers.clamp_tokens(n.get('max_tokens')):,} tokens)")
        if (providers.clamp_temperature(n.get("temperature")) or 0) > 0:
            tuned.append(f"some creative variation (temperature "
                         f"{providers.clamp_temperature(n.get('temperature')):g})")
        if tuned:
            s.warnings.append(f'The AI step "{n.get("name")}" is set to use '
                              + " and ".join(tuned) + " - building accepts that.")
    return None

def note_must_have(s: PlanSave) -> Optional[dict]:
    for n in s.nodes:
        for port in n.get("outputs") or []:
            sch = _shape_mod.from_port(port)
            obj = sch.get("items") if sch.get("type") == "array" else sch
            if not isinstance(obj, dict) or obj.get("type") != "object" \
                    or not obj.get("properties"):
                continue
            if not obj.get("required"):
                s.notes.append(
                    f'"{n.get("name")}" produces records on "{port.get("name")}" '
                    "with no must-have field - a row with nothing in it would pass. Mark the field the row is about (a name, an id) as required: true in that output's item_fields; leave the rest unmarked.")
    return None

def note_unexpected_input(s: PlanSave) -> Optional[dict]:
    for n in s.nodes:
        check = step_types.function(step_type(n), "parses_a_file_without_saying_so")
        if check:
            s.notes.extend(check(n))
    return None

def note_sender_outcomes(s: PlanSave) -> Optional[dict]:
    for n in s.nodes:
        check = step_types.function(step_type(n), "sends_without_saying_why")
        if check:
            s.notes.extend(check(n))
    return None

def note_read_only(s: PlanSave) -> Optional[dict]:
    for n in s.warn_nodes:
        if reaches_outside(n) and "read_only" not in n:
            s.notes.append(
                f'"{n.get("name")}" does not say whether it only READS: a '
                "connector without read_only counts as a WRITE and gets send-approval cards. Set read_only: true if it changes nothing outside; a real write states external_impact.")
    return None

def warn_batch_policy(s: PlanSave) -> Optional[dict]:
    successors: dict = {}
    for e in s.plan.get("edges") or []:
        successors.setdefault(e.get("src"), set()).add(e.get("dst"))
    by_nm = {n.get("name"): n for n in s.nodes}
    for n in s.warn_nodes:
        pol = str(n.get("on_invalid_items") or "").strip().lower()
        if pol not in ("proceed", "log"):
            continue
        how = ("carries on with the rest and raises an issue for them"
               if pol == "proceed" else
               "carries on with the rest and only notes them in the run history")
        line = (f'The step "{n.get("name")}" sets aside any items that do not '
                f"fit its expected shape and {how}, instead of stopping the run.")
        feeds_write = any(writes_outside(by_nm.get(d) or {})
                          for d in successors.get(n.get("name"), ()))
        if pol == "log" and feeds_write:
            line += (" It feeds a step that sends data out - noting rejected items only in the history means nobody is told; \"proceed\" (which raises an issue) is the safer setting.")
        s.warnings.append(line)
    return None

def warn_pace(s: PlanSave) -> Optional[dict]:
    for n in s.warn_nodes:
        try:
            rpm = float(n.get("requests_per_minute") or 0)
        except (TypeError, ValueError):
            rpm = 0.0
        if rpm > 30:
            s.warnings.append(
                f'The step "{n.get("name")}" is set to make about {rpm:g} '
                "requests a minute. That is fast enough that the service may start refusing them, and busy accounts can get restricted - spacing them out is the safer setting. Building accepts it as it is.")
    return None

def secret_inputs(s: PlanSave) -> Optional[dict]:
    try:
        owners = environments.secret_owners(s.workflow)
    except Exception:
        return None
    retyped = []
    for n in s.plan.get("nodes") or []:
        for port in n.get("inputs") or []:
            name = str(port.get("name") or "")
            if name in owners and port.get("type") != "secret":
                port["type"] = "secret"
                retyped.append(f'"{name}" on "{n.get("name")}"')
    if retyped:
        s.notes.append("typed as secret, since a stored secret has that name: "
                       + ", ".join(retyped))
    return None

# A blank setting a step reads while another variable with a value has a near-identical name
NEAR_NAME_MIN = 4

def note_near_names(s: PlanSave) -> Optional[dict]:
    variables = s.workflow.get("variables") or []
    valued = {str(v.get("name") or ""): v for v in variables
              if not v.get("secret") and environments.value_is_set(v.get("value"))}
    blank = [str(v.get("name") or "") for v in variables
             if not v.get("secret") and not environments.value_is_set(v.get("value"))]
    read = {str(p.get("name") or "") for n in s.plan.get("nodes") or []
            for p in n.get("inputs") or []}
    hints = []
    for b in blank:
        if b not in read or len(b) < NEAR_NAME_MIN:
            continue
        near = [v for v in valued if v != b and (b.lower() in v.lower() or v.lower() in b.lower())]
        if near:
            hints.append(f'"{b}" is blank while {", ".join(chr(34) + v + chr(34) for v in near)} '
                         "holds a value")
    if hints:
        s.notes.append("a setting a step reads is blank while a near-named one has the value - read that one, or store the value under the name the step reads: "
                       + "; ".join(hints))
    return None

def warn_unset_secrets(s: PlanSave) -> Optional[dict]:
    try:
        from runtime import executor as _exec
        secret_map = environments.secret_owners(s.workflow)
        unset: list = []
        for n in s.warn_nodes:
            names = ({p.get("name") for p in n.get("inputs") or []
                      if p.get("type") == "secret"}
                     | _exec._secret_literals(str(n.get("code") or "")))
            for sec in sorted(x for x in names if x):
                if not _exec._secret_val(sec, None, secret_map) and sec not in unset:
                    unset.append(sec)
        if unset:
            s.warnings.append("These private values are not saved yet: "
                              + ", ".join(unset)
                              + " - I'll ask for them before this can run; best to provide them now so the steps can be tested for real.")
    except Exception:
        pass
    return None

def note_domains(s: PlanSave) -> Optional[dict]:
    undeclared = []
    for n in s.nodes:
        lists_none = step_types.function(step_type(n), "lists_no_domains")
        if lists_none and lists_none(n):
            undeclared.append(n.get("name"))
    if undeclared:
        s.notes.append("these steps don't declare `domains`: "
                       + ", ".join(str(x) for x in undeclared)
                       + " - the connector guide lists them; declared, a live try needs no mid-step permission cards.")
    return None

def note_ai_descriptions(s: PlanSave) -> Optional[dict]:
    for n in s.nodes:
        check = step_types.function(step_type(n), "undescribed_output_fields")
        if check:
            s.notes.extend(check(n))
    return None

def warn_widened(s: PlanSave) -> Optional[dict]:
    widened = steps.contract_widened(s.workflow, s.plan)
    if widened:
        s.plan["contract_widened"] = widened
        for w in widened:
            s.warnings.append(
                f'"{w["step"]}" now accepts {w["accepted"]} of the '
                f'{w["of"]} rows the failed run rejected on "{w["port"]}" - '
                "the expected shape was widened to cover them.")
    else:
        s.plan.pop("contract_widened", None)
    if s.warnings:
        s.plan["warnings"] = s.warnings
    else:
        s.plan.pop("warnings", None)
    return None

def retarget_deliverables(s: PlanSave) -> Optional[dict]:
    kept = []
    for d in s.workflow.get("deliverables") or []:
        ref = d.get("node")
        built = s.built_by_ref.get(ref)
        step = s.plan_by_name.get(ref) \
            or (s.plan_by_name.get(built.get("name")) if built else None)
        if not step:
            kept.append(d)
            continue
        outs = [p.get("name") for p in step.get("outputs") or [] if p.get("name")]
        before = [p.get("name") for p in (built or {}).get("outputs") or [] if p.get("name")]
        port = d.get("port")
        if port and port not in outs and len(outs) == 1 and (not built or outs[0] not in before):
            s.retargeted.append(f'"{port}" -> "{outs[0]}" on "{step.get("name")}"')
            d["port"] = outs[0]

            new_port = next((p for p in step.get("outputs") or [] if p.get("name") == outs[0]), {})
            d["label"] = str(new_port.get("label") or outs[0]).replace("_", " ").capitalize()
        elif port and port not in outs and built and port in before:
            s.dropped_deliverables.append(f'"{d.get("label") or port}" ({port} on "{step.get("name")}")')
            continue
        kept.append(d)
    if s.dropped_deliverables:
        s.workflow["deliverables"] = kept
    return None

def logic_and_settings(s: PlanSave) -> Optional[dict]:
    workflow, plan = s.workflow, s.plan
    logic = plan_logic.check(workflow, plan)
    s.created_vars = environments.ensure_fallback_vars(workflow, logic.get("settings") or [])
    retired = environments.retire_unused_fallbacks(workflow, plan)
    if retired:
        fresh = store.load(workflow["id"])
        if fresh is not None:
            gone = set(retired)
            before = len(fresh.get("variables", []))
            fresh["variables"] = [v for v in fresh.get("variables", [])
                                  if v.get("name") not in gone
                                  or environments.value_is_set(v.get("value"))
                                  or v.get("secret")]
            if len(fresh["variables"]) != before:
                store.save(fresh)
    if s.created_vars:
        plan.setdefault("warnings", []).append(
            "These become settings you can edit any time: "
            + ", ".join(steps.humanise_name(n) for n in s.created_vars)
            + " - the first run asks for any that are still empty.")
        logic = plan_logic.check(workflow, plan)
    gaps = list(logic["findings"]) if not logic["ok"] else []
    if not gaps and plan.get("nodes") and not s.untested \
            and not str((workflow.get("intent") or {}).get("instructions") or "").strip():
        gaps = ["the workflow has no user instructions yet - save_intent with instructions: how to start it, where each setting lives and how to find its value, and what connected services must stay set up (someone new should be able to run it from that text alone)"]
    if gaps:
        plan["design_gaps"] = gaps
    else:
        plan.pop("design_gaps", None)
    s.logic = {**logic, "ok": not gaps, "findings": gaps}
    return None

def persist_and_card(s: PlanSave) -> Optional[dict]:
    workflow, plan = s.workflow, s.plan
    plan["status"], plan["ts"] = "draft", time.time()
    workflow["plan"] = plan
    if not steps.plan_approved(workflow) and s.nodes and not workflow.get("nodes"):
        s.turn.opening_card_show_now = True
    elif s.fix_tid and s.nodes and (not plan.get("fix_approved_ts")
                                    or steps.change_widened(s.workflow, plan)):
        s.turn.opening_card_show_now = True
    elif steps.change_needs_approval(workflow, plan):
        s.turn.opening_card_show_now = True
    elif steps.plan_steps_need_approval(workflow, plan):
        s.turn.opening_card_show_now = True
    steps.save(workflow)
    return None

PHASES: list[Callable[[PlanSave], Optional[dict]]] = [
    intake, amend, harness_keys, identity, continuity, evidence, change_note, scope,
    note_judgement, note_untested, note_instructions_early,
    note_identity_checks, warn_ai_model, unread_values, warn_ai_tuning,
    note_must_have, note_unexpected_input, note_sender_outcomes,
    note_read_only, warn_batch_policy, warn_pace, secret_inputs, warn_unset_secrets,
    note_domains, note_ai_descriptions, warn_widened,
    retarget_deliverables, logic_and_settings, note_near_names, persist_and_card,
]

NOTHING_DECLARED = (
    "plan saved, but nothing is declared as changing: no step differs from the built workflow and `changes` names none, so no card was shown. Declare the change: save again with changes {modified: [step names], added: [...], removed: [...]} beside the note, and no code yet - the card shows your declaration, the click approves it, and then you work the declared steps.")
NOTE_MAPPED = ("your fix_note was read as change_note, since this is a change you were asked for and not the fix of an issue (went_wrong as what you found). ")

# The declared steps whose code is still the built code: approved, not yet worked
def _declared_untouched(s: PlanSave) -> list:
    plan, workflow = s.plan, s.workflow
    names = [steps.humanise_name(str(x)) for x in plan.get("approved_changes") or []]
    built = {steps.humanise_name(str(n.get("name") or "")): n for n in workflow.get("nodes") or []}
    by_plan = {steps.humanise_name(str(n.get("name") or "")): n for n in plan.get("nodes") or []}
    out = []
    for nm in names:
        pn, b = by_plan.get(nm), built.get(nm)
        if pn is not None and b is not None and steps._step_matches_node(pn, b):
            out.append(nm)
    return out

def _result(s: PlanSave) -> dict:
    plan, workflow = s.plan, s.workflow
    ready = s.logic["ok"] and s.nodes and not s.untested
    approved = plan.get("fix_approved_ts") if s.fix_tid else plan.get("change_approved_ts")
    if workflow.get("nodes") and not s.fix_tid and plan.get("change_note") \
            and not plan.get("changes") and not approved:
        out = {"ok": True, "note": (NOTE_MAPPED if s.note_mapped else "") + NOTHING_DECLARED}
        if s.filled:
            out["assembled_from_cells"] = s.filled
        return out
    untouched = _declared_untouched(s) if approved else []
    if untouched:
        ready = False
    out = {"ok": True, "note": (NOTE_MAPPED if s.note_mapped else "") + ("plan saved"
                                + (" - showing the user the diagnosis and the planned change now, and waiting for their answer"
                                   if s.fix_tid and not plan.get("fix_approved_ts")
                                   else "" if steps.plan_approved(workflow)
                                   else " - showing it to the user now and waiting for their answer"))
                               + ("; proposals are built only if you ASK the user (ask_user, one option each) and pass the chosen ids as include_proposals - there is no card to tick them on"
                                  if s.props else "")
                               + (". Every step is ready: call build_workflow NOW in this same turn - building runs nothing and needs no approval (the user agreed to the plan at the start; the built card and done message are the record)."
                                  if ready else
                                  (". The change is approved - work the declared steps now (" + ", ".join(f'"{x}"' for x in untouched)
                                   + ") with run_cell or run_ai_step and save each as it is proven, then build_workflow.")
                                  if untouched else
                                  ". Keep working the steps - nothing else about the plan needs the user's agreement.")}
    if s.filled:
        out["assembled_from_cells"] = s.filled
    if s.carried:
        out["carried_from_recordings"] = s.carried
        out["carried_note"] = (
            "you re-typed code or a recorded-run test for these steps - I used the recording instead (identical, and it is the proof the build checks against). Omit `code` and `tests` for any step a cell has run: it saves you writing them and the user waiting for it.")
    if s.notes:
        out["notes"] = s.notes
    if s.seams:
        out["seam_types_derived"] = s.seams
    if s.retargeted:
        out["deliverables_retargeted"] = s.retargeted
    if s.dropped_deliverables:
        out["deliverables_dropped"] = s.dropped_deliverables
        s.notes.append("no longer kept as a result, since the change removed the output: "
                       + ", ".join(s.dropped_deliverables))
    if s.stale_names:
        out["discarded_tests"] = s.stale_names
        out["discarded_tests_note"] = (
            "the code you supplied for these steps differs from their recorded working runs, so they count as untested again and the build will refuse until each cell re-runs. If you did not mean to change them, save_plan again with the code field OMITTED for those steps - the already-tested code fills back in automatically.")
    if not s.logic["ok"]:
        out["design_gaps"] = s.logic["findings"]
        out["note"] = ("plan saved as a work in progress (the user sees the steps on the canvas, but no card yet) - its logic does not hang together: fix these in the design and save_plan again BEFORE building: " + "; ".join(s.logic["findings"]))
    return out

# One save: the phases in order, then the result
def save(workflow: dict, plan: dict) -> dict:
    s = PlanSave(workflow, plan)
    for phase in PHASES:
        refusal = phase(s)
        if refusal is not None:
            return refusal
    return _result(s)
