# The closed tool set the builder may call, and the executor that runs each call and shapes its result
from __future__ import annotations

import inspect
import json
import time as _time
from typing import Callable
from agent import steps
from agent import interactions, node_tools, skills
from agent import transcript as _transcript
from agent import turnstate

EXPLORATION_AIDS = frozenset({"web_search"})

_BUILD_FINDINGS = 6

_PY_TO_JSON = {str: "string", int: "integer", float: "number",
               bool: "boolean", dict: "object", list: "array"}

# Required-parameter lists come from each handler's own signature, so schema and code cannot drift apart
def _fn_required(fn: Callable) -> list[str]:
    out = []
    for name, p in inspect.signature(fn).parameters.items():
        if name == "workflow":
            continue
        if p.default is inspect.Parameter.empty \
                and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            out.append(name)
    return out

def _json_schema(spec_schema: dict, fn: Callable) -> dict:
    props = {k: (t if isinstance(t, dict) else {"type": _PY_TO_JSON.get(t, "string")})
             for k, t in (spec_schema or {}).items()}
    req = [r for r in _fn_required(fn) if r in props]
    return {"type": "object", "properties": props, "required": req, "additionalProperties": False}

_LOAD_SKILL_TOOL = {
    "name": "load_skill",
    "description": ("Load one guide by its id before you act on its subject: a guide for an outside service, a guide on how to do something, or a learning-* (something an earlier build found out; its title names the situation it applies to). The system prompt lists every id with WHEN it applies. A guide may name its own reference documents (id \"<guide>/<ref>\"), each with a line saying when to load it: load one as soon as that holds, and never guess what it says."),
    "input_schema": {"type": "object",
                     "properties": {"id": {"type": "string"}},
                     "required": ["id"], "additionalProperties": False}}

# The tool spec table the schemas and handlers both derive from
def _specs() -> list[tuple]:
    return list(node_tools._AGENT_SPECS)

# The tool schemas the model sees, derived from the tool specs so they cannot drift from the code
READ_ONLY_TOOLS = frozenset({
    "list_nodes", "read_node", "read_earlier_result", "list_samples",
    "read_sample", "read_cases", "query_corpus", "corpus_summary",
    "preview_run", "load_skill",
})

def schemas() -> list[dict]:
    out = [{"name": name, "description": desc,
            "input_schema": _json_schema(schema, fn)}
           for name, fn, desc, schema in _specs()]
    out.append(_LOAD_SKILL_TOOL)
    for s in out:
        if s["name"] in READ_ONLY_TOOLS:
            s["read_only"] = True
    return out

def _fns() -> dict[str, Callable]:
    return {name: fn for name, fn, *_ in _specs()}

# A secret typed into a card is routed by code into the secret store - the model only ever receives the name
_INTERNAL_WORDS = ("prove", "proven", "proof", "unproven", "skeleton",
                   "design gap", "design gaps", "harness", "corpus", "freeze",
                   "codify", "plan card", "schema", "seam",
                   "port", "ports", "node", "nodes", "recording", "recordings")
_INTERNAL_RE = None

# The internal words a piece of user-facing text carries, if any
def _internal_words(text: str) -> list[str]:
    import re as _re
    global _INTERNAL_RE
    if _INTERNAL_RE is None:
        _INTERNAL_RE = _re.compile(r"\b(" + "|".join(
            _re.escape(w) for w in _INTERNAL_WORDS) + r")\b", _re.I)
    seen: list[str] = []
    for m in _INTERNAL_RE.finditer(str(text or "")):
        w = m.group(1).lower()
        if w not in seen:
            seen.append(w)
    return seen

# Every card that pauses the turn is raised one way: the narration so far lands as a message above it, the card is appended as rendered and saved, the interaction is registered under the card's own id, and the turn waits for the answer
def _raise_card(workflow: dict, request_kind: str, payload: dict,
                interaction_kind: str, interaction_payload: dict, prefix: str):
    import turns as _turns

    if interactions.pending(workflow.get("id")):
        from agent import chatlog
        chatlog.append(workflow.get("id") or "", "card-already-waiting",
                       {"kind": request_kind, "title": str(payload.get("title") or payload.get("question") or "")})
        turnstate.of(workflow).ask_open = True
        return None, None, None
    pre = _turns.flush_text(workflow.get("id") or "")
    if pre:
        _transcript.append_message(workflow, "assistant", pre)

    from agent import loop as _loop
    _loop.landed(workflow.get("id") or "")
    iid = _transcript.new_iid(prefix)
    entry = _transcript.append_request(workflow, request_kind, payload, iid=iid)
    steps.save(workflow)
    interactions.create_sync(interaction_kind,
                             {"workflow_id": workflow["id"], **interaction_payload}, iid=iid)
    got = _wait_for_answer(workflow, iid)
    if got is not None:
        _loop.resumed(workflow.get("id") or "")
    return iid, entry, got

