# The connector step: reads from or writes to another app, over the network or on this computer
from __future__ import annotations

import re

TYPE = "connector"

MAY = {"code": True, "network": True, "app": True,
       "browser": False, "model": False, "process": False}

RECEIPT = "rehearsal"
EVIDENCE = "cell"
BAR = "full: authored tests (never re-fired) + rehearsal"

# The connector part of the one-kind-of-work check: a window it opens, or a second outside service
def work_mismatch(name: str, sig: dict, services: list) -> list[str]:
    if sig.get("browser"):
        return [f'the "{name}" step opens a real browser window but is '
                "typed \"connector\" - set its type to \"browser\": browser steps have their own rules (what a window may do, how a stop is read, what the run keeps)"]
    if len(services) > 1:
        return [f'the "{name}" step talks to {len(services)} outside '
                f"services ({', '.join(services)}) - one outside system "
                "per step: the second is its own connector step"]
    return []

# A note for the Builder Agent when a sender's outputs carry no text to explain a zero
def sends_without_saying_why(node: dict) -> list[str]:
    if node.get("read_only", False):
        return []
    outs = node.get("outputs") or []
    has_text = any(str(o.get("type") or "") in ("text", "longtext") for o in outs) \
        or any(o.get("item_fields") for o in outs)
    if not outs or has_text:
        return []
    return [f'"{node.get("name")}" acts on items but its outputs carry no text - '
            "a run where it does nothing (0 sent) has no way to say why and halts as no-effect. Add a per-item outcome list (item_fields with status: sent/skipped/failed and reason) or one text `note` output."]

# Whether the step leaves out where it connects, so its first live try would stop for permission cards
def lists_no_domains(node: dict) -> bool:
    return not (node.get("domains") or [])

SLOW_DOWN_WORDS = re.compile(
    "(429|too many requests|rate.?limit|slow down|whoa there|try again later|quota exceeded|temporarily blocked|unusual activity|captcha|challenge)",
    re.I)

NO_ANSWER_WORDS = re.compile(
    "(timed? ?out|timeout|connection reset|connection aborted|connection refused|broken pipe|temporarily unavailable|remote end closed|bad gateway|service unavailable|\\b50[234]\\b)", re.I)

# The service asked the step to slow down: a pause that keeps what it had done, or None
def read_slow_down(node: dict, head: str, said: str) -> dict | None:
    if not SLOW_DOWN_WORDS.search(head):
        return None
    if not node.get("read_only", False) and NO_ANSWER_WORDS.search(head):
        return None
    nm = node.get("name") or "this step"
    return {"pause": {"reason": "rate-limited", "lines": [
        f'The step "{nm}" was asked to slow down by the service{said}. '
        "What it had done is kept and will not be repeated - wait a few minutes and try again from this step. If this keeps happening, Fix it can slow the step down or do fewer per run."]}}

# The service never answered: a pause to run again, or for a send, a question whether it arrived
def read_no_answer(node: dict, head: str, said: str) -> dict | None:
    if not NO_ANSWER_WORDS.search(head):
        return None
    name = node.get("name") or "this step"
    if not node.get("read_only", False):
        return {"pause": {"reason": "send-unverified", "lines": [
            f'The step "{name}" sent its data, but the service never '
            "confirmed - so it may or may not have gone through. Check the outside system: if it arrived, continue from the next step; if it did not, run this step again." + said]}}
    return {"pause": {"reason": "service-unavailable", "lines": [
        f'The step "{name}" could not reach the service - it did not '
        "answer in time. Nothing was changed and nothing is lost: running again continues from this step." + said]}}
