# Assembles each turn's input from the stored record: the workflow's state, the working trail of recent turns, and the message
from __future__ import annotations

from typing import Optional

import config
from agent import steps
from agent import turnstate
import extensions

# Replays a bounded window of the conversation; a trimmed window says so rather than posing as complete
def _recent_chat(workflow: dict, user_message: str, before=None,
                 first_message: str = "", trailing_message: bool = True) -> str:
    from agent import transcript
    built = (workflow.get("plan") or {}).get("status") == "built"
    all_lines = transcript.replay_lines(workflow)
    if before is not None:
        all_lines = [l for l in all_lines
                     if float(l["item"].get("at") or 0) < float(before)]
        if all_lines and all_lines[-1]["role"] == "user" \
                and all_lines[-1]["text"] == first_message:
            all_lines = all_lines[:-1]
    lines_in = list(all_lines)
    lines = []
    for l in lines_in:
        item = l["item"]
        text = l["text"]
        tag = ""
        if item.get("kind") == "request":
            tag = {"ask": " (question card)", "approval": " (approval card)",
                   "blueprint": " (blueprint card)"}.get(item.get("request"), "")
        shown = item.get("shown") if item.get("kind") == "answer" else None
        if isinstance(shown, dict) and not shown.get("typed") \
                and not shown.get("via_message") and not shown.get("build"):
            text += ("\n[the user filled in: "
                     + "; ".join(f"{k}: {v}" for k, v in shown.items()) + "]")
        lines.append(f"{l['role']}{tag}: {text}{_attached(item)}")
    latest = lines_in[-1] if lines_in else None
    if not trailing_message:
        if latest and latest["role"] == "user" and latest["text"] == user_message:
            lines.pop()
        return ("\n[what you and the user said earlier]\n" + "\n".join(lines) + "\n") if lines else ""
    if not (latest and latest["role"] == "user"
            and latest["text"] == user_message):
        lines.append(f"user: {user_message}")
    return "\n".join(lines)

# The files a message came with, named on its line, so "the attached one" means something to the model
def _attached(item: dict) -> str:
    names = []
    for f in (item or {}).get("files") or []:
        n = f.get("name") if isinstance(f, dict) else str(f)
        if n:
            names.append(str(n))
    if not names:
        return ""
    return ("\n[attached and stored as a sample: " + ", ".join(names)
            + " - read_sample reads it]")

def _driving_attached(workflow: dict, user_message: str) -> str:
    from agent import transcript
    for i in reversed(transcript.items(workflow)):
        if i.get("kind") == "message" and i.get("from") == "user":
            return _attached(i) if i.get("text", "") == user_message else ""
    return ""

# Lists recorded work by name so a turn never re-runs what it already proved - names only, never values
def _recorded_context(workflow: dict) -> str:
    from agent import cells
    try:
        inv = cells.inventory(workflow["id"])
    except Exception:
        return ""
    if not inv:
        return ""
    lines = []
    for c in inv:
        lines.append(f'- "{c["name"]}"'
                     + (" [AI]" if c["kind"] == "ai" else "")
                     + " produced: " + (", ".join(c["outputs"]) or "no outputs")
                     + (f' ({c["tries"]} tries)' if c["tries"] > 2 else ""))

        for w in c.get("windows") or []:
            lines.append(f'    window {w["n"]}'
                         + (f' ({w["reason"]})' if w["reason"] else "")
                         + ": kept " + ", ".join(w["captured"])
                         + f' - "$recorded:{w["captured"][0]}#{w["n"]}"')
    return ("\n"
            "[already tested] These steps have a recorded run - REUSE the exact name to build on one (a new name orphans its evidence), and chain these values with \"$recorded\". Their code and their recorded run are ALREADY HELD as each step's code and first test: save_plan leaves `code` and `tests` out for these and they are filled in for you. Every browser window keeps its own recording, numbered and listed with what it was opened for: chain \"$recorded:har#3\" for window 3's traffic, \"$recorded:har\" for the newest.\n"
            + "\n".join(lines) + "\n")

# The first plan step with no recorded try - what the agent is about to work on
def _next_step(workflow: dict) -> Optional[dict]:
    from agent import cells
    plan_steps = (workflow.get("plan") or {}).get("nodes") or []
    if not plan_steps:
        return None
    try:
        recorded = {c["name"] for c in cells.inventory(workflow["id"])}
    except Exception:
        recorded = set()
    for step in plan_steps:
        if steps.humanise_name(str(step.get("name") or "")) not in recorded:
            return step
    return None