def _ask_user_owned(workflow: dict, emit, args: dict) -> dict:
    question = str(args.get("question") or "").strip()
    options = steps.option_labels(args.get("options"))
    secret = bool(args.get("secret"))
    secret_name = str(args.get("secret_name") or "")
    upload = bool(args.get("upload"))
    folder = bool(args.get("folder"))
    fields, ferr = steps.clean_ask_fields(
        args.get("fields"), secret=secret, upload=upload, folder=folder)
    if ferr:
        return {"error": ferr}

    if not question:
        return {"error": "the card would be empty - write the actual question in `question` and re-send."}

    if not (fields or options or secret or upload or folder):
        return {"error": "this question gives the user no way to answer on the card. " + steps.ASK_NEEDS_AN_ANSWER + " Re-send it that way."}

    leaked = _internal_words(question + " " + " ".join(options or []))
    if leaked:
        return {"error": "the card would show internal words the user does not have: " + ", ".join(leaked) + ". Say it in their words - tested / not yet tested / the step / the value / what it will do - and re-send."}

    if fields and not bool(args.get("allow_stored")):
        from storage import environments as _envs
        res = _envs.resolve(workflow) or {}
        stored = {n: str(r.get("value"))
                  for n, r in res.items()
                  if not r.get("secret") and not r.get("ambiguous")
                  and _envs.value_is_set(r.get("value"))}
        clash = [f for f in fields if f.get("name") in stored]
        if clash:
            have = "; ".join(f'{f.get("name")} = {stored[f.get("name")][:120]}'
                             for f in clash)
            return {"error": "these values are already stored - use them "
                             f"instead of re-asking: {have}. If you truly "
                             "need a NEW value, say why in the question and re-send with allow_stored=true."}

        elsewhere = _envs.unattached_holders(workflow, [f.get("name") for f in fields])
        if elsewhere:
            lines = "; ".join(f'"{n}" is in the environment "{e}"' for n, e in elsewhere)
            return {"error": f"already stored on this computer: {lines}. Tell "
                             "the user to attach that environment (the environment control on the workflow page) - never ask them to re-type a value it holds."}

    if secret and secret_name and not bool(args.get("allow_stored")):
        from storage import environments as _envs, secrets_store as _sstore
        owner = _envs.secret_owners(workflow).get(secret_name)
        if owner and _sstore.has_secret(secret_name, owner):
            return {"error": f'a value for "{secret_name}" is already in the '
                             "secret store - use it by name (get_secret), never re-ask just to make sure. Re-send with allow_stored=true only if the user said the value is wrong or must change."}
    over = steps.turn_over_error(workflow, "ask_user")
    if over:
        return {"error": over}

    payload = {"question": question, "options": options, "secret": secret,
               "secret_name": secret_name, "upload": upload, "folder": folder,
               **({"fields": fields} if fields else {})}

    iid, entry, got = _raise_card(workflow, "ask", payload, "ask",
                                  {"question": question, "secret": secret,
                                   "options": options}, "ask")
    if got is not None:
        return _ask_answered(workflow, emit, entry, got)

    turnstate.of(workflow).ask_open = True
    return {"ok": True, "asked": question}

PLAN_OK_LABEL = "Looks right - go ahead"

# Waits for the card's answer; a message typed meanwhile lands in the chat at once, and the wait goes on
def _wait_for_answer(workflow: dict, iid: str):
    from agent import reply as _reply
    return interactions.wait_sync(iid, on_tick=lambda: _reply.land_parked(workflow))

# The opening card: shown once before any step work, and the turn waits here for the user's click
def _opening_card_owned(workflow: dict, emit) -> dict:
    plan = workflow.get("plan") or {}

    fix_id = steps.fix_in_progress(workflow)

    payload = {**steps.opening_card_payload(workflow, plan, "plan"),
               "options": [PLAN_OK_LABEL],
               **({"fix": fix_id} if fix_id else {})}
    iid, entry, got = _raise_card(workflow, "blueprint", payload, "ask",
                                  {"question": plan.get("summary", ""),
                                   "options": [PLAN_OK_LABEL]}, "blu")
    if got is None:
        turnstate.of(workflow).ask_open = True
        return {"plan_shown": "waiting"}

    from agent import reply as _reply
    done = _reply.settle(workflow, entry, got)
    text, clicked = done["text"], done["clicked"]
    steps.save(workflow)
    if clicked and fix_id:
        return {"plan_shown": "approved",
                "note": "the user approved the fix - call build_workflow NOW in this same turn."}
    if clicked:
        return {"plan_shown": "approved",
                "note": "the user approved the plan. Get on with the work. The steps they approved are the contract: if a step is added or removed, or a step's type changes, the next save_plan shows them what changed and waits for their yes again, and build_workflow refuses until then. A rename asks nothing."}
    return {"plan_shown": "revised", "answer": text,
            "note": "the user replied instead of approving - that is feedback on the plan. Fold it in and save_plan again (amend:true); the updated plan goes back to them."}

