# What happens around a model turn: chat intake, the build, fixes and their evidence briefs
from __future__ import annotations

import json
import re
import time
from typing import Optional

import config
from runtime import corpus
from storage import environments
from runtime import executor
from runtime import run_state
from agent import interactions
from agent import loop
from agent import steps
from agent import node_tools
from agent import plan_check
from agent import plan_logic
from agent import previews
import providers
import turns
from storage import secrets_store
from storage import settings
from agent import skills
from agent import turnstate
from storage import store
import step_types
from step_types import OUTSIDE_TYPES, writes_outside

_TOOL_LINES = {
    "validate_node": 'completeness check on "{node}"',

    "run_node": 'trying "{node}" on a stored input',
    "run_cell": 'working step "{name}" for real',
    "read_earlier_result": "reading back an earlier result",
    "build_step": 'building "{name}" from its tested run',
    "run_ai_step": 'trying the AI step "{name}" for real',
    "read_node": 'reading "{node}"',
    "read_cases": 'reviewing past runs of "{node}"',
    "list_nodes": "looking over the workflow",
    "list_samples": "checking for uploaded files",
    "query_corpus": "checking the run history",
    "corpus_summary": "reviewing the run history",
    "diagnose_case": "recording what went wrong and why",
    "set_deliverables": "choosing which results each run keeps for you",
    "declare_variables": "noting the settings this workflow can use",
    "remove_variables": "removing settings this workflow no longer needs",
    "save_plan": "writing up the plan",
    "save_intent": "updating the workflow's purpose",
    "bind_variable": "recording which environment to use",
    "build_workflow": "building your workflow",
    "resolve_issue": "looking into the issue",
    "close_issue": "closing the issue - nothing in the workflow needs to change",
    "ask_user": "asking you",
    "share_file": 'sharing "{name}" with you',
    "delete_node": "removing a step",
    "preview_run": "previewing what a run would do",
}

_REASON_CLIP = 400

# The plain one-line description of each tool call the status line shows
def _describe_tool(workflow: dict, name: str, tool_input: dict) -> str:
    inp = tool_input or {}
    def node_name(nid):
        n = next((n for n in workflow["nodes"] if n["id"] == nid), None)
        return n["name"] if n else (str(nid) if nid else "a step")
    if name.startswith("mcp__cryogram__"):
        short = name.replace("mcp__cryogram__", "")
        if short in ("connect", "disconnect"):
            if inp.get("edges"):
                return f"putting {len(inp['edges']) + 1} steps in order"
            verb = "ordering" if short == "connect" else "unlinking"
            return f'{verb} "{node_name(inp.get("src"))}" and "{node_name(inp.get("dst"))}"'
        if short == "validate_node" and inp.get("node_ids"):
            return f"completeness check on {len(inp['node_ids'])} steps"
        if short == "read_node" and inp.get("node_ids"):
            named = [node_name(i) for i in inp["node_ids"]]
            known = [n for n in named if n != "a step"]
            if len(known) == len(named) and 1 <= len(known) <= 3:
                return "reading " + ", ".join(f'"{n}"' for n in known)
            return f"reading {len(named)} steps"
        if short == "save_learning":
            return f"noting down what I learned: {inp.get('title', '')}".strip()
        if short == "read_sample":
            return f"reading sample {inp.get('name', '')}".strip()
        if short == "load_skill":
            sid = str(inp.get("id") or "").rsplit("/", 1)[-1]
            if not sid or sid[:1].isdigit() \
                    or sid.startswith(("learning-", "note-", "learned", "acceptance")):
                return "checking my notes on the approach"
            named = {"browser": "browser-run websites",
                     "browser-login": "website logins",
                     "database": "databases", "webhook": "webhooks",
                     "email-inbox": "email inboxes"}
            topic = named.get(sid) or " ".join(
                w.capitalize() for w in sid.split("-"))
            return f"reading up on {topic}"
        if short == "save_plan":
            plan = workflow.get("plan") or {}
            plan_steps = plan.get("nodes") or []
            if plan_steps:
                try:
                    untested = steps.untested_steps(workflow, plan_steps)
                except Exception:
                    untested = ["?"]
                return ("writing down the final blueprint" if not untested
                        else "updating the plan")
            return "writing up the plan"
        if short in ("run_cell", "run_ai_step"):
            cname = steps.humanise_name(str(inp.get("name") or ""))
            known = ({str(n.get("name")) for n in workflow.get("nodes") or []}
                     | {str(n.get("name")) for n in
                        (workflow.get("plan") or {}).get("nodes") or []})
            if cname not in known:
                why = str(inp.get("reason") or "").strip().rstrip(".")
                if why:
                    return (why[:1].lower() + why[1:_REASON_CLIP]
                            + ("..." if len(why) > _REASON_CLIP else ""))
                return "double-checking a detail"
            inp = {**inp, "name": cname}
        line = _TOOL_LINES.get(short)
        if line:
            text = line.format(node=node_name(inp.get("node_id")),
                               name=inp.get("name", "?"))

            if short in ("run_cell", "run_ai_step"):
                why = str(inp.get("reason") or "").strip().rstrip(".")
                if why:
                    text += (" - " + why[:1].lower() + why[1:_REASON_CLIP]
                             + ("..." if len(why) > _REASON_CLIP else ""))
            return text
        return short.replace("_", " ")

    if name == "WebSearch":
        return f"looking something up: {inp.get('query', '')}".strip()
    return "working on it"

_SINGLE_NODE_TOOLS = {"validate_node", "run_node", "preview_run"}

# The single step a tool acts on - the canvas pulse's signal, never scraped from narration
def _tool_node(workflow: dict, name: str, tool_input: dict) -> Optional[str]:
    if not name.startswith("mcp__cryogram__"):
        return None
    short = name.replace("mcp__cryogram__", "")
    inp = tool_input or {}
    if short in ("connect", "disconnect"):
        return None
    if short == "validate_node" and inp.get("node_ids"):
        return None
    if short in ("create_node", "run_cell", "run_ai_step", "build_step") \
            and inp.get("name"):
        return steps.humanise_name(str(inp["name"]))
    nid = inp.get("node_id")
    if short in _SINGLE_NODE_TOOLS and nid:
        n = next((n for n in workflow["nodes"] if n["id"] == nid), None)
        return n["name"] if n else None
    return None

# The step a build event is about, so progress shows one line per step
def _phase_of(ev: dict) -> Optional[str]:
    if ev.get("type") != "tool":
        return None
    txt = ev.get("text", "") or ""
    if "reference data" in txt.lower():
        return None
    m = re.search(r'"([^"]+)"', txt)
    return m.group(1) if m else None

# Adds one Building line per step the first time it is touched; raw events pass through unchanged
def _build_progress_emitter(raw_emit):
    built_steps: list[str] = []
    cur_step = [None]

    def emit0(ev):
        raw_emit(ev)

        step = ev["node"] if "node" in ev else _phase_of(ev)
        if step and step != cur_step[0]:
            cur_step[0] = step
            if step not in built_steps:
                built_steps.append(step)

            if not (ev.get("type") == "phase"
                    and ev.get("text") == f'Finishing "{step}"'):
                raw_emit({"type": "phase", "text": f'Finishing "{step}"',
                          "node": step})

    return emit0, built_steps

