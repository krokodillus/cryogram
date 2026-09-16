# Problem reports: shown to the user in full, sent only when they press send
from __future__ import annotations

import json
import platform
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Optional

import config

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = None

SEND_TIMEOUT = 15
ERROR_TEXT_CAP = 2000

def _known_secrets() -> list[str]:
    from storage import secrets_store
    out = list(secrets_store.all_values())
    return out

# Every string in every payload passes this scrub against the stored secret values before it can leave
def _scrub(value: Any, known: list[str]) -> Any:
    if isinstance(value, str):
        for s in known:
            if s in value:
                value = value.replace(s, "<secret>")
        if len(value) > ERROR_TEXT_CAP:
            value = value[:ERROR_TEXT_CAP] + "..."
        return value
    if isinstance(value, dict):
        return {k: _scrub(v, known) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, known) for v in value]
    return value

# Structure only: step names, types and wiring; values, prompts and code are not included
def _workflow_shape(workflow: dict) -> dict:
    by_id = {n.get("id"): n.get("name") for n in workflow.get("nodes", [])}
    return {
        "steps": [{"name": n.get("name"), "type": n.get("type"),
                   **({"read_only": n.get("read_only")}
                      if n.get("type") in ("connector", "browser") else {})}
                  for n in workflow.get("nodes", [])],
        "edges": [{"src": by_id.get(e.get("src"), e.get("src")),
                   "dst": by_id.get(e.get("dst"), e.get("dst"))}
                  for e in workflow.get("edges", [])],
    }

# Allowlist-built: chat, variable values, entry inputs and other steps' data can never enter a report
def issue_report(workflow: dict, ticket: dict) -> dict:
    from agent import evidence
    from runtime import corpus
    known = _known_secrets()
    node = next((n for n in workflow.get("nodes", [])
                 if n.get("id") == ticket.get("node_id")), {})
    cfg = node.get("config") or {}
    case = None
    if ticket.get("case_id"):
        for c in corpus.cases(workflow["id"], ticket.get("node_id") or "",
                              "failure"):
            if c.get("case_id") == ticket["case_id"]:
                case = c
                break
    out = {
        "kind": "issue",
        "app_version": config.VERSION,
        "channel": config.BUILD,
        "ts": time.time(),
        "reason": ticket.get("reason"),
        "failure_class": (case or {}).get("cls") or "",
        "error": (case or {}).get("cause") or "",
        "verdict": ticket.get("verdict") or {},
        "step": {
            "name": node.get("name") or "",
            "type": node.get("type") or "",
            "code": cfg.get("code") or "",
            "prompt": cfg.get("prompt") or "",
            "browser": node.get("type") == "browser",
            "read_only": bool(node.get("read_only")),
            "external_impact": node.get("external_impact") or "",
            "inputs": [{"name": p.get("name"), "type": p.get("type")}
                       for p in node.get("inputs") or []],
            "outputs": [{"name": p.get("name"), "type": p.get("type")}
                        for p in node.get("outputs") or []],
        },
        "output_shape": (evidence.structural_summary((case or {}).get("output"))
                         if (case or {}).get("output") is not None else None),
        "workflow": _workflow_shape(workflow),
    }
    return _scrub(out, known)

# Whether to offer a problem report at all: ask each time (default), or never
def report_mode() -> str:
    from storage import settings
    v = (settings.get().get("preferences") or {}).get("share_reports")
    return v if v in ("ask", "never") else "ask"

# The request headers a report goes out with: the browser's user agent and the operating system, read at the other end and never part of the report
def envelope(user_agent: str = "") -> dict:
    out = {"Content-Type": "application/json",
           "X-Cryogram-Os": platform.platform()}
    if user_agent:
        out["User-Agent"] = str(user_agent)
    return out

# One POST to the report endpoint; a failure is dropped and nothing retries or blocks
def _send(payload: dict, headers: Optional[dict] = None) -> bool:
    try:
        from storage import settings
        url = (settings.get().get("report") or {}).get("url", "").strip()
        if not url:
            return False

        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers=headers or envelope(), method="POST")
        with urllib.request.urlopen(req, timeout=SEND_TIMEOUT,
                                    context=_SSL_CTX) as r:
            return r.status < 300
    except Exception:
        return False

def send_issue(workflow: dict, ticket: dict, user_agent: str = "") -> bool:
    return _send(issue_report(workflow, ticket), envelope(user_agent))
