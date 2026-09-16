# Drives one builder turn through the agent SDK with a restricted tool set - the model never gets shell or file access here
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Optional

import config
from storage import settings as _settings
from agent import steps
from agent import actions, prompt, transport
from agent import turnstate

# One log line per model round naming the tools it called
def _iter_log(workflow: dict, tr, round_no: int, tool_names: list,
              engine: str = "sdk", call: Optional[int] = None,
              ctx: Optional[int] = None) -> None:
    try:
        from agent import chatlog
        chatlog.append(workflow["id"], "iter", {
            "engine": engine, "transport": type(tr).__name__,
            "round": round_no, "tools": list(tool_names or []),
            **({"call": call} if call is not None else {}),
            **({"ctx": ctx} if ctx is not None else {})})
    except Exception:
        pass

# Counts model calls and context growth from the wire itself, so the numbers exist however a turn ends
class CallCounter:
    _IN_KEYS = ("input_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")

    def __init__(self, stats: dict):
        self.stats = stats
        stats.setdefault("model_calls", None)
        stats.setdefault("batched_calls", None)
        stats.setdefault("ctx_first", None)
        stats.setdefault("ctx_last", None)
        self.call = 0
        self.ctx: Optional[int] = None
        self._tools = 0

        self.wire_usage: dict = {}

    def call_started(self, usage: Optional[dict]) -> None:
        if usage is None:
            return
        self.call += 1
        self._tools = 0
        self.stats["model_calls"] = self.call
        self.stats["batched_calls"] = self.stats["batched_calls"] or 0
        self.ctx = sum(int(usage.get(k) or 0) for k in self._IN_KEYS)
        if self.stats["ctx_first"] is None:
            self.stats["ctx_first"] = self.ctx
        self.stats["ctx_last"] = self.ctx
        for k in self._IN_KEYS:
            if usage.get(k) is not None:
                self.wire_usage[k] = self.wire_usage.get(k, 0) + int(usage[k])

    # A message_delta closes the call in flight and carries its output tokens
    def call_ended(self, usage: Optional[dict]) -> None:
        if usage is None:
            return
        out = usage.get("output_tokens")
        if out is not None:
            self.wire_usage["output_tokens"] = \
                self.wire_usage.get("output_tokens", 0) + int(out)

    # Tool blocks arrive one per event, so a call counts as batched exactly once
    def tools_called(self, names: list) -> None:
        hist = self.stats.setdefault("tool_calls", {})
        for n in names:
            hist[n] = hist.get(n, 0) + 1
        before, self._tools = self._tools, self._tools + len(names)
        if before <= 1 < self._tools:
            self.stats["batched_calls"] = \
                (self.stats.get("batched_calls") or 0) + 1

# Argument size measured before the tool runs, so the gauge never counts what the harness filled in afterwards
def measure_args(args) -> int:
    try:
        return len(json.dumps(args or {}, default=str))
    except Exception:
        return 0

# The per-turn ledger of what each tool call weighs, shared by both engines
def note_tool_bytes(bucket: dict, name: str, chars_in: int, env) -> None:
    try:
        c_out = len(actions.envelope_text(env))
    except Exception:
        return
    c_in = int(chars_in or 0)
    bucket["last_chars_in"], bucket["last_chars_out"] = c_in, c_out
    per = bucket.setdefault("tool_bytes", {})
    was = per.get(name) or (0, 0)
    per[name] = (was[0] + c_in, was[1] + c_out)

# A missing usage report is recorded as a gap, never silently dropped from the ledger
def _usage_missing_log(workflow: dict, kind: str, engine: str = "sdk") -> None:
    try:
        from agent import chatlog
        chatlog.append(workflow["id"], "usage-missing",
                       {"turn_kind": kind, "engine": engine})
    except Exception:
        pass