# The credential and env var the chosen builder auth needs; subscription auth never reaches workflow steps
def _master_auth() -> tuple[str | None, str, str]:
    model = settings.get()["master_ai"].get("model", "")
    provider = providers.provider_for_model(model)
    auth = providers.auth_type(provider)
    if auth in ("codex-subscription", "codex-api-key"):
        raise RuntimeError("a codex builder has no SDK credential env")
    key_name = provider.get("key_name") or "ANTHROPIC_API_KEY"
    env_var = ("CLAUDE_CODE_OAUTH_TOKEN"
               if auth == "claude-subscription"
               else "ANTHROPIC_API_KEY")
    return secrets_store.get_secret(key_name, secrets_store.OWNER_APP), key_name, env_var

# The is-the-builder-ready pre-check; a codex subscription carries no credential of ours, so its answer is structural
def _master_key() -> tuple[str | None, str]:
    model = settings.get()["master_ai"].get("model", "")
    provider = providers.provider_for_model(model)
    auth = providers.auth_type(provider)
    if auth == "codex-subscription":
        from agent import codex_engine
        return ("codex" if codex_engine.connected_hint() else None), ""
    if auth == "claude-subscription":
        from agent import claude_code
        return ("claude" if claude_code.connected_hint() else None), ""
    if auth == "codex-api-key":
        key_name = provider.get("key_name") or ""
        return secrets_store.get_secret(key_name, secrets_store.OWNER_APP), key_name
    value, key_name, _ = _master_auth()
    return value, key_name

# Attached environments as names, kinds and secret flags - never values
def _environment_context(workflow: dict) -> str:
    envs = environments.attached(workflow)
    others = environments.unattached_summary(workflow)

    others_line = (("\n"
                    "Other environments on this computer, NOT attached to this workflow: " + "; ".join(others) + ". If the workflow needs one of these, tell the user to attach it (the environment control on the workflow page) - never ask them to re-type a value that is already stored.\n")
                   if others else "")
    if not envs:
        return ("[context] This workflow has no environments attached." +
                others_line) if others else ""
    blocks = []
    for env in envs:
        lines = [f"- {v['name']} ({v.get('kind', 'config')}"
                 f"{', secret' if v.get('secret') else ''})"
                 for v in env.get("variables", [])]
        blocks.append(f'Environment "{env["name"]}":\n'
                      + ("\n".join(lines) or "- (no variables yet)"))
    overlap_lines = []
    for name, r in sorted(environments.resolve(workflow, envs).items()):
        if r["ambiguous"]:
            names = " and ".join(c["name"] for c in r["ambiguous"])
            overlap_lines.append(
                f'- "{name}" is set in {names}: if the workflow uses it, ask the '
                "user which one (ask_user, options = the environment names) and record the answer with bind_variable - never guess")
        elif r["source"] not in (None, "workflow") and len(envs) > 1 \
                and sum(1 for e in envs
                        if any(v["name"] == name for v in e.get("variables", []))) > 1:
            overlap_lines.append(
                f'- "{name}" currently comes from "{r["env_name"]}" (already '
                "chosen; bind_variable changes it if the user asks)")
    tail = ("\nOverlapping names:\n" + "\n".join(overlap_lines) + "\n"
            if overlap_lines else "")
    plural = "environments" if len(envs) > 1 else "environment"
    return (f"[context] This workflow has {len(envs)} {plural} attached. Name a "
            "variable EXACTLY to pull its value at run time; ask the user when a match is plausible but uncertain.\n"
            + "\n".join(blocks) + tail + others_line + "\n")

# A compact skeleton of the saved workflow, injected each turn so changes start from the current structure
def _workflow_context(workflow: dict) -> str:
    nodes = workflow.get("nodes") or []
    plan_steps = [pn for pn in ((workflow.get("plan") or {}).get("nodes") or [])
                  if pn.get("id") and not any(n.get("id") == pn.get("id") for n in nodes)]
    if not nodes:
        if not plan_steps:
            return ""
        return ("[plan] Steps saved so far, not built yet (id - name; the id is the step's identity - carry it on a save that renames the step): "
                + "; ".join(f"{pn['id']} - {pn.get('name')}" for pn in plan_steps)
                + "\n")
    by_id = {n["id"]: n.get("name") or n["id"] for n in nodes}
    lines = []
    for n in nodes:
        t = n.get("type")
        t = t.value if hasattr(t, "value") else str(t)
        io = (",".join(p["name"] for p in n.get("inputs", [])) or "-") + " -> " \
             + (",".join(p["name"] for p in n.get("outputs", [])) or "-")
        note = f" ({n['intention']})" if n.get("intention") else ""

        lines.append(f"- {n.get('name')} ({n.get('id')}) [{t}] {io}{note}")
    edges = "; ".join(
        f"{by_id.get(e['src'], e['src'])} -> {by_id.get(e['dst'], e['dst'])}"
        + (f" when {e['when']}" if e.get("when") else "")
        for e in workflow.get("edges", []))
    return ("[context] The saved workflow's current steps (read_node shows a step's full config; this summary is already current - no need to call list_nodes). These steps are BUILT and finished. On a change, work only the steps the change touches - never re-run a built step you are not changing. The id in brackets is the step's identity for life - carry it on a save that renames the step; edges and changes may name steps by name or id:\n"
            + "\n".join(lines)
            + (f"\norder (src finishes -> dst may start; no data travels "
               f"along these): {edges}" if edges else "") + "\n"
            + (("[plan] Steps saved but not built yet (id - name): "
                + "; ".join(f"{pn['id']} - {pn.get('name')}" for pn in plan_steps)
                + "\n") if plan_steps else "")
            + _run_surface(workflow) + _corpus_digest(workflow) + "\n")

# One line when real runs exist; an empty history adds nothing to the prompt
def _corpus_digest(workflow: dict) -> str:
    try:
        from runtime import corpus as _corpus
        ids = [n["id"] for n in workflow.get("nodes") or []]
        if not ids:
            return ""
        stats = _corpus.summary(workflow["id"], ids)
        if not stats:
            return ""
        fails = sum(int(s.get("failures") or 0) for s in stats.values())
        cases = sum(int(s.get("successes") or 0)
                    for s in stats.values()) + fails
        if not cases:
            return ""
        return (f"\n[runs] {cases} recorded run case(s), {fails} failure(s) "
                "- corpus_summary / query_corpus when the history matters to this turn.")
    except Exception:
        return ""

# What the user experiences on a run - form fields, gates, pauses - so the fixer stops asking them
def _run_surface(workflow: dict) -> str:
    nodes = workflow.get("nodes") or []
    if not nodes:
        return ""
    dependent = {e["dst"] for e in workflow.get("edges", [])}
    entry, midrun, gates = [], [], []
    for n in nodes:
        t = n.get("type")
        t = t.value if hasattr(t, "value") else str(t)
        if t == "user-input":
            ports = ",".join(p["name"] for p in n.get("outputs", []) or [])
            (midrun if n["id"] in dependent else entry).append(
                f'"{n.get("name")}" ({ports})')
        if t in OUTSIDE_TYPES and not n.get("read_only", False):
            gates.append(f'"{n.get("name")}"'
                         + (" [approval off]" if n.get("approval_suppressed")
                            else " [asks approval each run]"))
    stored = sorted({v.get("name") for v in workflow.get("variables", []) or []})
    secret_names = [v.get("name") for v in workflow.get("variables", []) or []
                    if v.get("secret")]
    halted = ""
    try:
        from storage import db as _db
        recs = [r for r in _db.runstate_all()
                if r.get("workflow_id") == workflow.get("id")
                and r.get("status") == "halted"]
        if recs:
            r = max(recs, key=lambda x: x.get("started", 0))
            name = next((n.get("name") for n in nodes
                         if n["id"] == r.get("halted_at")), r.get("halted_at"))
            done = [next((n.get("name") for n in nodes if n["id"] == s), s)
                    for s in (r.get("steps") or {})]
            halted = (f'\n  latest halted run: stopped at "{name}" '
                      f'(reason: {r.get("reason")}); steps that ran: '
                      + (", ".join(f'"{d}"' for d in done) or "none"))
    except Exception as e:
        try:
            from agent import chatlog as _cl
            _cl.append(workflow.get("id") or "", "brief-degraded",
                       {"part": "halted-run-digest", "error": str(e)[:200]})
        except Exception:
            pass
    out = ["[run surface] What a run looks like to the user (facts - never ask the user about these):"]
    out.append("  run-form fields (asked before start): "
               + (", ".join(entry) or "none"))
    if midrun:
        out.append("  mid-run pauses for input: " + ", ".join(midrun))
    out.append("  approval gates: " + (", ".join(gates) or "none"))
    out.append("  stored variables: " + (", ".join(stored) or "none")
               + (f" (secrets, stored - never re-collect: "
                  f"{', '.join(secret_names)})" if secret_names else ""))
    return "\n".join(out) + halted + "\n"

