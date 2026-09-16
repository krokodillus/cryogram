# Builds the system prompt from the skill files; byte-identical across workflows so caching holds
from __future__ import annotations
from agent import skills

_TERMINAL_TAIL = (
    "# How a turn ends\n"
    "A response with tool calls keeps the turn going. A plain reply with no tool call ends it, and so do ask_user, build_workflow and resolve_issue. ask_user pauses the work and returns the user's answer when they give it; when it reports instead that they have not answered yet, the question is waiting as a card in the chat and their answer starts the next turn, so end the turn quietly without repeating it. A tool that reports a connection or browser approval as waiting has raised the same kind of card.")

def _action_index(workflow: dict) -> str:
    from storage import learnings as _learnings
    lines = []
    for e in skills.static_index():
        if e.get("kind") == "action":
            lines.append(f"- {e['id']}: {e.get('summary') or e.get('title')}")

    shown, dropped = _learnings.index_for(workflow)
    for e in shown:
        lines.append(f"- learning-{e['id']}: {e.get('title')}"
                     + (f" - {e['summary']}" if e.get("summary") else ""))
    if dropped:
        lines.append(f"- ...and {dropped} older learnings not listed here")
    if not lines:
        return ""
    return ("# Skills and learnings (load with the load_skill action BEFORE acting on their subject; each line says when it applies)\n"
            + "\n".join(lines))

# The system prompt, byte-identical across workflows so the model provider's caching holds
def system(workflow: dict) -> str:
    parts = [skills.policy_text().rstrip("\n"),
             _TERMINAL_TAIL, _action_index(workflow)]
    return "\n\n".join(p for p in parts if p) + "\n"