def _usage_log(workflow: dict, usage: dict, kind: str,
               engine: str = "sdk") -> None:
    try:
        from agent import chatlog
        if usage:
            chatlog.append(workflow["id"], "usage",
                           {"usage": usage, "engine": engine,
                            "turn_kind": kind})

            from storage import db, settings
            db.ai_usage_add(
                provider_id="builder",
                model=(settings.get().get("master_ai") or {}).get("model", ""),
                workflow_id=workflow["id"], run_id="", node_id="",
                tokens_in=sum(int(usage.get(k) or 0) for k in
                              ("input_tokens", "cache_creation_input_tokens",
                               "cache_read_input_tokens")),
                tokens_out=int(usage.get("output_tokens") or 0),
                source="builder")
    except Exception:
        pass

_HEAVIEST_TOOLS = 5

def _time_now() -> float:
    import time
    return time.time()

# One line per turn: rounds, wall time and how much of it was real work
def _turn_summary_log(workflow: dict, kind: str, stats: dict, *,
                      asked: bool, built: bool, stopped: bool) -> None:
    t0 = stats.get("t0", _time_now())
    try:
        from agent import cells
        worked = round(sum(r.get("duration") or 0 for r in cells._load_raw(workflow["id"])
                           if (r.get("ts") or 0) >= t0), 1)
    except Exception:
        worked = None
    try:
        from agent import chatlog
        by_bytes = sorted((stats.get("tool_bytes") or {}).items(),
                          key=lambda kv: -(kv[1][0] + kv[1][1]))
        chatlog.append(workflow["id"], "turn-summary", {
            "turn_kind": kind, "rounds": stats.get("rounds", 0),
            "wall_seconds": round(_time_now() - t0, 1),
            "cell_seconds": worked,

            "tools": dict(stats.get("tool_calls") or {}),
            "model_calls": stats.get("model_calls"),
            "batched_calls": stats.get("batched_calls"),

            "ctx_first": stats.get("ctx_first"),
            "ctx_last": stats.get("ctx_last"),

            "tool_chars_in": stats.get("tool_chars_in", 0),
            "tool_chars_out": stats.get("tool_chars_out", 0),
            "heaviest_tools": [{"tool": n, "in": v[0], "out": v[1]}
                               for n, v in by_bytes[:_HEAVIEST_TOOLS]],
            "asked": asked, "built": built, "stopped": stopped})
    except Exception:
        pass

# One builder turn, routed to whichever engine the configured credential uses
TURN_RETRY_WAITS = (10, 30, 60)

_TRANSIENT_TRANSPORT = ("overloaded", "rate limit", "rate_limit", "rate-limit",
                        "too many requests", "529", "503", "temporarily")

_USAGE_LIMIT_WORDS = ("usage limit", "hit your limit")

# A usage-limit error as the chat message that ends the turn, in the service's own words; "" for any other error
def usage_limit_message(text: str) -> str:
    text = str(text or "").strip()
    if not any(w in text.lower() for w in _USAGE_LIMIT_WORDS):
        return ""
    import re
    import time as _time
    m = re.search(r"\|\s*(\d{9,})\s*$", text)
    if m:
        at = _time.strftime("%H:%M", _time.localtime(int(m.group(1))))
        text = text[:m.start()].rstrip() + f" - it resets at {at}"
    text = re.sub(r"^(error:\s*)+", "", text, flags=re.I).strip()
    return f"The AI service stopped this turn: {text}" + ("" if text[-1:] in ".!?" else ".")

# Is this transport error the model service asking for time, rather than a setup problem?
def is_transient_transport(e: BaseException) -> bool:
    text = str(e).lower()
    return isinstance(e, transport.TransportError) \
        and any(w in text for w in _TRANSIENT_TRANSPORT)

def _wait_unless_stopped(pid: str, seconds: float) -> bool:
    import time as _t
    import turns as _turns
    end = _t.time() + seconds
    while _t.time() < end:
        if _turns.should_stop(pid):
            return False
        _t.sleep(min(0.5, max(0.0, end - _t.time())))
    return True

