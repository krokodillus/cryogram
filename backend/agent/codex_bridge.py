# The seam between a Codex turn and the tool surface - per-turn tokens, one call at a time
from __future__ import annotations

import json
import secrets as _secrets
import threading
import time
from agent import steps
from agent import actions

_RELEASED_CAP = 50

_lock = threading.Lock()
_reg: dict[str, dict] = {}
_released: list[str] = []

# Mints the per-turn token the shim must present on every bridge call
def register(workflow: dict, emit, on_tool=None) -> str:
    token = _secrets.token_urlsafe(32)
    with _lock:
        _reg[token] = {"workflow": workflow, "emit": emit,
                       "on_tool": on_tool, "lock": threading.Lock(),
                       "calls": 0, "outcome": {}, "busy_tool": "",
                       "terminal": threading.Event(), "ts": time.time()}
    return token

def release(token: str) -> None:
    with _lock:
        if _reg.pop(token, None) is not None:
            _released.append(token)
            del _released[:-_RELEASED_CAP]

def entry(token: str) -> dict | None:
    with _lock:
        return _reg.get(token)

def status(token: str) -> str:
    with _lock:
        if token in _reg:
            return "live"
        return "released" if token in _released else "unknown"

# Runs one tool call for a Codex turn; one call at a time per turn, and a busy call is refused rather than queued
def execute(token: str, name: str, args: dict) -> dict:
    e = entry(token)
    if e is None:
        return {"envelope_text": json.dumps(
            {"ok": False, "error": "this turn is over - the tool call was not executed"})}
    workflow, emit = e["workflow"], e["emit"]
    if not e["lock"].acquire(blocking=False):
        _log(workflow, "codex-bridge-busy", {"tool": name,
                                            "running": e["busy_tool"]})
        return {"envelope_text": json.dumps(
            {"ok": False, "error": f"the previous tool "
             f"({e['busy_tool'] or 'unknown'}) is still finishing - wait for "
             f"its result before calling anything else"})}
    try:
        e["busy_tool"] = name

        over = steps.turn_over_error(workflow, name)
        if over:
            _log(workflow, "codex-refused-after-terminal", {"tool": name})
            return {"envelope_text": json.dumps({"ok": False, "error": over})}
        from agent import orchestrator
        full = f"mcp__cryogram__{name}"
        emit({"type": "tool",
              "text": orchestrator._describe_tool(workflow, full, args),
              "node": orchestrator._tool_node(workflow, full, args)})
        t0 = time.time()

        from agent import loop as _loop
        chars_in = _loop.measure_args(args)
        env = actions.execute(workflow, {"name": name, "input": args or {}},
                              emit, engine="codex")
        e["calls"] += 1
        e["outcome"]["last_tool"] = name
        e["outcome"]["last_ok"] = bool(env.get("ok"))
        e["outcome"]["last_error"] = str(env.get("error") or "")

        _loop.note_tool_bytes(e["outcome"], name, chars_in, env)
        b = (e["outcome"].get("tool_bytes") or {}).get(name) or (0, 0)
        _log(workflow, "tool-result",
             {"tool": name, "ok": bool(env.get("ok")),
              "error": str(env.get("error") or "")[:300],
              "seconds": round(time.time() - t0, 1), "engine": "codex",
              "chars_in": e["outcome"].get("last_chars_in", 0),
              "chars_out": e["outcome"].get("last_chars_out", 0),
              "tool_chars_total": b[0] + b[1]})
        if e["on_tool"]:
            try:
                e["on_tool"]()
            except Exception:
                pass
        from agent import loop as loop_mod
        if loop_mod._terminal_landed(workflow):
            e["terminal"].set()
        out: dict = {"envelope_text": actions.envelope_text(_strip_image(env))}
        img = (env.get("data") or {}).get("image") \
            if isinstance(env.get("data"), dict) else None
        if isinstance(img, dict) and img.get("data"):
            out["image"] = img
        return out
    finally:
        e["busy_tool"] = ""
        e["lock"].release()

        if entry(token) is None:
            _log(workflow, "codex-orphan-tool", {"tool": name})

# Images travel as real MCP image blocks; inline base64 would drown the text envelope
def _strip_image(env: dict) -> dict:
    data = env.get("data")
    if isinstance(data, dict) and isinstance(data.get("image"), dict):
        return {**env, "data": {k: v for k, v in data.items() if k != "image"}}
    return env

def _log(workflow: dict, etype: str, fields: dict) -> None:
    try:
        from agent import chatlog
        chatlog.append(workflow["id"], etype, fields)
    except Exception:
        pass