# Refreshes the caller's dict from disk without rebinding; a rebind once silently reverted a whole build's writes
def _reload_in_place(workflow: dict) -> dict:
    fresh = store.load(workflow.get("id") or "")
    if fresh:
        workflow.clear()
        workflow.update(fresh)
    return workflow
_SDK_MISSING_MESSAGE = ("A part of my setup is missing on this machine. Close the app and start it again with the launcher - that repairs it automatically.")

def _turn_failed(workflow: dict, e: Exception,
                 lead: str = "I ran into a problem and couldn't finish that just now") -> str:
    from agent import chatlog
    chatlog.append(workflow.get("id") or "", "turn-error",
                   {"error": f"{type(e).__name__}: {e}"})

    msg = str(e).lower()
    from agent import loop as _loop
    from agent import transport as _tr
    if isinstance(e, _tr.TransportError) and (
            "unable to connect" in msg or "connectionrefused" in msg
            or "connection refused" in msg or "connecterror" in msg
            or "getaddrinfo" in msg or "network" in msg):
        return _loop.failed_ending(
            "I couldn't reach the AI service (your internet, or the service itself). Nothing was lost: check the connection and send your message again.")
    if isinstance(e, _tr.TransportError) and str(e).strip():
        return _loop.failed_ending(str(e).strip())
    return _loop.failed_ending(lead if lead and lead != _loop.EMPTY_TURN_MESSAGE else "")

# One chat turn end to end, including chaining a build or fix the turn requested
def handle_chat_stream(workflow: dict, message: str, emit) -> dict:
    key, key_name = _master_key()
    if not key:
        return {"content": "You haven't connected an AI account yet. Open Admin to set one up, then we can get started.", "kind": "text"}

    carried = str(turnstate.of(workflow).fix_issue_id or "")
    brief = issue_brief_for(workflow, message, carried)
    message = str(message or "") + brief + _pasted_credential_note(message)
    if brief:
        m = _ISSUE_ID_RE.search(str(message or ""))
        turnstate.of(workflow).fix_issue_id = carried or (m.group(0) if m else "unknown")
    else:
        live = steps.fix_in_progress(workflow)
        if live:
            turnstate.of(workflow).fix_issue_id = live
            message = str(message or "") + _fix_continuation_brief(workflow, live)
    started = time.time()
    built_before = bool((workflow.get("plan") or {}).get("built_ts"))
    try:
        reply = _agent_turn(workflow, message, emit)
        if change_left_unfinished(workflow, reply, started, built_before):
            emit({"type": "tool", "text": "finishing the change"})
            from agent import chatlog as _cl
            _cl.append(workflow.get("id") or "", "change-nudge", {})
            reply = _agent_turn(workflow, CHANGE_UNFINISHED_NUDGE, emit)
        return reply
    except Exception as e:
        return {"content": _turn_failed(workflow, e), "kind": "text"}

CHANGE_UNFINISHED_NUDGE = (
    "You saved a change to the built workflow and then ended without building it or asking a question, so the user is left with a plan that differs from what runs. Finish the change now: prove the changed steps and call build_workflow, or ask the one question you need with ask_user.")

# True when a turn on a built workflow saved a change and ended on a plain message, with no card open and no build
def change_left_unfinished(workflow: dict, reply: dict, started: float, built_before: bool) -> bool:
    if not built_before or (reply or {}).get("stopped"):
        return False
    plan = workflow.get("plan") or {}
    if plan.get("status") != "draft" or float(plan.get("ts") or 0) < started:
        return False
    from agent import transcript as _transcript
    if _transcript.awaiting_user(workflow):
        return False
    return bool(str((reply or {}).get("content") or "").strip())

