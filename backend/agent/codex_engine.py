# The alternative builder engine: a Codex subprocess drives the same tool surface through a local bridge
from __future__ import annotations

import hashlib
import json
import os
import shutil
from typing import Optional
import subprocess
import sys
import threading
import time
from pathlib import Path

import config
from agent import actions, codex_bridge, interactions, loop, previews, prompt, transport
from agent import turnstate

TURN_WALL_CAP = 1800

CODEX_TOOL_OUTPUT_TOKENS = previews.INLINE_CHARS["codex"] * 2 // 3
BOOT_TIMEOUT = 60

CODEX_QUIET_NOTE = 120
CONNECTED_TTL = 60
LOGIN_TIMEOUT = 30

_PROTOCOL = {
    "sandbox": "read-only",

    "approval_policies": ("on-request", "onRequest", "untrusted",
                          "unlessTrusted", "never"),
    "thread_start": "thread/start",
    "turn_start": "turn/start",
    "mcp_list": "mcpServerStatus/list",
    "tool_call": "item/tool/call",
    "ev_delta": "item/agentMessage/delta",
    "ev_item": "item/completed",
    "ev_usage": "thread/tokenUsage/updated",
    "ev_done": "turn/completed",
}

NOOP_COMMAND = (f'command = "{Path(sys.executable).as_posix()}", '
                'args = ["-c", "pass"]')
LOCKDOWN_CONFIG = (
    'mcp_servers.node_repl={' + NOOP_COMMAND + ', enabled = false}',

    "features.apps=false",

    *[f"features.{f}=false" for f in (
        "shell_tool", "shell_snapshot", "code_mode", "code_mode_host",
        "code_mode_only", "tool_search_always_defer_mcp_tools",
        "browser_use", "browser_use_external", "browser_use_full_cdp_access",
        "in_app_browser", "in_app_updates", "in_app_local_automation",
        "computer_use", "plugins", "remote_plugin", "plugin_sharing",
        "skill_search", "skill_mcp_dependency_install", "image_generation",
        "goals", "memories", "multi_agent", "hooks", "collab")],
    "tools.web_search=false",
    "include_plan_tool=false",
    "include_apply_patch_tool=false",
    "include_view_image_tool=false",

    'history.persistence="none"',
)
LOCKDOWN_PLUGINS = (
    "browser@openai-bundled", "visualize@openai-bundled",
    "sites@openai-bundled", "google-calendar@openai-curated",
    "slack@openai-curated", "documents@openai-primary-runtime",
    "pdf@openai-primary-runtime", "spreadsheets@openai-primary-runtime",
    "presentations@openai-primary-runtime",
    "template-creator@openai-primary-runtime",
)

AGENTS_PREAMBLE = """# Operating contract (read first, binding)

You are the builder assistant inside Cryogram, working for a NON-TECHNICAL
user. You act EXCLUSIVELY by calling the `cryogram` MCP tools. Hard rules:

- NEVER run shell commands, never create or edit files, never use any tool
  that is not from the `cryogram` server. This workspace is an empty
  sandbox; nothing you could do in it is real work. All real work - running
  code, saving plans, asking the user - happens ONLY through the cryogram
  tools, which act on the user's actual workflow.
- Your reply text goes to the user as chat. Speak plainly, no code dumps.
- The full working method follows below; the session's context (the user's
  request, prior chat, workflow state) arrives in each user message.

"""

DEFAULT_MODEL_SENTINEL = "codex-default"

# The page that installs Codex, the program every Codex route runs through, key or subscription
INSTALL_PAGE = "https://developers.openai.com/codex/cli"

def _codex_bin() -> str:
    return os.environ.get("CRYOGRAM_CODEX_BIN") or "codex"

def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")

CODEX_MIN_VERSION = "0.92.0"

_hint = {"ts": 0.0, "v": False, "program": {}}

# Which Codex the card shows: its path, its version and the floor
def program_hint(fresh: bool = False) -> dict:
    from agent import claude_code as _cc
    now = time.time()
    if fresh or now - _hint["ts"] > CONNECTED_TTL or not _hint["program"]:
        got = check_login()
        _hint["program"] = {"path": shutil.which(_codex_bin()) or _codex_bin(),
                            "version": _cc.version_number(str(got.get("version") or "")),
                            "too_old": bool(got.get("too_old")),
                            "message": got.get("message")}
    return {**_hint["program"], "floor": CODEX_MIN_VERSION}