# Applies a live answer on the turn thread; the card is never edited - the answer is its own transcript item
def _ask_answered(workflow: dict, emit, entry: dict, got) -> dict:
    ask = entry.get("payload") or {}

    from agent import reply as _reply
    done = _reply.settle(workflow, entry, got)
    text, shown = done["text"], done["shown"]
    steps.save(workflow)
    answer_text = "(provided)" if ask.get("secret") else text
    return {"ok": True, "answer": answer_text,

            **({"answers": shown} if isinstance(shown, dict)
               and not shown.get("typed") else {}),
            "note": "the user answered - continue from here. Never re-ask this question; a reply that changes direction is feedback to fold in."}

# One resolver for skills and saved learnings, so every reader agrees
def read_skill_or_learning(sid: str):
    sid = str(sid or "").strip()
    got = skills.read_static(sid)
    if got is None:
        from storage import learnings as _learnings
        if sid.startswith("learning-"):
            got = _learnings.read(sid[len("learning-"):])
        if got is None:
            got = _learnings.read(sid)
    return got

def _load_skill(workflow: dict, sid: str) -> dict:
    sid = str(sid or "").strip()
    got = read_skill_or_learning(sid)
    if got is None:
        return {"error": f"no skill {sid!r} - the system prompt lists the ids"}
    if skills.read_static(sid) is not None and (got.get("meta") or {}).get("kind") != "policy":
        from agent import active_skills
        active_skills.register(workflow.get("id") or "", sid, got)

    return {"ok": True, "id": sid, "content": got.get("content") or ""}

# Reads both error conventions, error and errors - a validation list once read as success
def _result_error(result: dict) -> str:
    err = result.get("error")
    if not err and result.get("errors"):
        err = "; ".join(str(x) for x in result["errors"])
    return str(err or "the action did not succeed")

# The tool's own ok flag is authoritative; either error convention means failure
def _result_ok(result: dict) -> bool:
    if not isinstance(result, dict):
        return True
    if result.get("error") or result.get("errors"):
        return False
    return result.get("ok", True) is not False

COUNT_YES = "Yes, that's about right"
COUNT_NO = "No, there should be more"

# After a browser step's try reads a list from a page, says when the try proved nothing about loading more, and asks the person when it came back short
def _list_count_check(workflow: dict, args: dict, result: dict) -> dict:
    if not (isinstance(result, dict) and result.get("ok")) or result.get("duplicate"):
        return result
    from models import step_type
    step = steps.step_by_ref(workflow, workflow.get("plan"), args.get("name"))
    if not step or step_type(step) != "browser" or not step.get("read_only"):
        return result
    ms = str(step.get("max_setting") or "").strip()
    if not ms:
        return result
    from runtime.executor import iterated_input_names
    lists_in = {p.get("name") for p in step.get("inputs") or [] if p.get("type") == "list"}
    if lists_in & iterated_input_names(str(args.get("code") or "")):
        return result
    given = (args.get("inputs") or {}).get(ms) if isinstance(args.get("inputs"), dict) else None
    if given is None:
        given = next((v.get("value") for v in workflow.get("variables") or []
                      if v.get("name") == ms), None)
    try:
        limit = int(given)
    except (TypeError, ValueError):
        return result
    out = result.get("output") if isinstance(result.get("output"), dict) else {}
    port = next((p for p in step.get("outputs") or []
                 if p.get("type") == "list" and isinstance(out.get(p.get("name")), list)), None)
    if not port or limit <= 0:
        return result
    found = len(out[port["name"]])
    what = str(port.get("label") or steps.humanise_name(port["name"])).lower()
    sname = step.get("name") or args.get("name")
    if found >= limit:
        note = (f"it returned exactly the maximum it was passed ({limit} {what}): if that "
                "is not above what the page shows before any scrolling, this try never showed the page loading more - try again with a maximum above the first screen's count")
        return {**result, "note": " ".join(x for x in (result.get("note"), note) if x)}
    if found == 0:
        note = (f"it found no {what} at all: the step is not reading the list yet - check "
                "the page copy and the recording for where the items are before trying again")
        return {**result, "note": " ".join(x for x in (result.get("note"), note) if x)}
    sid = step.get("id") or sname
    if _kept_grant(workflow, "list-count", sid):
        return result

    said_no = next((g for g in reversed(workflow.get("approval_grants") or [])
                    if g.get("kind") == "list-count-no" and g.get("step") == sid), None)
    if said_no and said_no.get("count") == found:
        note = (f"it still found {found} {what}, the count the person already said is too "
                "low: the step is not reading more of the list yet - work out how the page loads more before trying again")
        return {**result, "note": " ".join(x for x in (result.get("note"), note) if x)}
    question = (f'"{sname}" found {found} {what}, fewer than the {limit} it was allowed '
                "to collect, so the page showed no more than that. Is that about how many you expect?")
    options = [COUNT_YES, COUNT_NO]
    iid, entry, answer = _raise_card(
        workflow, "ask", {"question": question, "options": options, "secret": False,
                          "secret_name": "", "upload": False, "folder": False},
        "ask", {"question": question, "options": options}, "ask")
    if answer is None:
        turnstate.of(workflow).ask_open = True
        return {**result, "note": " ".join(x for x in (result.get("note"),
                "the person is asked whether this count is right") if x)}
    text = str(answer.get("text") if isinstance(answer, dict) else answer or "").strip()
    if text == COUNT_YES:
        workflow.setdefault("approval_grants", []).append(
            {"kind": "list-count", "step": sid, "count": found, "ts": _time.time()})
        steps.save(workflow)
        said = f"the person confirmed {found} {what} is about what they expect"
    elif text == COUNT_NO:
        workflow.setdefault("approval_grants", []).append(
            {"kind": "list-count-no", "step": sid, "count": found, "ts": _time.time()})
        steps.save(workflow)
        said = (f"the person expects more than {found} {what}: the page did not load more "
                "while this step ran - work out how it loads more (scroll the list as a person does, and look in the recording for the request the page makes for its next batch) and try the step again")
    else:
        said = f"the person answered about the count of {found} {what}: {text}"
    return {**result, "note": " ".join(x for x in (result.get("note"), said) if x)}