# The build: assemble every proven step into code, wire the plan's edges, and re-prove the result - no model involved
def build_now(workflow: dict, emit, include_proposals: Optional[list] = None) -> dict:
    plan = workflow.get("plan") or {}
    if plan.get("status") not in ("draft", "building"):
        return {"ok": False, "findings": [],
                "content": "There's nothing to build yet - tell me what you'd like and I'll put a plan together first."}

    steps.fill_from_cells(workflow["id"], plan.get("nodes") or [])
    plan_to_build = _filter_proposals(plan, include_proposals)
    plan["status"] = "building"

    domains = {str(d) for n in plan_to_build.get("nodes", [])
               for d in (n.get("domains") or [])}
    if domains:
        workflow["egress_allowlist"] = sorted(
            set(workflow.get("egress_allowlist") or []) | domains)
    store.save_turn(workflow)

    import copy as _copy
    before_build = {k: _copy.deepcopy(workflow.get(k))
                    for k in ("nodes", "edges", "deliverables")}

    def _put_back():
        for k, v in before_build.items():
            if v is None:
                workflow.pop(k, None)
            else:
                workflow[k] = v

    removed = (plan_to_build.get("changes") or {}).get("removed") or []
    if removed:
        by_ref = {n["id"]: n["id"] for n in workflow["nodes"]}
        by_ref.update({n.get("name"): n["id"] for n in workflow["nodes"]
                       if n.get("name")})
        for ref in removed:
            nid = by_ref.get(ref)
            if nid:
                node_tools.delete_node(workflow, nid)
        _reload_in_place(workflow)
        plan = workflow.get("plan") or plan
    prior_node_ids = {n["id"] for n in workflow["nodes"]}
    emit0, built_steps = _build_progress_emitter(emit or (lambda ev: None))

    from agent import transcript as _transcript
    pre = turns.flush_text(workflow.get("id") or "")
    if pre:
        _transcript.append_message(workflow, "assistant", pre)
    emit0({"type": "build-start"})
    try:
        from agent import assembler as _assembler
        from agent import receipts as _receipts
        emit0({"type": "phase",
               "text": "Assembling the workflow from the tested steps"})
        asm_errors = _assembler.assemble(workflow, plan_to_build, emit0)
        steps.auto_wire_plan_edges(workflow, plan_to_build)
        det = plan_check.check(workflow, plan_to_build, prior_node_ids)

        if not det["ok"] and _autofix_findings(workflow, plan_to_build,
                                               det["findings"], emit0):
            det = plan_check.check(workflow, plan_to_build, prior_node_ids)
        _keep_unread_outputs(workflow, emit0)
        findings = list(asm_errors) + list(det.get("findings") or [])
        receipt_notes: list = []
        cred_scan: dict = {}
        if not findings:
            emit0({"type": "phase",
                   "text": "Double-checking every step the way a real run uses it"})
            rec = _receipts.check(workflow, plan_to_build)
            findings = list(rec.get("findings") or [])
            cred_scan = rec.get("credential_scan") or {}

            receipt_notes = list(rec.get("notes") or [])
        ok = not findings
        plan = workflow.get("plan") or plan
        plan["validation"] = {"status": "pass" if ok else "discrepancies",
                              "findings": findings,
                              **({"notes": receipt_notes} if receipt_notes
                                 else {}),

                              **({"credential_scan": cred_scan}
                                 if cred_scan else {}),
                              "ts": time.time()}
        plan["status"] = "built" if ok else "draft"
        plan["build_log"] = built_steps
        if ok:
            plan["built_ts"] = time.time()
            workflow.pop("approval_grants", None)
        else:
            _put_back()
        workflow["plan"] = plan
        store.save_turn(workflow)
        emit0({"type": "validation", **plan["validation"]})
        if not ok:
            broken = []
            for pn in plan_to_build.get("nodes", []):
                node = next((n for n in workflow["nodes"]
                             if n.get("name") == pn.get("name")), None)
                if node is None or not node_tools.validate_node(
                        workflow, node["id"]).get("ok"):
                    broken.append(pn.get("name") or "a step")
            if broken:
                named = ", ".join(f'"{b}"' for b in broken[:_LIST_CLIP])
                if len(broken) > _LIST_CLIP:
                    named += f" and {len(broken) - _LIST_CLIP} more"
                what = (f"{named} {'is' if len(broken) == 1 else 'are'} "
                        "not working yet")
            else:
                plain = plain_findings(findings)
                what = "; ".join(plain) if plain else \
                    "a couple of details don't match the plan yet"
            return {"ok": False, "findings": findings, "content": what}

        store.record_version(workflow, "build")

        cont = _gate_ticket_fix(workflow, plan, emit0)

        plan.pop("change_approved_ts", None)
        workflow["plan"] = plan

        try:
            from agent import cells as _cells
            _cells.forget_captures(workflow["id"])
        except Exception:
            pass
        committed = _commit_pending_learnings(workflow)
        if committed:
            emit0({"type": "tool",
                   "text": f"noting down {committed} thing"
                           f"{'s' if committed != 1 else ''} learned "
                           "during this build"})

        plan["_must_have"] = steps.must_have_summary(workflow)

        if any(steps.node_type_of(n) == "ai" for n in workflow.get("nodes", [])) \
                and not providers.any_node_model_ready():
            plan["_done_warnings"] = [
                "This workflow has an AI step, and no AI model is ready to run it yet - add an API key for one of the providers in Admin (and check it is switched on) before you run it."]
        if cont:
            plan["_continue"] = cont
        steps.append_plan_entry(workflow, plan, force_new=True,
                                      head="built")

        from agent import loop as _loop
        _loop.landed(workflow.get("id") or "")
        for k in ("_must_have", "_done_warnings", "_continue"):
            plan.pop(k, None)

        plan.pop("changes", None)
        plan.pop("change_note", None)
        store.save_turn(workflow)
        return {"ok": True, "findings": [],
                "content": "Built and checked. The user sees the built card, and this turn ends here."}
    except Exception as e:
        _put_back()
        plan["status"] = "draft"
        workflow["plan"] = plan
        store.save_turn(workflow)
        return {"ok": False, "findings": [],
                "content": _turn_failed(workflow, e,
                                        "The build hit a problem and stopped")}
# Finding classes that need selection, not judgement, are fixed by the harness before any model round
def _autofix_findings(workflow: dict, plan_for_freeze: dict, findings,
                      emit) -> bool:
    changed = False
    if any("no step's output is kept" in str(f) for f in findings or []):
        nodes = workflow.get("nodes") or []
        with_out = {e.get("src") for e in workflow.get("edges") or []}
        items = []
        for n in nodes:
            if n["id"] in with_out or not n.get("outputs"):
                continue
            if steps.node_type_of(n) == "user-input":
                continue
            for p in n.get("outputs") or []:
                items.append({"node": n["id"], "port": p.get("name"),
                              "label": str(p.get("label") or p.get("name") or "")
                              .replace("_", " ").capitalize()})
        if items:
            r = node_tools.set_deliverables(workflow, items)
            if r.get("ok"):
                changed = True
                emit({"type": "tool",
                      "text": "keeping the final step's results for you"})
    return changed
# Keeps as a result every output no later step reads, except what a sending step reports back
def _keep_unread_outputs(workflow: dict, emit) -> list:
    nodes = workflow.get("nodes") or []
    read = {p.get("name") for n in nodes for p in n.get("inputs") or []}
    for e in workflow.get("edges") or []:
        read.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(e.get("when") or "")))
    kept = list(workflow.get("deliverables") or [])
    have = {(d.get("node"), d.get("port")) for d in kept}
    added = []
    for n in nodes:
        if steps.node_type_of(n) == "user-input" or writes_outside(n):
            continue
        for p in n.get("outputs") or []:
            name = p.get("name")
            if not name or name in read or p.get("type") == "secret" \
                    or (n["id"], name) in have:
                continue
            added.append({"node": n["id"], "port": name,
                          "label": str(p.get("label") or name).replace("_", " ").capitalize()})
    if added and node_tools.set_deliverables(workflow, kept + added).get("ok"):
        emit({"type": "tool",
              "text": f"keeping {len(added)} more result{'s' if len(added) != 1 else ''} "
                      "that no later step uses"})
        return [a["port"] for a in added]
    return []

# Deterministic findings in the user's words, never raw
def plain_findings(findings: list) -> list[str]:
    out: list[str] = []
    for f in findings or []:
        f = str(f)
        if "wired" in f or "routing condition" in f:
            p = "the connections between steps don't match the plan yet"
        elif "set_deliverables" in f:
            p = "which results each run keeps for you isn't set yet"
        elif "not in the plan" in f:
            p = "a leftover step needs removing"
        elif "marked removed but still exists" in f:
            p = "a step that should be gone is still there"
        else:
            p = ""
        if p and p not in out:
            out.append(p)
    return out

_LIST_CLIP = 4

# Only the suggestions the user ticked survive
def _filter_proposals(plan: dict, include: Optional[list]) -> dict:
    out = dict(plan)
    props = out.pop("proposals", None) or []
    keep = [p for p in props if p.get("id") in set(include or [])]
    if keep:
        out["proposals"] = keep
    return out

# Publishes queued learnings once the build is green; the anonymity check re-runs and a violator is skipped, never a crash
def _commit_pending_learnings(workflow: dict) -> int:
    from storage import learnings as _learnings
    q = workflow.get("pending_learnings") or []
    if not q:
        return 0
    n = 0
    for e in q:
        r = _learnings.save(e.get("title"), e.get("content"), workflow)
        if r.get("ok"):
            ids = workflow.setdefault("learning_ids", [])
            if r["id"] in ids:
                ids.remove(r["id"])
            ids.append(r["id"])
            n += 1
        else:
            try:
                from agent import chatlog as _cl
                _cl.append(workflow.get("id") or "", "learning-skipped",
                           {"title": str(e.get("title"))[:120],
                            "error": str(r.get("error"))[:200]})
            except Exception:
                pass
    workflow["pending_learnings"] = []
    return n