def run_turn(workflow: dict, user_message: str, emit=None,
             kind: str = "chat", tr=None, max_turns: int = 0) -> dict:
    emit = emit or (lambda ev: None)
    tr = tr or transport.for_settings()

    import turns as _turns
    from agent import trail as _trail
    pid = workflow.get("id") or ""
    base_id = _turns.current_turn_id(pid)
    for attempt in range(len(TURN_RETRY_WAITS) + 1):
        tid = base_id if attempt == 0 else f"{base_id or 'turn'}-retry{attempt}"
        _trail.begin(pid, tid, user_message, kind)
        try:
            if isinstance(tr, transport.CodexTransport):
                from agent import codex_engine
                return codex_engine.run_turn_codex(workflow, user_message, emit,
                                                   kind, tr, max_turns=max_turns)
            return _run_turn_sdk(workflow, user_message, emit, kind, tr,
                                 max_turns=max_turns)
        except transport.TransportError as e:
            if attempt >= len(TURN_RETRY_WAITS) or not is_transient_transport(e):
                raise
            wait = TURN_RETRY_WAITS[attempt]
            emit({"type": "tool",
                  "text": f"the AI service is busy - trying again in {wait}s"})
            if not _wait_unless_stopped(pid, wait):
                raise
        finally:
            _trail.end(pid)
    raise transport.TransportError("the builder could not start")

def _short_tool(name: str) -> str:
    return name.split("__")[-1]

# The loop stops only when a turn-ending tool actually succeeded; a failed one lets the model recover
def _terminal_landed(workflow: dict) -> bool:
    return bool(turnstate.of(workflow).ask_open
                or turnstate.of(workflow).built or turnstate.of(workflow).pending_fix)

EMPTY_TURN_MESSAGE = "Something went wrong and I couldn't finish that. Send it again."

# The one wording for a turn that could not finish, with the plain reason when there is one
def failed_ending(detail: str = "", streamed: str = "") -> str:
    detail = str(detail or "").strip()
    if not detail:
        line = EMPTY_TURN_MESSAGE
    elif detail[-1] in ".!?":
        line = detail
    else:
        line = f"I couldn't finish that: {detail}."
    streamed = str(streamed or "").strip()
    return f"{streamed}\n\n{line}" if streamed else line

# The one rule for what a turn's final text becomes, shared by both engines
def turn_ending(text_buf: list, *, built=None, fix=None, asked=False,
                stopped=False, interrupted=False, outcome=None) -> dict:
    outcome = outcome or {}
    streamed = "".join(text_buf).strip()
    if built:
        return {"content": "", "dropped": streamed}
    if fix:
        return {"content": "", "dropped": streamed}
    if asked:
        return {"content": "", "dropped": streamed}
    if stopped:
        if interrupted:
            return {"content": streamed, "dropped": ""}
        return {"content": (streamed + "\n"
                                       "\n"
                                       "Stopped - tell me how you'd like to continue.").strip(), "dropped": ""}
    if not streamed and outcome.get("last_tool") \
            and outcome.get("last_ok") is False:
        return {"content": EMPTY_TURN_MESSAGE, "dropped": ""}
    return {"content": streamed, "dropped": ""}

# Turns streamed model output into the status line and chat text, the same way on both engines
_NARRATORS: dict = {}

# Marks that the narration so far has been saved above a card, so the closing message starts after it
def landed(workflow_id: str) -> None:
    n = _NARRATORS.get(workflow_id)
    if n is not None:
        n.landed()

# The card was answered in the turn: the narrator shows and keeps the model's words again
def resumed(workflow_id: str) -> None:
    n = _NARRATORS.get(workflow_id)
    if n is not None:
        n.resumed()