# Raises an approval card and waits; a decline is recorded and respected, never retried around
def _kept_grant(workflow: dict, holds: str, key: str) -> dict | None:
    field = "domain" if holds == "window" else "step"
    return next((g for g in workflow.get("approval_grants") or []
                 if key and g.get("kind") == holds and g.get(field) == key), None)

def _approve(workflow: dict, emit, title: str, detail: str, scope: str,
             step: str = "", step_id: str = "", repeat: bool = False,
             code_key: str = "", holds: str = "", domain: str = ""):
    grants = [] if holds == "person" else (workflow.get("approval_grants") or [])
    for g in list(grants):
        if g.get("kind") == "window":
            if holds == "window" and domain and g.get("domain") == domain and g.get("always"):
                return "allow"
            continue

        if str(g.get("kind") or "").startswith("list-count"):
            continue

        if (g.get("kind") == "ready") != (holds == "ready"):
            continue
        if (step and g.get("step") == step) \
                or (not step and title and g.get("title") == title):
            if g.get("kind") == "send":
                if holds != "send":
                    continue

                if g.get("always"):
                    return "allow"
                if repeat or (code_key and g.get("code_key") and g["code_key"] != code_key):
                    continue
                return "allow"
            grants.remove(g)
            steps.save(workflow)
            return "allow"

    iid, entry, got = _raise_card(
        workflow, "approval",
        {"title": title, "detail": detail, "scope": scope,
         **({"step": step} if step else {}),
         **({"step_id": step_id} if step_id else {}),
         **({"holds": holds} if holds else {}),
         **({"domain": domain} if domain else {})},
        "approval", {"title": title, "detail": detail, "scope": scope}, "int")

    cid = str(got.get("cid") or "") if isinstance(got, dict) else ""
    decision = str(got.get("text") or "") if isinstance(got, dict) else got
    if decision not in ("allow", "always", "deny"):
        decision = None

    if decision is not None:
        from agent import reply as _reply
        _reply.settle(workflow, entry, {"text": decision, "via": "click", "cid": cid})
        steps.save(workflow)
    emit({"type": "approval-done", "id": iid, "decision": decision or "parked"})
    return decision

NO_IS_FINE = ("Saying no is fine too: the step is still built either way, just not tested right now, so your own first run tests it, and anything off gets fixed there.")

NO_MEANS_UNSEEN = ("If you say no, I'll work out this step without looking at the page, and your first run will show whether it works.")

WINDOW_NEEDS_YOU = ("You may need to do something in the window, such as logging in. It opens when you say yes, so say yes when you're ready.")

WINDOW_SENDS = ("This step sends something on the site, so I'll ask before each window it opens.")

NO_MEANS_EARLIER_SEND = ("Saying no means the earlier send stays the step's test: the changed code goes untested until your first real run, which sends it behind its own approval.")

# A try of a step the approved change does not name widens the change, shows the card again and runs on the click
def _widen_change(workflow: dict, emit, fn, args: dict, result: dict) -> dict:
    steps.widen_change(workflow, str(result["widen_change"]), str(args.get("reason") or ""))
    steps.save(workflow)
    card = _opening_card_owned(workflow, emit)
    if card.get("plan_shown") == "approved":
        from agent import chatlog
        chatlog.append(workflow["id"], "change-widened", {"step": result["widen_change"]})
        return fn(workflow, **args)
    if card.get("plan_shown") == "waiting":
        return {"ok": True, "parked": True}
    return {"ok": False, "error": "the user replied on the card instead of approving the wider change",
            **{k: v for k, v in card.items() if k != "plan_shown"}}