# Notes relevant to the next piece of work are included in full; the rest stay loadable by name
def _learnings_context(workflow: dict, user_message: str) -> str:
    from storage import learnings
    bits = [str(user_message or "")[:600],
            str((workflow.get("intent") or {}).get("summary") or "")]
    step = _next_step(workflow)
    if step:
        bits += [str(step.get("name") or ""), str(step.get("type") or ""),
                 str(step.get("description") or "")]
        bits += [str(d) for d in (step.get("domains") or [])]
        import step_types
        words = step_types.function(step.get("type"), "learning_search_words")
        if words:
            bits.append(words(step))
    inline, _listed = learnings.select("\n".join(b for b in bits if b),
                                       workflow)
    if not inline:
        return ""
    head = ("\n"
            "[what earlier builds found out] Relevant to what you are about to do" + (f' ("{step.get("name")}")' if step else "")
            + ". These are FINDINGS from earlier work, not rules: trust what you can see live over anything here, and correct one by saving a learning with the same title.\n")
    return head + "\n".join(f"--- {e['title']}\n{e['content'].strip()}"
                            for e in inline) + "\n"

# The stored non-secret values, so the agent never asks for something the user already gave
def _stored_values(workflow: dict) -> str:
    lines = []
    for v in workflow.get("variables") or []:
        name, val = v.get("name"), v.get("value")
        if not name:
            continue
        if v.get("secret"):
            lines.append(f"- {name} (secret, stored)" if val else "")
            continue
        sval = str(val if val is not None else "").strip()
        if sval:
            lines.append(f"- {name} = {sval[:120]}"
                         + ("..." if len(sval) > 120 else ""))
    lines = [ln for ln in lines if ln]
    if not lines:
        return ""
    return ("\n"
            "[stored values] Already given - USE these, never re-ask (overwrite via declare_variables only after the user confirms a change):\n" + "\n".join(lines) + "\n")

# Recorded facts ride every turn so a proven discovery cannot scroll out of the bounded chat replay
def _intent_facts(workflow: dict) -> str:
    intent = workflow.get("intent") or {}
    parts = []
    if str(intent.get("summary") or "").strip():
        parts.append("\n[intent] " + str(intent["summary"]).strip() + "\n")
    facts = [str(f).strip() for f in intent.get("facts") or []
             if str(f).strip()]
    if facts:
        parts.append(
            "\n"
            "[recorded facts] Already established this session - build on these, never re-research or re-ask one (correct an outdated fact via save_intent remove_facts):\n"
            + "\n".join(f"- {f}" for f in facts) + "\n")
    return "".join(parts)

# The workflow models the user has set up, with their providers, so a named model or provider can be honoured
def _models_line(workflow: dict) -> str:
    import providers
    ready = [m for m in providers.node_models() if m.get("ready")]
    if not ready:
        return ""
    from storage import model_catalog
    names = ", ".join(
        f'{m["name"]} ({m["provider"]}, '
        f'{model_catalog.reads_phrase(providers.model_facts({"model": m["name"], "provider_id": m["provider_id"]}))})'
        for m in ready)
    default = next((m["provider"] for m in ready if m.get("default")), "")
    return ("\n"
            "[models] Workflow models the user has set up, each with the files it reads directly: " + names + ". "
            + (f"The default provider is {default}: " if default else "")
            + "a step's model is picked for you, so leave `model` blank; name a model or provider on a plan step only when the user asks for one, and then exactly the one they named.\n")

# One line about where a workflow will run, when there is anything to say
def _where_it_runs(workflow: dict) -> str:
    return extensions.first("context_line", workflow) or ""

# Assembles the whole turn input; fix turns skip the chat replay so a diagnosis starts from evidence
def build(workflow: dict, user_message: str, kind: str = "chat") -> list[dict]:
    from agent import orchestrator
    from storage import environments as _envs
    env = orchestrator._environment_context(workflow)

    turnstate.of(workflow).env_seen = _envs.snapshot(workflow)
    wf = (orchestrator._workflow_context(workflow) + _recorded_context(workflow)
          + _where_it_runs(workflow) + _models_line(workflow)
          + _stored_values(workflow) + _intent_facts(workflow)
          + _learnings_context(workflow, user_message))

    from agent import trail as _trail
    memory, since, first_msg = _trail.replay(workflow.get("id") or "")

    if kind == "chat":
        older = _recent_chat(workflow, user_message, before=since,
                             first_message=first_msg, trailing_message=False)
        body = (env + wf + older + memory + f"\nuser: {user_message}"
                + _driving_attached(workflow, user_message))
    else:
        body = env + wf + memory + user_message
    return [{"role": "user", "content": body}]