class TurnNarrator:
    def __init__(self, emit, workflow_id: str = ""):
        self.emit = emit
        self.pid = workflow_id
        self.buf: list = []
        self._landed = 0

        self._muted = False
        self._started = False
        self._gap = False
        if workflow_id:
            _NARRATORS[workflow_id] = self
        emit({"type": "tool", "text": "getting started"})

    # The narration so far has been saved as a message above a card
    def landed(self) -> None:
        self._landed = len("".join(self.buf))
        self._muted = True

    # The card was answered inside the turn: the words after the answer are the reply to it, shown and kept again
    def resumed(self) -> None:
        self._muted = False

    # The text since the last card, which is what a plain ending says
    def tail(self) -> list:
        return ["".join(self.buf)[self._landed:]]

    def text(self, frag: str) -> None:
        if not frag:
            return
        if self.pid:
            from agent import trail as _trail
            _trail.text(self.pid, frag)
        if self._muted:
            return
        if not self._started:
            self._started = True
            self.emit({"type": "thinking"})
        if self._gap and "".join(self.buf).strip():
            self.emit({"type": "delta", "text": "\n\n"})
            self.buf.append("\n\n")
        self._gap = False
        self.emit({"type": "delta", "text": frag})
        self.buf.append(frag)

    # A tool call just split the narration - the next text starts a new box
    def tool_gap(self) -> None:
        self._gap = True

_THREADED_TOOLS = {"run_cell", "run_ai_step", "ask_user"}

# Registers the tool set with the SDK; slow tools run on a thread so Stop stays responsive
def _sdk_tool_server(workflow: dict, emit, outcome: dict):
    from claude_agent_sdk import create_sdk_mcp_server, tool
    from agent import orchestrator
    import asyncio as _aio

    tool_lock = _aio.Lock()

    def _make(name):
        full = f"mcp__cryogram__{name}"

        async def handler(args):
            args = args or {}

            over = steps.turn_over_error(workflow, name)
            if over:
                try:
                    from agent import chatlog
                    chatlog.append(workflow["id"], "sdk-refused-after-terminal",
                                   {"tool": name})
                except Exception:
                    pass
                return {"content": [{"type": "text", "text": json.dumps(
                    {"ok": False, "error": over})}]}
            emit({"type": "tool",
                  "text": orchestrator._describe_tool(workflow, full, args),
                  "node": orchestrator._tool_node(workflow, full, args)})

            chars_in = measure_args(args)
            if name in _THREADED_TOOLS:
                async with tool_lock:
                    env = await _aio.to_thread(
                        actions.execute, workflow,
                        {"name": name, "input": args}, emit, "claude")
            elif name in actions.READ_ONLY_TOOLS:
                env = await _aio.to_thread(
                    actions.execute, workflow,
                    {"name": name, "input": args}, emit, "claude")
            else:
                env = actions.execute(workflow, {"name": name, "input": args},
                                      emit, engine="claude")

            outcome["last_tool"] = name
            outcome["last_ok"] = bool(env.get("ok"))
            outcome["last_error"] = str(env.get("error") or "")

            note_tool_bytes(outcome, name, chars_in, env)
            try:
                from agent import chatlog
                b = (outcome.get("tool_bytes") or {}).get(name) or (0, 0)
                chatlog.append(workflow["id"], "tool-result",
                               {"tool": name, "ok": bool(env.get("ok")),
                                "error": str(env.get("error") or "")[:300],
                                "chars_in": outcome.get("last_chars_in", 0),
                                "chars_out": outcome.get("last_chars_out", 0),
                                "tool_chars_total": b[0] + b[1]})
            except Exception:
                pass

            img = (env.get("data") or {}).get("image") \
                if isinstance(env.get("data"), dict) else None
            if img and img.get("data"):
                slim = {**env, "data": {k: v for k, v in env["data"].items()
                                        if k != "image"}}
                return {"content": [
                    {"type": "image", "data": img["data"],
                     "mimeType": img.get("media_type") or "image/png"},
                    {"type": "text", "text": actions.envelope_text(slim)}]}
            return {"content": [{"type": "text",
                                 "text": actions.envelope_text(env)}]}
        return handler

    from claude_agent_sdk import ToolAnnotations
    from agent import previews
    backstop = previews.inline_limit("claude") * 2
    wrapped = [tool(s["name"], s["description"], s.get("input_schema") or {},
                    annotations=ToolAnnotations(maxResultSizeChars=backstop,
                                                readOnlyHint=bool(s.get("read_only"))))(
                   _make(s["name"]))
               for s in actions.schemas()]
    return create_sdk_mcp_server(name="cryogram", version="0.1.0", tools=wrapped)