# The four things a cell may need the user for while it runs: a browser window, a real send, a folder, an outside address - one card each, asked one way
def _cell_approvals(workflow: dict, emit, fn, args: dict, result: dict) -> dict:
    asked: set = set()

    args = dict(args)
    while True:
        if not isinstance(result, dict):
            return result
        card = (_browser_card(workflow, fn, args, result) or _send_card(workflow, fn, args, result)
                or _folder_card(workflow, fn, args, result) or _domain_card(workflow, fn, args, result))
        if card is None:
            return result
        if card.get("refused"):
            return {"ok": False, "error": card["refused"]}

        if card["title"] in asked:
            return result
        asked.add(card["title"])
        decision = _approve(workflow, emit, card["title"], card["detail"], card["scope"],
                            step=card["step"], step_id=card["step_id"],
                            repeat=bool(card.get("repeat")),
                            code_key=str(card.get("code_key") or ""),
                            holds=str(card.get("holds") or ""),
                            domain=str(card.get("domain") or ""))
        if decision in ("allow", "always"):
            if card.get("holds") == "window":
                args["browser_ok"] = True
            elif card.get("holds") == "send":
                args["send_ok"] = True
            if card.get("holds") == "send":
                grants = workflow.setdefault("approval_grants", [])
                kept = _kept_grant(workflow, "send", card["step"])
                for_good = decision == "always" or bool(kept and kept.get("always"))
                grants[:] = [g for g in grants
                             if not (g.get("kind") == "send" and g.get("step") == card["step"])]
                grants.append({"step": card["step"], "kind": "send",
                               "title": card["title"], "code_key": str(card.get("code_key") or ""),
                               "always": for_good, "ts": _time.time()})
                steps.save(workflow)
            elif card.get("holds") == "window" and decision == "always" \
                    and not _kept_grant(workflow, "window", card.get("domain") or ""):
                workflow.setdefault("approval_grants", []).append(
                    {"kind": "window", "domain": card["domain"], "always": True,
                     "title": card["title"], "ts": _time.time()})
                steps.save(workflow)
            result = card["on_yes"]()
            continue
        if decision is None:
            turnstate.of(workflow).ask_open = True
            return {"ok": True, "parked": True}
        return card["declined"]
    return result

# A card for one browser window: the site, the step and why, with a yes kept for the site when the step only reads
def _browser_card(workflow: dict, fn, args: dict, result: dict):
    cell = result.get("needs_browser_ok")
    if not cell:
        return None
    if not args.get("ask_again") and steps.is_declined(workflow, cell):
        return {"refused": "the user already said no to opening the browser "
                           f"for \"{cell}\" - that answer stands; do NOT ask "
                           "again. Work from a recorded run (\"$recorded\") or move on and build: the step counts as done."}
    why = str(args.get("reason") or "").strip()
    again_why = str(args.get("ask_again") or "").strip()
    domain = str(result.get("domain") or "").strip()
    sends = bool(result.get("sends"))

    person = bool(result.get("needs_person"))

    def on_yes():
        r = fn(workflow, **{**args, "browser_ok": True})
        if isinstance(r, dict) and not r.get("ok") and r.get("window_opened") is False:
            grants = workflow.setdefault("approval_grants", [])
            grants.append({"step": steps.humanise_name(str(cell)),
                           "title": "", "ts": _time.time()})
            steps.save(workflow)
        return r
    return {
        "title": (f'Open {domain} in a browser window for "{cell}"?' if domain
                  else f'Open a browser window for "{cell}"?'),
        "detail": ((f"You said no to this earlier. {again_why} " if again_why else "")
                   + why).strip(),
        "scope": ((WINDOW_NEEDS_YOU + " " if person else "")
                  + (WINDOW_SENDS + " " if sends else "") + NO_MEANS_UNSEEN),
        "step": steps.humanise_name(str(cell)),
        "step_id": steps.step_id_for(workflow, cell),
        **({"holds": "ready"} if person
           else {"holds": "window", "domain": domain} if domain and not sends else {}),
        "on_yes": on_yes,

        "declined": {"ok": False, "error":
                     "the user said no to opening the browser for "
                     f"\"{cell}\". That settles this step - do NOT try again "
                     "or rename the cell. Either work from a recorded run (a plain code cell chaining \"$recorded\"), or move on and build: the step counts as done and its first real run will be its first try."},
    }