# A green fix build marks its issue ready to continue, and says which run the built card can offer to continue
def _gate_ticket_fix(workflow: dict, plan: dict, emit) -> Optional[dict]:
    ticket_id = plan.get("ticket_id")
    if not ticket_id:
        return None
    ticket = next((t for t in workflow.get("tickets", []) if t["id"] == ticket_id), None)
    node = next((n for n in workflow["nodes"]
                 if n["id"] == (ticket or {}).get("node_id")), None)
    if not ticket or not node:
        return None
    ntype = node.get("type")
    ntype = ntype.value if hasattr(ntype, "value") else str(ntype)

    if ntype in OUTSIDE_TYPES:
        regression_ok = True
        note = ("The fix was checked against this step's saved example - a live re-send would fire the real action again, so it is proven on the next run.")
        emit({"type": "regression", "ticket_id": ticket_id,
              "result": {"ok": True, "note": note}})
    else:
        case = next((c for c in corpus.cases(workflow["id"], node["id"], "failure")
                     if c.get("case_id") == ticket.get("case_id")), None)
        extra = [{"inputs": case.get("inputs", {})}] if case else []
        from agent import receipts as _rec
        res = _rec.regression_check(node, extra_cases=extra,
                                    workflow_id=workflow["id"])
        emit({"type": "regression", "ticket_id": ticket_id, "result": res})
        regression_ok = bool(res.get("ok"))
        note = (f"The fix passed all {res.get('total')} recorded cases, "
                "including the one that failed." if regression_ok
                else "The fix was built, but it does not pass every previously working case yet - it stays open.")
    if not regression_ok:
        ticket["status"] = "open"
        ticket["notes"] = note
    else:
        ticket["status"] = "ready"
        ticket["notes"] = note

        plan.pop("ticket_id", None)
        plan.pop("fix_approved_ts", None)
        workflow["plan"] = plan
    store.save_turn(workflow)
    if not regression_ok or ticket.get("continued") or not ticket.get("run_id"):
        return None
    rec = run_state.load(ticket["run_id"])
    if not rec or rec.get("status") != "halted":
        return None
    return {"ticket_id": ticket_id, "run_id": ticket["run_id"]}

# The failing node's contract and implementation, so the agent reasons without a read first
def _node_brief(node: Optional[dict]) -> dict:
    if not node:
        return {}
    cfg = node.get("config") or {}
    return {"name": node.get("name"), "type": node.get("type"),
            "inputs": node.get("inputs"), "outputs": node.get("outputs"),
            "code": cfg.get("code"), "prompt": cfg.get("prompt"),
            "model": cfg.get("model"), "criteria": cfg.get("criteria")}

_CHANGE_RAILS = (
    "\n"
    "\n"
    "HOW A CHANGE WORKS HERE:\n"
    "- The workflow is BUILT and this is a DELTA - work out which parts change, never re-derive the design. Amend the saved plan (amend:true with ONLY the changed steps, ids carried); every proven step stands.\n"
    "- A CHANGE EDITS ONLY THE STEPS IT NEEDS: every other step keeps its name, its code and its recording. A rename changes a name and nothing else; a removal takes out the step or the output named and leaves the rest as built. Folding two proven steps into one, or rewriting a step the change does not touch, throws away its proof and shows the user a card full of changes they never asked for.\n"
    "- A change that REPLACES a route removes the superseded steps in the SAME save (changes.removed): after rewiring a consumer to new outputs, an old step nothing reads any more still runs every time and its work is thrown away - two unconditional pathways to one outcome both run.\n"
    "- The user ALREADY asked for this. Never ask whether to proceed, never ask HOW to do it, never offer 'leave it as it is' for an obviously-correct repair - do it and say what you did in one line. Ask ONLY for a domain decision you genuinely cannot make (a business rule, a live write's go-ahead).\n"
    "- FIX WHAT THE ERROR NAMES: the smallest change that makes the named failure impossible, made IN PLACE (an undefined name is removed or defined; a wrong field name is corrected). Redesign - new steps, swapped approaches - only when the named failure cannot be fixed where it is, and then say why in one line before the save. A fix that replaces working structure to dodge a one-line error creates the next failure.\n"
    "- WORK BACKWARDS FROM THE CHANGE: before any tool call, answer three things - what exactly must behave differently, which step's code that touches, and what information you actually LACK to write it. Gather ONLY that; when the lack is nothing, the first tool call is the edit. Re-reading the workflow, grazing the corpus or opening a window first is how a two-line fix becomes an hour.\n"
    "- CHANGE EVERYTHING FIRST, PROVE ONCE: when the change touches several steps, revise ALL of them (one amend save) BEFORE running any proving cell - a step tested before a later change lands upstream of it just has to be tested again, and those re-runs (and their cards) exist only because of the ordering.\n"
    "- A CHANGE SHOWS WHAT YOU FOUND: your save_plan shows the user a card and waits, and it carries `change_note` {found, will_change} - what you found about what they asked or noticed, and what you will change and why, in plain words. It renders at the top of the card; never repeat it in your own message text.\n"
    "- FIXING AN ISSUE works the same way: YOU decided the change, so your save_plan shows the user a card and waits - and it carries `fix_note` {went_wrong, will_change}: two plain sentences FOR THE USER (what happened; what you will change and why that fixes it - no code, tool or field names). They render INSIDE the card - put them in fix_note ONLY and never repeat them in your own message text; the card itself lists only the steps that change. Their click approves; build in the same turn. AND a fix has THREE honest exits, not one: change the workflow; or close the issue with close_issue because the workflow was RIGHT (a malformed row it rejected - 'bad-data'; a cause fixed outside - 'fixed-elsewhere'). A brief above may restate the same three in that failure's own terms - it is the ONE list, never a second one. Decide which and say why in one sentence first. Never build a change to absorb data that is plainly broken, and never add a data-repair step nobody asked for.\n"
    "- PROVE THE FIX ON THE RUN'S OWN DATA: the failing step's real inputs are on the issue - chain them into the re-proving cell as \"$issue\" (same input name) or \"$issue:<name>\". Never invent a row to prove a step deep in a built workflow when a real run has already produced the data; if the step sends for real, the card asks and the user's yes/no on it is the whole decision.\n"
    "- A RUN THAT STOPPED ON THE WRONG KIND OF INPUT (reason input-shape: the step itself said the file/payload is not what it parses) is a question for the USER, not a code repair - ask how such files should be handled before doing anything.\n"
    "- IF THE USER GAVE A STEER with the fix (a preference, what they tried, what not to do - it opens the message), it binds: work inside it, and if it rules out the fix you would have made, say so and follow it.\n"
    "- The whole conversation is above you: values, decisions and preferences the user already gave still stand - never re-ask for one.\n"
    "- FINISH. Say what changed and stop - never end with 'let me know when you want to run it' or an offer to continue later.")

_THROTTLE_WORDS = ("429", "rate", "too many", "slow down", "unusual activity",
                   "captcha", "challenge", "quota", "temporarily blocked",
                   "whoa there", "try again later")

# When a site pushed back the brief states the two honest fixes - slow down, or change how the call is made - with this step's real numbers
def _route_ladder(ticket: dict, node: Optional[dict]) -> str:
    blob = (str(ticket.get("reason") or "")
            + " " + json.dumps(ticket.get("verdict") or {}, default=str)).lower()
    if not any(w in blob for w in _THROTTLE_WORDS):
        return ""
    cfg = (node or {}).get("config") or {}
    route = str(cfg.get("route") or "unstated")
    rpm = cfg.get("requests_per_minute")
    pace = f"{rpm:g} requests/minute" if rpm else "an unstated pace"
    nxt = {"api": "the IN-PAGE route (browser/api-routes tier 2: the call goes out from the real browser, with its headers, cookies and tokens)",
           "in-page": "the UI route (browser/api-routes tier 3: drive the real controls)",
           "ui": "no higher route - this is already the most faithful one, so the answer here is volume/pacing only"}.get(
        route, "the next route up (browser/api-routes)")
    return (
        "\n\nTHE SERVICE PUSHED BACK - this is not a broken step. It runs the "
        f"{route} route at {pace}. There are exactly TWO fixes, and the user "
        "picks:\n"
        "1. SAME route, less pressure - fewer items per run and/or wider spacing with jitter.\n"
        f"2. A more robust route - {nxt}.\n"
        "Load browser/api-routes before proposing either. Say which you recommend and why in one line, offer both, and NEVER respond by disguising the client (spoofed agents, rotating anything) - that risks the user's account instead of the run. Completed items are recorded and will not be repeated.")