# The SDK runs isolated: no filesystem settings, no memory files, a neutral working directory
def sdk_option_kwargs(system, server, allowed, builtins, model, env,
                      max_turns, cli_path: str = "", effort: str = "",
                      stderr=None) -> dict:
    return dict(
        system_prompt=system, mcp_servers={"cryogram": server},
        allowed_tools=allowed, tools=builtins, model=model,
        env=env, setting_sources=[], skills=[], strict_mcp_config=True,
        max_turns=max_turns or 200,
        include_partial_messages=True, cwd=str(sdk_cwd()),

        **({"cli_path": cli_path} if cli_path else {}),

        **({"effort": effort} if effort else {}),

        **({"stderr": stderr} if stderr else {}),
        extra_args={"no-session-persistence": None})

_turn_local = threading.local()

# Keeps the SDK's own log lines with the workflow's debug log
class _SdkLogHandler(logging.Handler):
    def emit(self, record):
        from agent import chatlog
        wid = getattr(_turn_local, "workflow_id", "") or "server"
        chatlog.append(wid, "sdk-log", {"level": record.levelname,
                                        "message": record.getMessage()})

# Sends the SDK's own log lines to the workflow's debug log instead of the terminal
def _route_sdk_log(workflow_id: str) -> None:
    log = logging.getLogger("claude_agent_sdk")
    if not any(isinstance(h, _SdkLogHandler) for h in log.handlers):
        log.addHandler(_SdkLogHandler())
        log.propagate = False
    _turn_local.workflow_id = workflow_id

# One plain sentence when the Claude Code program stops before the turn is over
def program_died_message(text: str) -> str:
    import re as _re
    m = _re.search(r"exit code:? (-?\d+)", text or "")
    if not m or "command failed" not in (text or "").lower():
        return ""
    return (f"Claude Code stopped before it finished (exit code {m.group(1)}). "
            "What it said on the way out is kept in the debug log.")

# The builder program a turn runs on: the one installed here, at or above the floor, or the one sentence that says what to do
def sdk_program() -> str:
    from agent import claude_code as _cc
    binp = _cc.claude_bin()
    if not binp:
        raise transport.TransportError(_cc.check_login()["message"])
    version = _cc.version_of(binp)
    if _cc.below(version, _cc.MIN_VERSION):
        raise transport.TransportError(
            _cc.too_old_sentence("Claude Code", version, _cc.MIN_VERSION))
    return binp

# The SDK runs from a neutral folder, so it can never read configuration or memory files from folders above an install
def sdk_cwd() -> "Path":
    import tempfile
    from pathlib import Path
    d = Path(tempfile.gettempdir()) / "cryogram-builder"
    d.mkdir(parents=True, exist_ok=True)
    return d

# The engine's own settings folder, inside the data folder, so nothing of the user's own command-line setup or history is read or written
def builder_config_dir() -> "Path":
    from pathlib import Path as _P
    d = _P(config.DATA_DIR) / "claude_home"
    d.mkdir(parents=True, exist_ok=True)
    return d

# Any variable in the app's own environment that would steer a Claude or Codex engine somewhere else is blanked before the app's own values go on top
STEERING_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "OPENAI_", "CODEX_")

_ENGINE_OWN = {"CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING"}