def _send_card(workflow: dict, fn, args: dict, result: dict):
    send = result.get("needs_send_ok")
    if not send:
        return None
    if not args.get("ask_again") and steps.is_declined(workflow, send):
        return {"refused": f"the user already said no to sending \"{send}\" "
                           "for real - that answer stands; do NOT ask again. Move on and build: the step counts as done."}
    why = str(args.get("reason") or "").strip()
    impact = str(result.get("impact") or "").strip()
    repeat = bool(result.get("send_repeat"))
    again_why = str(args.get("ask_again") or "").strip()
    code_key = str(result.get("code_key") or "")

    kept = _kept_grant(workflow, "send", steps.humanise_name(str(send)))
    changed = bool(kept and not kept.get("always") and code_key
                   and kept.get("code_key") and kept["code_key"] != code_key)
    why_again = ("You said yes to this before, and the step's code has changed since, so it asks once more. " if changed and not repeat
                 else "You said yes to this before, and it went through; sending it again sends it a second time. " if repeat and kept else "")
    return {
        "title": f'Send "{send}" for real again?' if repeat else f'Send "{send}" for real?',
        "detail": (f"You said no to this earlier. {again_why} " if again_why else "")
                  + why_again
                  + (f"{why} " if why else "") + (f"({impact})" if impact else ""),
        "scope": ("It has been sent once already, and a yes sends it again, once. " + NO_MEANS_EARLIER_SEND if repeat
                  else "I'll only send this once. " + NO_IS_FINE),
        "step": steps.humanise_name(str(send)),
        "step_id": steps.step_id_for(workflow, send),
        "holds": "send", "repeat": repeat, "code_key": code_key,
        "on_yes": lambda: fn(workflow, **{**args, "send_ok": True}),
        "declined": {"ok": False, "error":
                     f"the user said no to sending \"{send}\" for real. That "
                     "settles this step - do NOT try again or rename the cell. Move on: the step counts as done, build_workflow accepts it, and its first real run will be its first try."},
    }

def _folder_card(workflow: dict, fn, args: dict, result: dict):
    if steps.egress_block_domain(result.get("error")):
        return None
    blocked_path = steps.path_block_path(result.get("error"))
    if not blocked_path:
        return None
    folder = steps.folder_of(blocked_path)
    why = str(args.get("reason") or "").strip()

    def on_yes():
        wl = set(workflow.get("path_allowlist") or [])
        wl.add(folder)
        workflow["path_allowlist"] = sorted(wl)
        steps.save(workflow)
        return fn(workflow, **args)
    return {
        "title": f"Let this workflow read files in {folder}",
        "detail": why or "Needed to try this step for real.",
        "scope": "Allowed for this workflow from now on.",
        "step": steps.humanise_name(str(args.get("name") or "")),
        "step_id": steps.step_id_for(workflow, args.get("name") or ""),
        "on_yes": on_yes,
        "declined": result,
    }

def _domain_card(workflow: dict, fn, args: dict, result: dict):
    domain = steps.egress_block_domain(result.get("error"))
    if not domain:
        return None
    why = str(args.get("reason") or "").strip()
    siblings = _plan_domains_for(workflow, args.get("name"))
    extra = sorted(d for d in siblings if d != domain
                   and d not in (workflow.get("egress_allowlist") or []))
    detail = why or "Needed to try this step for real."
    if extra:
        detail += (" One yes also covers the step's other declared addresses: " + ", ".join(extra) + ".")

    def on_yes():
        wl = set(workflow.get("egress_allowlist") or [])
        wl.add(domain)
        wl.update(extra)
        workflow["egress_allowlist"] = sorted(wl)
        steps.save(workflow)
        return fn(workflow, **args)
    return {
        "title": f"Let this workflow connect to {domain}",
        "detail": detail,
        "scope": "Allowed for this workflow from now on.",
        "step": steps.humanise_name(str(args.get("name") or "")),
        "step_id": steps.step_id_for(workflow, args.get("name") or ""),
        "on_yes": on_yes,
        "declined": result,
    }

# Domains declared by the same-named plan step, folded into one approval
def _plan_domains_for(workflow: dict, cell_name) -> list[str]:
    cname = steps.humanise_name(str(cell_name or ""))
    if not cname:
        return []
    for step in (workflow.get("plan") or {}).get("nodes") or []:
        if steps.humanise_name(str(step.get("name") or "")) == cname:
            return [str(d).strip().lower() for d in step.get("domains") or []
                    if str(d).strip()]
    return []

_ARG_TYPES: dict[str, dict] = {}

# Type lookup for coercing string-shaped arguments, derived from the same schemas the model sees
def _arg_types() -> dict:
    if "tools" not in _ARG_TYPES:
        _ARG_TYPES["tools"] = {
            s["name"]: {k: (v.get("type") or "") for k, v in
                        ((s.get("input_schema") or {}).get("properties")
                         or {}).items()}
            for s in schemas()}
    return _ARG_TYPES["tools"]