# Checks only that a sign-in file exists; it is never read or opened
def connected_hint(fresh: bool = False) -> bool:
    now = time.time()
    if not fresh and now - _hint["ts"] < CONNECTED_TTL:
        return _hint["v"]
    _hint["ts"] = now
    try:
        _hint["v"] = (_codex_home() / "auth.json").exists()
    except OSError:
        _hint["v"] = False
    return _hint["v"]

# Whether the Codex account is signed in; the sign-in file itself is never read or copied
def check_login() -> dict:
    binp = _codex_bin()
    try:
        v = subprocess.run([binp, "--version"], capture_output=True,
                           text=True, timeout=LOGIN_TIMEOUT)
    except FileNotFoundError:
        return {"ok": False, "installed": False,
                "message": f"The Codex app isn't installed. Install it from "
                           f"{INSTALL_PAGE}, then sign in with `codex login`."}
    except Exception as e:
        return {"ok": False, "installed": False,
                "message": f"Couldn't run the Codex app: {e}"}
    version = (v.stdout or "").strip()
    from agent import claude_code as _cc
    number = _cc.version_number(version)
    if _cc.below(number, CODEX_MIN_VERSION):
        return {"ok": False, "installed": True, "version": version, "too_old": True,
                "message": _cc.too_old_sentence("Codex", number, CODEX_MIN_VERSION)}
    try:
        st = subprocess.run([binp, "login", "status"], capture_output=True,
                            text=True, timeout=LOGIN_TIMEOUT)
    except Exception as e:
        return {"ok": False, "installed": True, "version": version,
                "message": f"Couldn't check the sign-in: {e}"}
    if st.returncode == 0:
        return {"ok": True, "installed": True, "version": version,
                "message": "Signed in and ready."}
    return {"ok": False, "installed": True, "version": version,
            "message": "Not signed in yet. Run `codex login` in a terminal and finish the sign-in in your browser, then check again."}

# The ChatGPT-subscription flavour's own codex home: one link to the user's sign-in file, none of their configuration
def _chatgpt_home() -> Optional[Path]:
    home = config.DATA_DIR / "codex_home_chatgpt"
    home.mkdir(parents=True, exist_ok=True)
    link = home / "auth.json"
    target = _codex_home() / "auth.json"
    try:
        if link.is_symlink() and link.resolve() == target.resolve():
            return home
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(target, link)
    except OSError:
        return None
    return home

# Seeds an isolated codex home from the app's own secret store; the user's ~/.codex is never touched
def _api_key_home(credential: str) -> Path:
    home = config.DATA_DIR / "codex_home"
    home.mkdir(parents=True, exist_ok=True)
    marker = home / ".key_fingerprint"
    fp = hashlib.sha256(credential.encode()).hexdigest()[:16]
    have = marker.read_text().strip() if marker.exists() else ""
    if have != fp or not (home / "auth.json").exists():
        r = subprocess.run([_codex_bin(), "login", "--with-api-key"],
                           input=credential, text=True, capture_output=True,
                           timeout=LOGIN_TIMEOUT,
                           env={**os.environ,
                                **loop.sealed_env({"CODEX_HOME": str(home)})})
        if r.returncode != 0:
            raise transport.TransportError(
                "the Codex app refused the API key - check the key in Admin (the exact error is in the debug log)")
        marker.write_text(fp)
    return home

# Turns off the Codex CLI's own tools and history for the turn; only our tool set is reachable
def write_workflow_lockdown(cwd: Path) -> None:
    d = Path(cwd) / ".codex"
    d.mkdir(parents=True, exist_ok=True)
    noop = NOOP_COMMAND.split(", args = ")
    lines = ['[mcp_servers.node_repl]', noop[0], 'args = ' + noop[1],
             'enabled = false', '',
             '[mcp_servers."computer-use"]', noop[0], 'args = ' + noop[1],
             'enabled = false', '']
    for pid in LOCKDOWN_PLUGINS:
        lines += [f'[plugins."{pid}"]', "enabled = false", ""]
    (d / "config.toml").write_text("\n".join(lines))