# An empty list input is valid data; a crash on it is this step's own defect, never a case for new validation
def _empty_input_brief(case: Optional[dict], node: Optional[dict]) -> str:
    ins = (case or {}).get("inputs") or {}
    if not isinstance(ins, dict):
        return ""
    empties = [k for k, v in ins.items() if isinstance(v, list) and not v]
    if not empties:
        return ""
    names = ", ".join(f'"{e}"' for e in empties)
    return (f"\n\nEMPTY INPUT PRESENT: input(s) {names} arrived as an EMPTY "
            "list. Emptiness is judged once, at its producer's edge - here it is a VALID value. If the failure is a crash on the empty list, fix THIS step's code to run correctly on empty (loop over nothing, write empty outputs for that part, act fully on the non-empty inputs) - never add validation and never re-judge emptiness downstream.")

SAW_PAGE_TEXT_CHARS = 3_000

# When the failing run left no evidence at all, the brief says so instead of showing an empty record
def _nothing_kept(ticket: dict, case: Optional[dict]) -> str:
    if case or (ticket.get("saw") or {}):
        return ""
    return ("\n"
            "\n"
            "THIS RUN KEPT NOTHING: no failing case, no page, no traffic - the step was stopped in a way that recorded nothing, so there is no evidence to read. Say that plainly in your first sentence to the user. Reason from the step's own code and its earlier recordings; a live try is a last resort, and if you need one, say why before asking for it.")

# What the failing step saw, for the fix turn: the last exchanges, and a browser step's page at failure
def _saw_brief(ticket: dict) -> str:
    saw = ticket.get("saw")
    if not isinstance(saw, dict) or not saw:
        return ""
    lines = ["\n"
             "\n"
             "WHAT THE STEP SAW when it failed (recorded by the harness - read this before running anything):"]
    for t in saw.get("traffic") or []:
        head = str(t.get("response_head") or "").strip()
        lines.append(f"  - {t.get('method')} {t.get('host')}{t.get('path')} -> "
                     f"{t.get('status')} {t.get('reason') or ''}".rstrip()
                     + (f"\n    response: {head}" if head else ""))
    page = saw.get("page") or {}
    if page:
        lines.append(f"  - the page at failure: {page.get('url') or '?'}"
                     + (f' titled "{page.get("title")}"' if page.get("title") else ""))
        ref = page.get("page")
        if ref:
            try:
                from storage import blobstore
                html = blobstore.get(str(ref)).decode("utf-8", "replace")
                text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                lines.append("    visible text: " + text[:SAW_PAGE_TEXT_CHARS]
                             + (" ..." if len(text) > SAW_PAGE_TEXT_CHARS else ""))
                lines.append(f"    the whole page is stored as {ref}"
                             + (f"; a screenshot as {page['screenshot']}" if page.get("screenshot") else ""))
            except Exception:
                lines.append(f"    the whole page is stored as {ref}")

    chain = []
    if page.get("page"):
        chain.append('"$issue:page"')
    if saw.get("har"):
        chain.append("\"$issue:har\" (the browser's own recording of every request and response)")
    if page.get("screenshot"):
        chain.append('"$issue:screenshot"')
    chain += [f'"$issue:{n}"' for n in sorted(saw.get("files") or {})]
    if chain:
        lines.append("  READ THESE INSTEAD OF RE-RUNNING: pass them into a plain code cell as inputs - " + ", ".join(chain)
                     + ". A window is for something that never loaded, not for a page this run already kept.")
    return "\n".join(lines)

# A failed step's type may add a hint to the fix brief, such as a browser step's page state
def _browser_state_brief(ticket: dict, node: Optional[dict]) -> str:
    hint = step_types.function((node or {}).get("type"), "fix_brief_hint")
    if not hint:
        return ""
    return hint(node, json.dumps(ticket.get("verdict") or {}, default=str))

# The rows that missed the step's shape, counts leading, so a diagnosis reads the record instead of re-running the step
def _offending_brief(ticket: dict) -> str:
    off = ticket.get("offending")
    if not isinstance(off, list) or not off:
        return ""
    if str(ticket.get("reason") or "") == "input-shape":
        return ""
    parts = []
    for o in off:
        rows = o.get("rows") or []
        head = (f"{o.get('total')} of {o.get('of')} items did not fit the "
                f"expected shape" if (o.get("of") or 0) > 1
                else "the value that arrived did not fit the expected shape")
        lines = [f'output "{o.get("port")}": {head}']
        for r in rows:
            tag = f"item {r['item']}" if r.get("item") else "value"
            lines.append(f"  - {tag}: " + "; ".join(r.get("problems") or [])
                         + "\n    " + json.dumps(r.get("value"), default=str))
        parts.append("\n".join(lines))
    return ("\n\nWHAT ACTUALLY ARRIVED (the offending rows, as recorded):\n"
            + "\n".join(parts)
            + "\n"
              "\n"
              "THE FIX'S SAME THREE EXITS, in this failure's terms - say which BEFORE saving a plan or closing (the counts above are the first clue - one odd row reads differently from the whole feed changing shape):\n"
              "1. BAD DATA (reject at the edge) - the row is bad data and the step was right to stop; nothing about the workflow changes. Call close_issue(resolution='bad-data', note=...) with one plain sentence. You MAY offer, as an ask with one option, that the step set such rows aside and carry on in future runs - never build that unasked; fixing malformed source data is not this workflow's job.\n"
              "2. CHANGE THE WORKFLOW (widen the contract) - the source legitimately produces this second shape (a field that is sometimes absent, another type, a new choice). Mark it in the plan step's item_fields (optional: true / options / type) and save_plan amend:true - the plan then records what widened and the card says so, and the offending rows stay on the issue as the evidence. Say what widened.\n"
              "3. FIXED ELSEWHERE (not this workflow's job) - the cause sat outside and is dealt with there: close_issue(resolution='fixed-elsewhere', note=...).\n"
              "Never widen to make a failure go away when the row is plainly broken, and never build a data-repair step nobody asked for.")

