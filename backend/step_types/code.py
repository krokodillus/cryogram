# The code step: works on its declared inputs and local files, and reaches nothing else
from __future__ import annotations

TYPE = "code"

MAY = {"code": True, "network": False, "app": False,
       "browser": False, "model": False, "process": False}

RECEIPT = "replay"
EVIDENCE = "cell"
BAR = "full: code + tests replayed through the executor"

# The code part of the one-kind-of-work check: a window or an outside system belongs to another type
def work_mismatch(name: str, sig: dict, services: list) -> list[str]:
    if sig.get("browser"):
        return [f'the "{name}" step opens a real browser window but is '
                "typed as plain code - make it a browser step"]
    if sig.get("http_client") or services:
        what = ", ".join(services) if services else "an outside system"
        return [f'the "{name}" step reaches {what} but is typed as '
                "plain code - make it a connector and say what it changes there (read-only if it only reads); outside access always goes through a connector or browser step"]
    return []

# A note for the Builder Agent when a step reads a file and never says it is the wrong kind
def parses_a_file_without_saying_so(node: dict) -> list[str]:
    reads_file = any(str(p.get("type") or "") in ("file", "filepath")
                     for p in node.get("inputs") or [])
    code = str(node.get("code") or "")
    if not (reads_file and code and "unexpected_input(" not in code):
        return []
    return [f'"{node.get("name")}" parses a file but never calls '
            "unexpected_input(...) - check the columns/keys it needs first and call it when they are missing (found=..., expected=...), so a wrong file stops the run with a plain message instead of a KeyError."]