# A list or object argument that arrives as JSON text is parsed once here, structurally
def _coerce_args(name: str, args: dict) -> dict:
    types = _arg_types().get(name)
    if not types or not isinstance(args, dict):
        return args
    out = args
    for k, v in args.items():
        want = types.get(k)
        if want not in ("array", "object") or not isinstance(v, str):
            continue
        txt = v.strip()
        if not ((want == "array" and txt.startswith("["))
                or (want == "object" and txt.startswith("{"))):
            continue
        try:
            parsed = json.loads(txt)
        except ValueError:
            continue
        if (want == "array" and isinstance(parsed, list)) \
                or (want == "object" and isinstance(parsed, dict)):
            if out is args:
                out = dict(args)
            out[k] = parsed
    return out

def _argument_error(name, args):
    if not isinstance(args, dict):
        return "input must be an object"
    spec = next((s["input_schema"] for s in schemas() if s["name"] == name), None)
    if spec is None:
        return f"unknown action {name!r}"
    props = spec.get("properties") or {}
    unknown = sorted(set(args) - set(props))
    if unknown:
        return "unknown argument(s): " + ", ".join(unknown)
    missing = [k for k in spec.get("required", []) if k not in args]
    if missing:
        return "missing argument(s): " + ", ".join(missing)
    types = {"string": str, "object": dict, "array": list, "boolean": bool,
             "integer": int, "number": (int, float)}
    for k, value in args.items():
        kind = props[k].get("type")
        expected = types.get(kind) if isinstance(kind, str) else None
        if expected and (not isinstance(value, expected) or
                         (kind in ("integer", "number") and isinstance(value, bool))):
            return f"{k} must be {kind}"
    if name == "run_cell":
        if "cells" in args:
            if len(args) != 1 or not args["cells"]:
                return "use cells=[...] alone, or the single name/code form, not both"
            for i, cell in enumerate(args["cells"]):
                if not isinstance(cell, dict) or "cells" in cell:
                    return f"cells[{i}] must be a single cell object"
                error = _argument_error("run_cell", cell)
                if error:
                    return f"cells[{i}]: {error}"
        elif not args.get("name", "").strip():
            return "a single cell requires a name"
    return ""

# Runs one tool call: refreshes the workflow from disk around it and hands the model anything the user said meanwhile
def execute(workflow: dict, action: dict, emit, engine: str = "") -> dict:
    name, args = action.get("name") or "", action.get("input", {})
    args = _coerce_args(name, args)

    from storage import store as _store, environments as _envs
    _store.refresh_from_disk(workflow)
    notes = [_envs.changes_since(workflow)]
    argument_error = _argument_error(name, args)
    from agent import operating_context
    phase_error = operating_context.refusal(workflow, name) if not argument_error else ""
    if action.get("refused"):
        out = {"action": name, "ok": False, "error": action["refused"], "failure_stage": "lifecycle"}
    elif argument_error:
        out = {"action": name, "ok": False, "error": "bad arguments: " + argument_error, "failure_stage": "schema"}
    elif phase_error:
        out = {"action": name, "ok": False, "error": phase_error, "failure_stage": "lifecycle"}
    else:
        out = _execute(workflow, name, args, action, emit)
    if isinstance(out, dict) and not _result_ok(out):
        out.setdefault("failure_stage", "tool")

    _store.refresh_from_disk(workflow)
    notes.append(_envs.changes_since(workflow))
    if isinstance(out, dict):
        out["operating_state"] = operating_context.availability(workflow)
        delivered = args.get("id") or "" if name == "load_skill" and out.get("ok") else ""
        guidance, _ = operating_context.supply(workflow, already_delivered=delivered)
        if guidance:
            out["operating_guidance"] = guidance
    env_note = " ".join(n for n in notes if n)
    if env_note and isinstance(out, dict):
        out = {**out, "environment_update": env_note}

    import turns as _turns
    said = _turns.take_interjections(workflow.get("id") or "")
    if said and isinstance(out, dict):
        out = {**out, "user_said_meanwhile": (
            "the user sent this while you were working (it is already in the chat - read it now; if it changes what you are doing, change course, and answer it in your reply): "
            + " | ".join(f'"{t}"' for t in said))}

    from agent import previews
    from agent import trail as _trail
    key = ""
    if isinstance(out, dict):
        key = "data" if "data" in out else ("error" if "error" in out else "")
        if key:
            out = {**out, "size": previews.size(out[key])}
    rid = _trail.tool(workflow.get("id") or "", name, args, out)
    if isinstance(out, dict):
        if rid is not None:
            out = {**out, "result_id": rid}
        if key:
            out = {**out, key: previews.guard(out[key], engine)}

    if emit:
        emit({"type": "thinking"})
    return out