# A wrong kind of file is a domain decision - the brief says ask the user, never add validation
def _input_shape_brief(ticket: dict) -> str:
    if str(ticket.get("reason") or "") != "input-shape":
        return ""
    off = ticket.get("offending") or []
    row = ((off[0].get("rows") or [{}])[0] if off else {}) or {}
    val = row.get("value") if isinstance(row.get("value"), dict) else {}
    found, expected = val.get("found"), val.get("expected")
    return ("\n"
            "\n"
            "THIS IS NOT A CODE DEFECT - THE INPUT WAS A DIFFERENT KIND OF THING. The step recognised that what it was given does not look like what it parses"
            + (f" (it needs: {json.dumps(expected, default=str)}" if expected is not None else "")
            + (f"; what arrived has: {json.dumps(found, default=str)})" if found is not None
               else (")" if expected is not None else ""))
            + ". The check that stopped the run IS the right behaviour - do NOT 'add validation' as the fix, and do not guess. ASK THE USER (ask_user, options, recommendation first) how files like this should be handled - the fix's same three exits, in this failure's terms:\n"
              "1. BAD DATA: it was the wrong file - the workflow is right to stop and say which columns/keys it needs (close_issue resolution='bad-data', with the sentence naming them).\n"
              "2. CHANGE THE WORKFLOW: this is ANOTHER FORMAT it should also accept - only the user knows; if yes, widen the reading step to handle both shapes (a fix plan with the diagnosis card), proving it on this very file.\n"
              "3. FIXED ELSEWHERE: not this workflow's job (close_issue resolution='fixed-elsewhere' or 'bad-data', as they say).\n"
              "Say the exit you chose in one sentence before saving or closing.")

# A failed AI call comes with its numbers and its known remedies, stated deterministically
def _ai_failure_brief(ticket: dict) -> str:
    af = ticket.get("ai_failure")
    if not isinstance(af, dict):
        return ""
    facts = [f"model {af.get('model') or 'unstated'}"]
    if af.get("elapsed_s") is not None:
        facts.append(f"ran {af['elapsed_s']}s of its "
                     f"{af.get('timeout_s')}s budget")
    else:
        facts.append(f"time budget {af.get('timeout_s')}s")
    facts.append(f"answer room {af.get('max_tokens'):,} tokens"
                 if af.get("max_tokens") else "answer room unstated")
    facts.append(f"was handed ~{af.get('input_chars'):,} characters"
                 if af.get("input_chars") else "input size unknown")
    if af.get("batch_items"):
        facts.append(f"largest list input: {af['batch_items']} items")
    remedies = {
        "timed-out": (
            "the model did not finish inside the time budget. The fixes that work, in order: (1) fewer items per call - a code step splits the batch and the AI step handles one chunk per call; (2) less output per item - return the judgement plus one identifying field, never a copy of the input; (3) only if the request is genuinely right-sized, declare a larger `timeout_seconds` on the plan step (it extends this wall)."),
        "truncated": (
            "the answer ran out of room before it finished. The fixes: (1) stop echoing input fields back out (the usual cause - return the judgement, join the rest downstream in code); (2) fewer items per call; (3) declare a larger `max_tokens` on the plan step only when the output is genuinely that big."),
        "blank": (
            "every field came back empty - a refusal or a prompt/schema mismatch, not a real result. Contrast the prompt with the declared output fields (the recorded try is the working example); check the prompt actually asks for what the ports declare."),
        "wrong-shape": (
            "the answer missed the declared shape even though the schema is enforced at the API layer - usually a declared field the prompt never mentions, or an enum the model cannot satisfy. Line the output ports up against what the prompt asks for."),
        "call-failed": (
            "the call failed outright - read the verdict's real message; this is not a timeout and not the service being busy (those route elsewhere)."),
    }.get(str(af.get("mode") or ""), "")
    return ("\n"
            "\n"
            "THE AI CALL'S OWN FACTS (captured at the failure, not guessed): " + "; ".join(facts) + ".\n"
            + ("KNOWN REMEDIES for this failure mode: " + remedies
               if remedies else ""))

# A file the step's model cannot take is a model-choice matter with two remedies, never something to investigate
def _file_unsupported_brief(ticket: dict) -> str:
    if str(ticket.get("reason") or "") != "file-unsupported":
        return ""
    said = " ".join((ticket.get("verdict") or {}).get("file") or [])
    return ("\n"
            "\n"
            "THE FILE NEVER REACHED THE MODEL - THIS IS A MODEL CHOICE, NOT A DEFECT. The run stopped before the call: " + said + " Nothing else about the step is wrong, so investigate nothing and re-run nothing. The two fixes, and only these: 1. Change this step's model to one the models line marks as reading that file type (save_plan amend:true with the step's `model`). 2. Add a code step before it that turns the file into text or rows for this step, and keep the model. Say which you recommend and why in one line, then do it.")

# How far a per-item step got before it stopped, for the repair: the items that went are kept, the repair works on the rest
def _progress_brief(ticket: dict) -> str:
    try:
        from runtime import run_state
        rec = run_state.load(str(ticket.get("run_id") or "")) or {}
    except Exception:
        return ""
    prog = rec.get("progress") if isinstance(rec.get("progress"), dict) else None
    if not prog or not prog.get("of"):
        return ""
    keys = [str(k) for k in prog.get("done_keys") or []]
    shown = ", ".join(keys[:20]) + (f" and {len(keys) - 20} more" if len(keys) > 20 else "")
    return ("\n\nHOW FAR IT GOT: " + str(prog.get("done", 0)) + " of " + str(prog["of"])
            + " items went and are recorded in the step's ledger (" + str(prog.get("input"))
            + " by " + str(prog.get("key")) + (": " + shown if shown else "") + "); "
            + str(prog.get("remaining", 0)) + " failed. When the run continues after your fix, the step reads its ledger and skips the ones that went, so the repair is judged on the ones that failed - do not invent a row to prove it, and do not change the step's per_item declaration or its checkpoint calls.")

# Assembles the repair prompt, pure and unit-testable, with the evidence injected up front
def _investigate_message(ticket: dict, node: Optional[dict], case: Optional[dict],
                         successes: Optional[list] = None,
                         contract: str = "") -> str:
    return (
        f"A run of this workflow failed and the user asked you to investigate "
        f"(ticket {ticket['id']}).\n"
        f"Failed node: {node.get('name') if node else '?'} "
        f"({ticket.get('node_id')}, type {node.get('type') if node else '?'})\n"
        f"Halt reason: {ticket.get('reason')}\n"
        f"Verdict: {json.dumps(ticket.get('verdict') or {}, default=str)}\n\n"
        "THE FAILING STEP (its declared I/O + implementation - already here, no need to read_node):\n" + json.dumps(_node_brief(node), default=str)

        + "\n"
          "\n"
          "FAILING CASE (from the step's corpus - inputs, and what the step actually produced):\n"
        + json.dumps(previews.guard(case or {}), default=str)
        + _nothing_kept(ticket, case)
        + "\n"
          "\n"
          "RECENT SUCCESSES for contrast (already here, no need to read_cases unless you want more):\n"
        + json.dumps(previews.guard(successes or []), default=str)
        + (("\n\n" + contract) if contract else "")
        + _route_ladder(ticket, node)
        + _ai_failure_brief(ticket)
        + _file_unsupported_brief(ticket)
        + _input_shape_brief(ticket)
        + _offending_brief(ticket)
        + _progress_brief(ticket)
        + _empty_input_brief(case, node)
        + _browser_state_brief(ticket, node)
        + _saw_brief(ticket)
        + "\n"
          "\n"
          "Work the repair skillset: invoke your 40-corpus-and-verdicts skill first, diagnose the failure CLASS (contrast the failing input with the successes above), record it with diagnose_case, then design a class-level fix and save_plan. THE FIX BEING BUILT CLOSES THE ISSUE - there is nothing to write onto it. Tell the USER in chat what went wrong and what you changed (one or two plain sentences: that is the record); anything GENERAL you learned about how the outside system behaves is a save_learning. Never run the saved workflow; run_node on the broken code/ai node with the failing input is your ceiling."
        + _CHANGE_RAILS)

