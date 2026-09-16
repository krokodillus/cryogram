# The browser step: works in a real Chrome window the app opens, records and closes for it
from __future__ import annotations

import re

TYPE = "browser"

MAY = {"code": True, "network": False, "app": False,
       "browser": True, "model": False, "process": True}

RECEIPT = "rehearsal"
EVIDENCE = "cell"

APP_WINDOW_OPTIONS = ("record_har_path", "record_har_content", "args",
                      "ignore_default_args", "channel", "headless",
                      "user_data_dir", "executable_path")
BAR = "full: one recorded window (HAR + whole page kept), never re-fired"

# The browser part of the one-kind-of-work check: a call over a plain HTTP client beside the window
def work_mismatch(name: str, sig: dict, services: list) -> list[str]:
    if not sig.get("http_client"):
        return []
    what = f"{', '.join(services)} " if services else ""
    return [f'the "{name}" step drives a browser window and also calls '
            f"{what}over a plain HTTP client - a browser step's "
            "traffic goes through the window; that other call is its own connector step"]

# Plan findings when a browser step does not say whether it sends, or which address its window opens
def declaration_gaps(name: str, node: dict) -> list[str]:
    out = []
    if not isinstance(node.get("read_only"), bool):
        out.append(f'the "{name}" browser step must say read_only: true when it '
                   "only reads the site, false when it sends anything there")
    url = str(node.get("url") or (node.get("config") or {}).get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        out.append(f'the "{name}" browser step must declare the url its window '
                   "opens (https://...) - without it the window opens on nothing")
    return out

# Plan findings for a step's own window settings: an object, and none of the app's own
def window_options_problems(name: str, opts) -> list[str]:
    if not isinstance(opts, dict):
        return [f'the "{name}" step\'s browser_options must be an object of '
                "window settings (viewport, locale, timezone_id and the like)"]
    own = sorted(k for k in opts if k in APP_WINDOW_OPTIONS)
    if own:
        return [f'the "{name}" step\'s browser_options set {", ".join(own)}, '
                "which the app sets itself (the profile, the recording and the launch flags) - leave them out"]
    return []

# Words a browser step adds to the search for what earlier builds found out
def learning_search_words(step: dict) -> str:
    return "browser chrome window login session"

CLOSED_TOKENS = ("TargetClosedError", "Target page, context or browser has been closed",
                 "Browser closed", "browser has been closed")

# A window closed while the step was working: a pause that offers to open it again, or None
def read_closed_window(node: dict, err_kind: str, head: str) -> dict | None:
    if not (err_kind == "BrowserClosedError"
            or any(t in head for t in CLOSED_TOKENS)):
        return None
    name = node.get("name") or "this step"
    return {"pause": {"reason": "browser-closed", "lines": [
        f'The browser window closed before "{name}" was finished. '
        "Nothing is lost: open it again and this step runs from the start, keeping whatever it had already got through."]}}

# A question to the person at the machine that nobody answered: a pause that keeps it, or None
def read_unanswered_question(node: dict, err_kind: str, err_text: str) -> dict | None:
    if err_kind != "NobodyAnsweredError":
        return None
    name = node.get("name") or "this step"
    asked = str(err_text or "").strip()
    return {"pause": {"reason": "not-confirmed", "lines": [
        f'The step "{name}" asked you to do something in the browser '
        "window and did not hear back"
        + (f": {asked}" if asked else "")
        + ". Run this step again when you are ready."]}}

PAGE_STATE_WORDS = re.compile(
    "(captcha|challenge|verif|log ?in|logged.?out|rate.?limit|whoa there|page state|title '|unusual)", re.I)

# The fix brief's hint to suspect the page before the code, when the failure names no page state
def fix_brief_hint(node: dict, verdict_text: str) -> str:
    if PAGE_STATE_WORDS.search(verdict_text):
        return ""
    return ("\n"
            "\n"
            "BROWSER FAILURE WITHOUT A NAMED PAGE STATE: this step drives a real browser and its error reports no page state. Suspect FIRST a captcha/verification page, a rate limit, or an expired login - not the code. Add the page-state gate (browser guide's page-state reference) so every failure reports the final URL, title and known markers; only then debug the code.")