QUIET_SWITCHES = {"DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
                  "DISABLE_AUTOUPDATER": "1", "DISABLE_BUG_COMMAND": "1",
                  "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}

# The environment an engine subprocess gets on top of the server's: steering variables blanked, quiet switches on, then the caller's own values
def sealed_env(extra: dict) -> dict:
    env = {k: "" for k in os.environ
           if k.startswith(STEERING_PREFIXES) and k not in _ENGINE_OWN}
    env.update(QUIET_SWITCHES)
    env.update(extra)
    return env

NOT_SIGNED_IN = ("Claude Code is not signed in on this computer. In a terminal run `claude`, sign in, then send your message again.")

# Passes exactly one credential to the engine, or none: the Claude sign-in is read from Claude Code's own folder
def _sdk_env(tr) -> dict:
    auth = getattr(tr, "auth", "claude-subscription")
    if auth in ("codex-subscription", "codex-api-key"):
        raise transport.TransportError(
            "a Codex credential can never drive the Claude SDK engine")

    timeouts = {"MCP_TOOL_TIMEOUT": "1800000", "MCP_TIMEOUT": "1800000"}
    if auth == "api-key":
        env = sealed_env({**timeouts, "CLAUDE_CONFIG_DIR": str(builder_config_dir())})
        return {**env, "ANTHROPIC_API_KEY": tr.credential,
                "CLAUDE_CODE_OAUTH_TOKEN": ""}

    env = sealed_env(timeouts)
    env.pop("CLAUDE_CONFIG_DIR", None)
    return {**env, "ANTHROPIC_API_KEY": "", "CLAUDE_CODE_OAUTH_TOKEN": ""}

# The engine runs its own loop with this app's restricted tools; it stops when a card lands, and the events stream to the page
def _run_turn_sdk(workflow: dict, user_message: str, emit,
                  kind: str, tr, max_turns: int = 0) -> dict:
    import asyncio
    import turns
    try:
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions,
                                      ResultMessage, StreamEvent,
                                      ToolUseBlock, query)
    except Exception as e:
        raise transport.TransportError(
            f"the Claude Agent SDK is not installed: {e}") from e
    from agent import orchestrator

    system = prompt.system(workflow)
    schemas = actions.schemas()
    outcome: dict = {}
    server = _sdk_tool_server(workflow, emit, outcome)
    builtins, allowed = tr._sdk_config(schemas, actions.EXPLORATION_AIDS)
    env = _sdk_env(tr)
    from agent import chatlog
    _route_sdk_log(workflow["id"])
    options = ClaudeAgentOptions(**sdk_option_kwargs(
        system, server, allowed, builtins, tr.model or None, env, max_turns,
        cli_path=sdk_program(), effort=_settings.builder_effort(),
        stderr=lambda line: chatlog.append(workflow["id"], "claude-stderr",
                                           {"line": line})))

    from agent import context as _context
    prompt_body = _context.build(workflow, user_message, kind)[0]["content"]

    narrator = TurnNarrator(emit, workflow.get("id") or "")
    text_buf = narrator.buf
    err = {"msg": ""}
    stopped = {"v": False}
    stats = {"rounds": 0, "t0": _time_now(), "usage": None}
    counter = CallCounter(stats)

    async def _drive():
        async def _prompt():
            yield {"type": "user",
                   "message": {"role": "user", "content": prompt_body}}
        agen = query(prompt=_prompt(), options=options)
        it = agen.__aiter__()

        asyncio.get_running_loop().set_exception_handler(
            lambda _l, ctx: chatlog.append(workflow["id"], "sdk-task-error", {
                "message": str(ctx.get("message") or ""),
                "error": repr(ctx.get("exception"))}))
        try:
            while True:
                if turns.should_stop(workflow["id"]):
                    stopped["v"] = True
                    break
                nxt = asyncio.ensure_future(it.__anext__())
                while True:
                    done, _ = await asyncio.wait({nxt}, timeout=0.2)
                    if nxt in done:
                        break
                    if turns.should_stop(workflow["id"]):
                        nxt.cancel()
                        try:
                            await nxt
                        except (asyncio.CancelledError, Exception):
                            pass
                        stopped["v"] = True
                        return
                try:
                    msg = nxt.result()
                except StopAsyncIteration:
                    break
                if isinstance(msg, StreamEvent):
                    raw = msg.event or {}
                    counter.call_started(transport._call_start_usage(raw))
                    counter.call_ended(transport._call_end_usage(raw))
                    frag = transport._delta_text(raw)
                    if frag:
                        narrator.text(frag)
                elif isinstance(msg, AssistantMessage):
                    stats["rounds"] += 1
                    names = [_short_tool(b.name) for b in msg.content
                             if isinstance(b, ToolUseBlock)]
                    if names:
                        narrator.tool_gap()
                        counter.tools_called(names)
                        _iter_log(workflow, tr, stats["rounds"], names,
                                  call=counter.call or None, ctx=counter.ctx)
                elif isinstance(msg, ResultMessage):
                    u = getattr(msg, "usage", None)
                    if u:
                        stats["usage"] = u if isinstance(u, dict) else \
                            getattr(u, "__dict__", None)
                    cost = getattr(msg, "total_cost_usd", None)
                    if cost is not None and isinstance(stats["usage"], dict):
                        stats["usage"] = {**stats["usage"], "cost_usd": cost}
                    if getattr(msg, "is_error", False) or \
                            "error" in str(getattr(msg, "subtype", "")).lower():
                        err["msg"] = str(getattr(msg, "result", "")
                                         or "unknown error")[:300]
                    break

                if _terminal_landed(workflow):
                    break
        except BaseException:
            turns.request_stop(workflow["id"])
            raise
        finally:
            try:
                await agen.aclose()
            except Exception:
                pass

    try:
        asyncio.run(_drive())
    except Exception as e:
        low = str(e).lower()
        if "logged in" in low or "login" in low:
            raise transport.TransportError(NOT_SIGNED_IN) from e
        if usage_limit_message(str(e)):
            raise transport.TransportError(usage_limit_message(str(e))) from e
        if program_died_message(str(e)):
            raise transport.TransportError(program_died_message(str(e))) from e
        raise transport.TransportError(f"subscription request failed: {e}") from e
    if err["msg"]:
        low = err["msg"].lower()
        if "logged in" in low or "login" in low:
            raise transport.TransportError(NOT_SIGNED_IN)
        if usage_limit_message(err["msg"]):
            raise transport.TransportError(usage_limit_message(err["msg"]))
        raise transport.TransportError(f"the subscription builder failed: {err['msg']}")

    asked = bool(turnstate.of(workflow).take("ask_open"))
    built = turnstate.of(workflow).take("built")
    fix = turnstate.of(workflow).take("pending_fix")

    stats["tool_bytes"] = outcome.get("tool_bytes") or {}
    stats["tool_chars_in"] = sum(v[0] for v in stats["tool_bytes"].values())
    stats["tool_chars_out"] = sum(v[1] for v in stats["tool_bytes"].values())
    _turn_summary_log(workflow, kind, stats, asked=asked,
                      built=bool(built or fix), stopped=stopped["v"])

    usage = stats["usage"]
    if usage:
        usage = {**usage, "measured_from": "result"}
    elif counter.wire_usage:
        usage = {**counter.wire_usage, "measured_from": "wire"}
    if usage:
        _usage_log(workflow, usage, kind)
    else:
        _usage_missing_log(workflow, kind)

    import turns as _turns
    _NARRATORS.pop(workflow.get("id") or "", None)
    ending = turn_ending(narrator.tail(), built=built, fix=fix, asked=asked,
                         stopped=stopped["v"],
                         interrupted=bool(stopped["v"] and
                                          _turns.unread_interjections(
                                              workflow["id"])),
                         outcome=outcome)
    if ending["dropped"]:
        try:
            from agent import chatlog
            chatlog.append(workflow["id"], "ephemeral-tail",
                           {"content": ending["dropped"][:400]})
        except Exception:
            pass
    reply = {"content": ending["content"], "kind": "text",
             **({"_ask_open": True} if asked else {}),
             **({"stopped": True} if stopped["v"] else {})}
    if fix:
        reply["fix"] = fix
    return reply