# The repair entry: one agent turn with the failing case, launched only by the user's click
def investigate(workflow: dict, ticket: dict, emit=None) -> dict:
    key, key_name = _master_key()
    if not key:
        return {"content": "You haven't connected an AI account yet. Open Admin to set one up, then I can look into this.", "kind": "text"}
    node = next((n for n in workflow["nodes"] if n["id"] == ticket.get("node_id")), None)
    nid = ticket.get("node_id") or ""
    case = next((c for c in corpus.cases(workflow["id"], nid, "failure")
                 if c.get("case_id") == ticket.get("case_id")), None)

    successes = corpus.cases(workflow["id"], nid, "success")[-3:]
    for t in workflow.get("tickets", []):
        if t["id"] == ticket["id"]:
            t["status"] = "investigating"

    turnstate.of(workflow).fix_issue_id = ticket["id"]
    store.save_turn(workflow)
    started = time.time()
    contract = ""
    if node is not None:
        try:
            from agent import evidence
            contract = evidence.step_contract(workflow, node)
        except Exception as e:
            contract = ""
            try:
                from agent import chatlog as _cl
                _cl.append(workflow.get("id") or "", "brief-degraded",
                           {"part": "step-contract", "error": str(e)[:200]})
            except Exception:
                pass
    message = _investigate_message(ticket, node, case, successes, contract)

    def _back_to_open() -> None:
        for t in workflow.get("tickets", []):
            if t["id"] == ticket["id"] and t.get("status") == "in-progress":
                t["status"] = "open"
        store.save_turn(workflow)
    try:
        reply = _agent_turn(workflow, message, emit)
    except ModuleNotFoundError:
        _back_to_open()
        return {"content": _SDK_MISSING_MESSAGE, "kind": "text"}
    except Exception as e:
        _back_to_open()
        return {"content": _turn_failed(workflow, e), "kind": "text"}
    plan = workflow.get("plan") or {}
    tkt = next((t for t in workflow.get("tickets", []) if t["id"] == ticket["id"]), None)
    if tkt:
        if plan.get("status") == "draft" and plan.get("ts", 0) >= started:
            tkt["status"] = "in-progress"
        elif tkt.get("status") in ("investigating", "in-progress"):
            tkt["status"] = "open"
        store.save_turn(workflow)
    return reply

_ISSUE_ID_RE = re.compile(r"\btkt_[0-9a-f]{6,}\b")

# The Fix click is a chat message; this attaches the evidence brief the message alone would lose
def issue_brief_for(workflow: dict, message: str, issue_id: str = "") -> str:
    tid = str(issue_id or "")
    if not tid:
        m = _ISSUE_ID_RE.search(str(message or ""))
        if not m:
            return ""
        tid = m.group(0)
    ticket = next((t for t in workflow.get("tickets", [])
                   if t.get("id") == tid), None)
    if not ticket:
        return ""
    node = next((n for n in workflow.get("nodes", [])
                 if n["id"] == ticket.get("node_id")), None)
    case = next((c for c in corpus.cases(workflow["id"],
                                         ticket.get("node_id") or "", "failure")
                 if c.get("case_id") == ticket.get("case_id")), None)
    successes = corpus.cases(workflow["id"], ticket.get("node_id") or "",
                             "success")[-3:]
    contract = ""
    if node is not None:
        try:
            from agent import evidence
            contract = evidence.step_contract(workflow, node)
        except Exception:
            contract = ""
    return ("\n\n" + _investigate_message(ticket, node, case, successes,
                                           contract)
            + _run_siblings(workflow, ticket))

# A message carrying a credential shape gets one line telling the agent to store it by name; the match is never quoted
def _pasted_credential_note(message: str) -> str:
    from agent import gate as _gate
    found = [w for w in _gate.find_hardcoded_secrets(str(message or ""))
             if w.startswith("what looks like")]
    if not found:
        return ""
    return ("\n\n[note] this message contains " + " and ".join(found)
            + " - store it NOW with declare_variables {name, value, secret:true} under a clear name and use it by name; it is removed from the chat record by code. Do not remark on how it was sent.")

# The short brief for a turn continuing an open fix - which issue, what failed, where the approval stands
def _fix_continuation_brief(workflow: dict, tid: str) -> str:
    ticket = next((t for t in workflow.get("tickets") or []
                   if t.get("id") == tid), {}) or {}
    node = next((n for n in workflow.get("nodes") or []
                 if n.get("id") == ticket.get("node_id")), {}) or {}
    plan = workflow.get("plan") or {}
    stands = ("the user has approved the diagnosis card - build_workflow when the plan is ready"
              if plan.get("fix_approved_ts") else
              "the user has NOT approved a diagnosis card yet (a reply at the card is a revision) - the next save_plan shows a fresh card with the diagnosis (fix_note: what went wrong / what will change, updated if the change changed) and waits for their answer, then build_workflow")
    return ("\n\n[This conversation continues the fix for issue "
            f"{tid}: step \"{node.get('name') or ticket.get('node_id') or '?'}\" "
            f"stopped with {ticket.get('reason') or 'a problem'} - the "
            f"evidence brief is earlier in this chat. Where it stands: {stands}.]"
            + _CHANGE_RAILS)

# The same run's other issues, threaded in - a run that stopped twice is one story
def _run_siblings(workflow: dict, ticket: dict) -> str:
    rid = ticket.get("run_id")
    if not rid:
        return ""
    by_id = {n["id"]: n.get("name") or n["id"]
             for n in workflow.get("nodes", [])}
    lines = []
    for t in workflow.get("tickets", []):
        if t.get("run_id") != rid or t.get("id") == ticket.get("id"):
            continue
        lines.append(f'- "{by_id.get(t.get("node_id"), t.get("node_id"))}" '
                     f'stopped earlier in this run: {t.get("reason")}'
                     f' (issue {t.get("id")}, now {t.get("status")})')
    if ticket.get("landed") is False:
        lines.append("- the user CHECKED the outside system: this step's send NEVER ARRIVED - nothing fired, so once your fix is built the run continues by sending it again (no double-send risk)")
    for t in workflow.get("tickets", []):
        uo = t.get("user_outputs")
        if t.get("run_id") == rid and uo:
            nm = by_id.get(t.get("node_id"), t.get("node_id"))
            lines.append(
                f'- CAUTION: "{nm}"\'s outputs in this run were TYPED BY THE '
                f'USER after an unconfirmed send (given: '
                f'{", ".join(uo.get("given") or []) or "none"}; could not '
                f'see: {", ".join(uo.get("unsure") or []) or "none"}) - a '
                "failure downstream of it may be malformed hand-entered data, not the step's own code")
            if uo.get("unsure"):
                lines.append(
                    "- for each field the user could not see, DECIDE ITS OPTIONALITY: if the sending step can legitimately not produce it some runs, mark that output `optional: true` and make every reader run without it; if the workflow truly needs it every run, source it from somewhere real (the user, another step) - and when you cannot tell which, ask the user during this fix")
    if not lines:
        return ""
    return ("\n\n[this run's other issues]\n" + "\n".join(lines))

# Keeps the server's copy honest when the fix message arrives by another route
def mark_issue_in_progress(workflow: dict, message: str, issue_id: str = "") -> None:
    tid = str(issue_id or "")
    if not tid:
        m = _ISSUE_ID_RE.search(str(message or ""))
        if not m:
            return
        tid = m.group(0)
    for t in workflow.get("tickets", []):
        if t.get("id") == tid and t.get("status") in (None, "open"):
            t["status"] = "in-progress"

# One chat turn; it ends when the model stops calling tools or a turn-ending tool fires
def _agent_turn(workflow: dict, message: str, emit=None) -> dict:
    return loop.run_turn(workflow, message, emit, kind="chat")