def _execute(workflow: dict, name: str, args: dict, action: dict,
             emit) -> dict:
    over = steps.turn_over_error(workflow, name)
    if over:
        return {"action": name, "ok": False, "error": over, "failure_stage": "lifecycle"}
    if name == "load_skill":
        r = _load_skill(workflow, args.get("id") or "")
        ok = not r.get("error")
        return {"action": name, "ok": ok,
                **({"data": r} if ok else {"error": r["error"]})}
    if name == "ask_user":
        r = _ask_user_owned(workflow, emit, args)
        ok = not r.get("error")
        return {"action": name, "ok": ok,
                **({"data": r} if ok else {"error": r["error"]})}
    fn = _fns().get(name)
    if fn is None:
        return {"action": name, "ok": False,
                "error": f"unknown action {name!r}"}
    cellish = name in ("run_cell", "run_ai_step")

    cell_name = (steps.step_name_for(workflow, str(args.get("name") or ""))
                 if cellish and str(args.get("name") or "").strip() else "")
    if cellish and emit and cell_name:
        emit({"type": "cell", "name": cell_name, "status": "working"})
    if name == "run_cell" and emit:
        from agent import orchestrator as _mai
        base = _mai._describe_tool(workflow, "mcp__cryogram__run_cell", args)

        def _progress(note, _b=base, _cell=cell_name):
            emit({"type": "progress", "text": f"{_b} - {note}"})
            try:
                from agent import chatlog
                chatlog.append(workflow["id"], "cell-progress", {"cell": _cell, "note": str(note)})
            except Exception:
                pass
        steps.set_cell_progress(workflow["id"], _progress)
    try:
        result = fn(workflow, **args)
    except Exception as e:
        steps.set_cell_progress(workflow["id"], None)
        return {"action": name, "ok": False, "error": f"failed: {e}", "failure_stage": "execution"}
    if cellish and emit and isinstance(result, dict) and result.get("widen_change"):
        result = _widen_change(workflow, emit, fn, args, result)

    if name == "run_cell" and emit and isinstance(result, dict):
        if isinstance(result.get("results"), list) and isinstance(args.get("cells"), list):
            fixed = []
            for spec, r in zip(args["cells"], result["results"]):
                if isinstance(r, dict) and isinstance(spec, dict):
                    r = _cell_approvals(workflow, emit, fn, dict(spec), r)
                    r = _list_count_check(workflow, dict(spec), r)
                fixed.append(r)
            result = {**result, "results": fixed,
                      "ok": all(isinstance(r, dict) and r.get("ok") for r in fixed)}
        else:
            result = _cell_approvals(workflow, emit, fn, args, result)
            result = _list_count_check(workflow, args, result)

    if name == "save_plan" and turnstate.of(workflow).take("opening_card_show_now") \
            and isinstance(result, dict) and _result_ok(result):
        card = _opening_card_owned(workflow, emit)

        result = {**result, **card,
                  "note": " ".join(n for n in (result.get("note"),
                                               card.get("note")) if n)}

    note = turnstate.of(workflow).take("note_to_show") if name == "save_plan" else None
    if note and isinstance(result, dict) and _result_ok(result):
        import turns as _turns
        pre = _turns.flush_text(workflow.get("id") or "")
        if pre:
            _transcript.append_message(workflow, "assistant", pre)
        _transcript.append_message(workflow, "assistant", steps.note_lines(note))

        from agent import loop as _loop
        _loop.landed(workflow.get("id") or "")
        steps.save(workflow)

    if name == "build_workflow" and isinstance(result, dict) \
            and _result_ok(result):
        req = turnstate.of(workflow).take("build_now")
        if req is not None:
            from agent import orchestrator as _mai
            built = _mai.build_now(workflow, emit,
                                   include_proposals=req.get("include_proposals"))
            if built.get("ok"):
                turnstate.of(workflow).built = built["content"]
                result = {"ok": True, "built": True, "note": built["content"]}
            else:
                found = [str(f) for f in (built.get("findings") or [])]
                extra = ""
                if found:
                    extra = " Specifically: " + "; ".join(found[:_BUILD_FINDINGS])
                    if len(found) > _BUILD_FINDINGS:
                        extra += f"; ...and {len(found) - _BUILD_FINDINGS} more"
                result = {"ok": False,
                          "error": built["content"] + "." + extra
                                   + " Fix that in the plan (save_plan, amend:true) and build again."}
    steps.set_cell_progress(workflow["id"], None)
    if cellish and emit and cell_name:
        emit({"type": "cell", "name": cell_name,
              "status": "ok" if isinstance(result, dict) and result.get("ok")
              else "error"})

        if isinstance(result, dict) and result.get("duplicate"):
            emit({"type": "tool",
                  "text": f'checked "{cell_name}" against its recorded run '
                          "- nothing re-ran"})

    if not isinstance(result, dict):
        result = {"result": result}
    ok = _result_ok(result)
    return {"action": name, "ok": ok,
            **({"data": result} if ok else {"error": _result_error(result)})}

# Serialised in full - the inline limit in execute is the only bound
def envelope_text(env: dict) -> str:
    return json.dumps(env, default=str)