def _log(workflow_id: str, etype: str, fields: dict) -> None:
    try:
        from agent import chatlog
        chatlog.append(workflow_id, etype, fields)
    except Exception:
        pass

# Pure argv builder, so the config table is testable
def spawn_command(effort: str = "") -> list[str]:
    cmd = [_codex_bin(), "app-server",
           "-c", f"tool_output_token_limit={CODEX_TOOL_OUTPUT_TOKENS}"]

    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    for kv in LOCKDOWN_CONFIG:
        cmd += ["-c", kv]
    return cmd

# One app-server subprocess; only this app's own tool calls are approved, everything else is declined and logged
class _AppServer:
    def __init__(self, workflow_id: str, cwd: Path, token: str,
                 env_extra: dict | None = None):
        self.pid = workflow_id
        self.token = token
        from storage import settings as _settings
        cmd = spawn_command(_settings.builder_effort("codex"))
        _log(workflow_id, "codex-spawn", {"cmd": cmd, "cwd": str(cwd)})

        env = {**os.environ, **loop.sealed_env(env_extra or {})}
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True,
                                         encoding="utf-8", errors="replace",
                                         bufsize=1, cwd=str(cwd), env=env)
        except FileNotFoundError:
            raise transport.TransportError(
                f"the Codex app isn't installed - install it from "
                f"{INSTALL_PAGE} and sign in with `codex login`")
        self._id = 0
        self._replies: dict[int, dict] = {}
        self._reply_cv = threading.Condition()
        self.events: list[dict] = []
        self._evt_cv = threading.Condition()
        self.dead = threading.Event()
        self.stderr_tail: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg and "method" not in msg:
                with self._reply_cv:
                    self._replies[msg["id"]] = msg
                    self._reply_cv.notify_all()
            elif "id" in msg:
                self._serve_request(msg)
            else:
                with self._evt_cv:
                    self.events.append(msg)
                    self._evt_cv.notify_all()
        self.dead.set()
        with self._evt_cv:
            self._evt_cv.notify_all()
        with self._reply_cv:
            self._reply_cv.notify_all()

    def _read_err(self):
        for line in self.proc.stderr:
            self.stderr_tail.append(line.rstrip()[:300])
            del self.stderr_tail[:-20]

    def _serve_request(self, msg: dict) -> None:
        method = msg.get("method", "")
        p = msg.get("params") or {}
        if method == _PROTOCOL["tool_call"]:
            threading.Thread(target=self._answer_tool_call, args=(msg,),
                             daemon=True).start()
        elif "elicitation" in method:
            meta = p.get("_meta") or {}
            ours = (p.get("serverName") == "cryogram"
                    and meta.get("codex_approval_kind") == "mcp_tool_call")
            if ours:
                self._send({"id": msg["id"],
                            "result": {"action": "accept", "content": {}}})
            else:
                _log(self.pid, "codex-declined",
                     {"method": method, "message":
                      str(p.get("message") or "")[:200]})
                self._send({"id": msg["id"], "result": {"action": "decline"}})
        elif "approval" in method.lower():
            _log(self.pid, "codex-declined", {"method": method})
            self._send({"id": msg["id"], "result": "decline"})
        else:
            _log(self.pid, "codex-error",
                 {"where": "server-request", "method": method})
            self._send({"id": msg["id"],
                        "error": {"code": -32601, "message": "unhandled"}})

    # Runs one client-supplied tool call through the app's own tool pipeline and answers it
    def _answer_tool_call(self, msg: dict) -> None:
        p = msg.get("params") or {}
        name = str(p.get("tool") or "")
        args = p.get("arguments") if isinstance(p.get("arguments"), dict) else {}
        try:
            out = codex_bridge.execute(self.token, name, args)
        except Exception as e:
            out = {"envelope_text": json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})}
        items = [{"type": "inputText", "text": str(out.get("envelope_text") or "")}]
        img = out.get("image")
        if isinstance(img, dict) and img.get("data"):
            items.append({"type": "inputImage",
                          "imageUrl": f"data:{img.get('media_type') or 'image/png'};base64,{img['data']}"})
        self._send({"id": msg["id"], "result": {"contentItems": items, "success": True}})

    def _send(self, msg: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except Exception:
            self.dead.set()

    def request(self, method: str, params: dict, timeout: float):
        self._id += 1
        mid = self._id
        self._send({"method": method, "id": mid, "params": params})
        deadline = time.time() + timeout
        with self._reply_cv:
            while mid not in self._replies:
                left = deadline - time.time()
                if left <= 0 or self.dead.is_set():
                    raise TimeoutError(f"no reply to {method}")
                self._reply_cv.wait(min(left, 1.0))
            reply = self._replies.pop(mid)
        if "error" in reply:
            _log(self.pid, "codex-error",
                 {"where": method, "error": json.dumps(reply["error"])[:300]})
            raise RuntimeError(
                f"{method}: {reply['error'].get('message', reply['error'])}")
        return reply

    def notify(self, method: str, params: dict) -> None:
        self._send({"method": method, "params": params})

    def next_event(self, timeout: float):
        deadline = time.time() + timeout
        with self._evt_cv:
            while not self.events:
                left = deadline - time.time()
                if left <= 0 or self.dead.is_set():
                    return None
                self._evt_cv.wait(min(left, 1.0))
            return self.events.pop(0)

    def terminate(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self.proc.stdin.close()
        except Exception:
            pass

# The tool definitions a thread starts with: the app's own tool surface, every one loaded from the start
def dynamic_tools() -> list[dict]:
    return [{"type": "function", "name": s["name"],
             "description": s.get("description", ""),
             "inputSchema": s.get("input_schema") or {"type": "object"},
             "deferLoading": False}
            for s in actions.schemas()]

# The initialize request: the client's name and the flag that unlocks client-supplied tools
def initialize_params() -> dict:
    return {"clientInfo": {"name": "cryogram", "title": "Cryogram", "version": "1.0"},
            "capabilities": {"experimentalApi": True}}

# The Codex on this computer is at or above the floor, or the one sentence that says what to do
def require_floor(pid: str) -> None:
    from agent import claude_code as _cc
    try:
        v = subprocess.run([_codex_bin(), "--version"], capture_output=True,
                           text=True, timeout=LOGIN_TIMEOUT)
        number = _cc.version_number(v.stdout)
    except Exception:
        return
    if _cc.below(number, CODEX_MIN_VERSION):
        raise _fail(pid, _cc.too_old_sentence("Codex", number, CODEX_MIN_VERSION))

# Classifies a failure into one plain hard-stop message - no retry, no silent downgrade
def _fail(workflow_id: str, text: str) -> transport.TransportError:
    _log(workflow_id, "codex-error", {"error": text[:500]})
    low = text.lower()
    if ("login" in low or "not signed in" in low or "unauthorized" in low
            or "401" in low or "auth" in low):
        return transport.TransportError(
            "the Codex account isn't signed in - run `codex login` in a terminal (or check the API key in Admin), then try again")
    if loop.usage_limit_message(text):
        return transport.TransportError(loop.usage_limit_message(text))
    if ("rate limit" in low or "quota" in low
            or "plan limit" in low or "429" in low):
        return transport.TransportError(
            "the Codex plan's usage limit was hit - it resets on its own; try again later or switch the builder in Admin")
    return transport.TransportError(
        f"the Codex builder failed: {text[:200]}")

# Maps codex token counts onto the common ledger shape so spend means the same thing on both engines
def _map_usage(total: dict) -> dict:
    inp = int(total.get("inputTokens") or 0)
    cached = min(int(total.get("cachedInputTokens") or 0), inp)
    return {"input_tokens": inp - cached,
            "cache_read_input_tokens": cached,
            "output_tokens": int(total.get("outputTokens") or 0),
            "codex_raw": dict(total)}

# One builder turn on the Codex engine: a fresh subprocess per turn, same tools, same rules
def run_turn_codex(workflow: dict, user_message: str, emit,
                   kind: str, tr, max_turns: int = 0) -> dict:
    import turns
    from agent import codex_bridge, context as _context

    pid = workflow["id"]
    cwd = config.workflow_dir(pid) / "work" / "codex_cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd / "AGENTS.md").write_text(AGENTS_PREAMBLE + prompt.system(workflow),
                                   encoding="utf-8")
    write_workflow_lockdown(cwd)

    env_extra: dict = {}
    if getattr(tr, "auth", "") == "codex-api-key":
        env_extra["CODEX_HOME"] = str(_api_key_home(tr.credential))
    else:
        home = _chatgpt_home()
        if home is None:
            home = _codex_home()
            _log(pid, "codex-home-shared",
                 {"home": str(home), "why": "auth.json link refused"})
        env_extra["CODEX_HOME"] = str(home)

    body = _context.build(workflow, user_message, kind)[0]["content"]
    full = ("(Your operating contract and working method are in AGENTS.md - binding. Work only through the cryogram tools.)\n"
            "\n"
            "=== SESSION CONTEXT ===\n"
            "\n" + body)

    narrator = loop.TurnNarrator(emit, pid)
    token = codex_bridge.register(workflow, emit,
                                  on_tool=narrator.tool_gap)
    entry = codex_bridge.entry(token)
    outcome = entry["outcome"]
    stopped = {"v": False}
    stats = {"rounds": 0, "t0": loop._time_now(), "usage": None,
             "model_calls": None, "batched_calls": None,
             "ctx_first": None, "ctx_last": None}

    calls_cap = max_turns * 2 if max_turns else None

    require_floor(pid)
    app = _AppServer(pid, cwd, token, env_extra)
    status = "unknown"
    try:
        try:
            app.request("initialize", initialize_params(), timeout=BOOT_TIMEOUT)
            app.notify("initialized", {})
        except (TimeoutError, RuntimeError) as e:
            raise _fail(pid, f"{e}; stderr: "
                             f"{' | '.join(app.stderr_tail[-3:])}") from e

        try:
            r = app.request(_PROTOCOL["mcp_list"], {}, timeout=BOOT_TIMEOUT)
            shape = {d.get("name"): len(d.get("tools") or {})
                     for d in (r.get("result") or {}).get("data") or []}
            _log(pid, "codex-mcp-surface", {"servers": shape})
            stats["mcp_surface"] = shape
        except Exception:
            pass
        thread_id = ""
        model = tr.model if tr.model and tr.model != DEFAULT_MODEL_SENTINEL \
            else ""

        for eph in (True, False):
            for cand in _PROTOCOL["approval_policies"]:
                try:
                    th = app.request(_PROTOCOL["thread_start"], {
                        "cwd": str(cwd), "approvalPolicy": cand,
                        "sandbox": _PROTOCOL["sandbox"],
                        "dynamicTools": dynamic_tools(),
                        **({"ephemeral": True} if eph else {}),
                        **({"model": model} if model else {})},
                        timeout=BOOT_TIMEOUT)
                    thread_id = ((th.get("result") or {}).get("thread")
                                 or {}).get("id") or ""
                    if cand == "never":
                        _log(pid, "codex-error", {
                            "where": "approval-policy",
                            "error": "ask-first policies all rejected - running under 'never' (codex may auto-run its own sandboxed shell; recorded, not silent)"})
                    break
                except RuntimeError:
                    continue
            if thread_id:
                if not eph:
                    _log(pid, "codex-error", {
                        "where": "thread-start",
                        "error": "this codex build rejected ephemeral threads - the turn will appear in the ChatGPT session history (recorded, not silent)"})
                break
        if not thread_id:
            raise _fail(pid, "codex accepted no approval policy / returned no thread id")

        try:
            trn = app.request(_PROTOCOL["turn_start"], {
                "threadId": thread_id,
                "input": [{"type": "text", "text": full}]},
                timeout=BOOT_TIMEOUT)
        except (TimeoutError, RuntimeError) as e:
            raise _fail(pid, str(e)) from e
        turn_id = ((trn.get("result") or {}).get("turn") or {}).get("id")

        interrupted = {"v": False}
        last_event = {"v": time.time()}
        quiet_noted = {"v": False}

        failure = {"text": ""}

        def _interrupt():
            if interrupted["v"]:
                return
            interrupted["v"] = True
            app.terminate()

        while True:
            if turns.should_stop(pid) and not stopped["v"]:
                stopped["v"] = True
                _interrupt()
            if entry["terminal"].is_set():
                _interrupt()
            if not interrupted["v"] and (
                    time.time() - stats["t0"] > TURN_WALL_CAP
                    or (calls_cap and entry["calls"] > calls_cap)):
                _log(pid, "codex-error", {
                    "where": "caps",
                    "error": f"wall cap ({TURN_WALL_CAP}s) or tool cap "
                             f"({calls_cap}) hit - interrupting"})
                _interrupt()
            if interrupted["v"]:
                break
            ev = app.next_event(timeout=0.2)
            if ev is None:
                if app.dead.is_set():
                    break
                if (not quiet_noted["v"] and not entry.get("busy_tool")
                        and time.time() - last_event["v"] > CODEX_QUIET_NOTE):
                    quiet_noted["v"] = True
                    _log(pid, "codex-quiet", {"seconds": CODEX_QUIET_NOTE,
                                              "stderr": app.stderr_tail[-3:]})
                continue
            last_event["v"] = time.time()
            m = ev.get("method", "")
            p = ev.get("params") or {}
            if m == _PROTOCOL["ev_delta"]:
                narrator.text(p.get("delta", ""))
            elif m == _PROTOCOL["ev_item"]:
                item = p.get("item") or {}
                itype = item.get("type")
                if itype == "dynamicToolCall":
                    stats["rounds"] += 1
                    loop._iter_log(workflow, tr, stats["rounds"],
                                   [item.get("tool") or "?"], engine="codex")
                    hist = stats.setdefault("tool_calls", {})
                    tname = item.get("tool") or "?"
                    hist[tname] = hist.get(tname, 0) + 1
                elif itype in ("commandExecution", "fileChange",
                               "mcpToolCall"):
                    _log(pid, "codex-own-tool",
                         {"item": json.dumps(item)[:300]})
            elif m == _PROTOCOL["ev_usage"]:
                total = (p.get("tokenUsage") or {}).get("total")
                if total:
                    stats["usage"] = _map_usage(total)
            elif m == _PROTOCOL["ev_done"]:
                status = (p.get("turn") or {}).get("status", "completed")
                failure["text"] = str(((p.get("turn") or {}).get("error") or {})
                                      .get("message") or "")
                break
    finally:
        app.terminate()

        if entry.get("busy_tool"):
            parked = interactions.park_workflow(pid)
            _log(pid, "codex-orphan-parked",
                 {"tool": entry.get("busy_tool"), "parked": parked})
        codex_bridge.release(token)

    _log(pid, "codex-turn", {"status": status, "tools": entry["calls"],
                             "interrupted": bool(status == "interrupted")})
    if status == "failed" and not stopped["v"]:
        raise _fail(pid, failure["text"] or "Codex reported the turn as failed and gave no reason")

    if entry["calls"] == 0 and not stopped["v"]:
        _log(pid, "codex-no-tools", {
            "surface": stats.get("mcp_surface"),
            "reply_head": "".join(narrator.buf)[:200]})
    asked = bool(turnstate.of(workflow).take("ask_open"))
    built = turnstate.of(workflow).take("built")
    fix = turnstate.of(workflow).take("pending_fix")

    stats["tool_bytes"] = (entry.get("outcome") or {}).get("tool_bytes") or {}
    stats["tool_chars_in"] = sum(v[0] for v in stats["tool_bytes"].values())
    stats["tool_chars_out"] = sum(v[1] for v in stats["tool_bytes"].values())
    loop._turn_summary_log(workflow, kind, stats, asked=asked,
                           built=bool(built or fix), stopped=stopped["v"])
    if stats["usage"]:
        loop._usage_log(workflow, stats["usage"], kind, engine="codex")
    else:
        loop._usage_missing_log(workflow, kind, engine="codex")

    import turns as _turns
    loop._NARRATORS.pop(pid, None)
    ending = loop.turn_ending(narrator.tail(), built=built, fix=fix,
                              asked=asked, stopped=stopped["v"],
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
    content = ending["content"]
    reply = {"content": content, "kind": "text",
             **({"_ask_open": True} if asked else {}),
             **({"stopped": True} if stopped["v"] else {})}
    if fix:
        reply["fix"] = fix
    return reply
