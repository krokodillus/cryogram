# The local web server: the UI and a JSON API on 127.0.0.1 only - no accounts, nothing listens beyond this machine
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets as pysecrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import config
from runtime import corpus
import extensions
from storage import environments
from runtime import executor
from agent import steps
from agent import interactions

# A browser step's request for the person at the machine: the popup, the notification, and the wait
def _window_request(workflow_id: str, emit):
    def ask(message: str) -> str:
        from runtime import capability
        iid = interactions.create_sync(
            "run-request", {"workflow_id": workflow_id, "message": message})
        emit({"type": "user-request", "iid": iid, "message": message})
        answered = interactions.wait_sync(
            iid, timeout=max(30.0, capability.REQUEST_PATIENCE - 20))
        if turns.should_stop(workflow_id):
            return "stopped"
        return "answered" if answered is not None else "unanswered"
    return ask
from agent import transcript
from agent import orchestrator
import models
from storage import issues
from storage import node_stats
from runtime import run_state
from storage import secrets_store
from storage import settings
from storage import store

CONTENT_TYPES = {
    ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
    ".json": "application/json", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".md": "text/markdown",
}

# A fingerprint of the app's own code, used to tell a stale instance from this one
def _build_id(root: Optional[Path] = None) -> str:
    root = root or Path(__file__).resolve().parent
    h = hashlib.sha1()
    for f in (sorted(root.glob("*.py"))
              + sorted(root.glob("agent/*.py"))
              + sorted(root.glob("runtime/*.py"))
              + sorted(root.glob("storage/*.py"))
              + sorted(root.glob("extensions/*.py"))
              + sorted(root.glob("extensions/*/*.py"))):
        h.update(str(f.relative_to(root)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]

BUILD_ID = _build_id()

# Opens the operating system's own folder or file dialog, since a browser cannot reveal a real path
def _native_pick_folder(runner=None, platform: Optional[str] = None,
                        want: str = "folder") -> dict:
    import subprocess
    run = runner or subprocess.run
    plat = platform or sys.platform

    afile = want == "file"
    prompt = ("Choose a file for the workflow" if afile
              else "Choose a folder for the workflow")
    if plat == "darwin":
        chooser = "choose file" if afile else "choose folder"
        script = ('tell application "System Events"\nactivate\n'
                  f'set f to POSIX path of ({chooser} with prompt '
                  f'"{prompt}")\nend tell\nreturn f')
        cmd = ["osascript", "-e", script]
    elif plat.startswith("win"):
        dlg = "OpenFileDialog" if afile else "FolderBrowserDialog"
        prop = "FileName" if afile else "SelectedPath"
        ps = ("Add-Type -AssemblyName System.Windows.Forms; "
              f"$d = New-Object System.Windows.Forms.{dlg}; "
              "$t = New-Object System.Windows.Forms.Form -Property @{TopMost=$true}; "
              f"if ($d.ShowDialog($t) -eq 'OK') {{ $d.{prop} }}")
        cmd = ["powershell", "-NoProfile", "-Command", ps]
    else:
        cmd = ["zenity", "--file-selection"] + ([] if afile else ["--directory"]) + [
            f"--title={prompt}"]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return {"unavailable": True}
    except Exception:
        return {"cancelled": True}
    picked = (r.stdout or "").strip()
    if r.returncode == 0 and picked:
        return {"path": picked}

    err = (r.stderr or "").lower()
    if "display" in err or "dbus" in err or "not found" in err:
        return {"unavailable": True}
    return {"cancelled": True}

_server: Optional[ThreadingHTTPServer] = None

# A fresh per-start token the UI must send back, so other local software can't drive the API as if it were the page
LOCAL_TOKEN = os.environ.get("CRYOGRAM_LOCAL_TOKEN") or pysecrets.token_urlsafe(24)

# Over-size or malformed bodies route to a plain 400
class _BadBody(ValueError):
    pass

# What an integration's handler is given: the request, without the server behind it
class _ExtCtx:
    def __init__(self, handler, method: str, parts: list, wildcards: list):
        self._handler = handler
        self.method = method
        self.parts = list(parts)
        self.wildcards = list(wildcards)

    # The query string, parsed
    @property
    def query(self) -> dict:
        from urllib.parse import parse_qs
        return parse_qs(urlparse(self._handler.path).query)

    # The JSON body, bounded and validated the same way every other route reads it
    def body(self) -> dict:
        return self._handler._body()

# The one turn pipeline, wire-optional: buffered events and a guaranteed done, whoever (or nothing) is watching
def _turn_body(workflow: dict, kind: str, run_turn, pre_items,
               done_extra, _write_line) -> None:
    import turns
    from agent import chatlog
    from agent import reply as _reply
    pid = workflow["id"]
    chatlog.append(pid, "turn-start",
                   {"turn": kind, "turn_id": turns.current_turn_id(pid)})

    def emit_raw(ev: dict) -> None:
        ev.setdefault("ts", round(time.time(), 3))
        turns.record(pid, ev)
        chatlog.event(pid, ev)
        _write_line(ev)

    def _land_interjections() -> None:
        _reply.land_parked(workflow)

    def emit(ev: dict) -> None:
        _land_interjections()
        emit_raw(ev)

    turns.bind_emit(pid, emit_raw)
    error = None
    try:
        emit({"type": "turn", "turn_id": turns.current_turn_id(pid)})
        for it in pre_items or []:
            emit({"type": "shown", "item": it})
        reply = run_turn(emit)

        _land_interjections()
        chatlog.append(pid, "assistant", {"content": (reply or {}).get("content", ""),
                                          "reply_kind": (reply or {}).get("kind")})
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        chatlog.append(pid, "turn-error", {"error": error})

        from agent import loop as _loop
        reply = {"content": _loop.failed_ending("", turns.partial_text(pid)),
                 "kind": "error"}
    try:
        try:
            content = reply.get("content", "")
            if content.strip():
                transcript.append_message(workflow, "assistant", content)

            store.save_turn(workflow)

            try:
                workflow["_steps"] = steps.step_states(workflow)
            except Exception:
                pass
            done = {"type": "done", "workflow": workflow, **(done_extra or {})}
        except Exception as e:
            error = error or f"{type(e).__name__}: {e}"
            done = {"type": "done", **(done_extra or {})}

        if isinstance(reply, dict) and reply.get("fix"):
            done["fix"] = reply["fix"]

        if isinstance(reply, dict) and reply.get("stopped"):
            done["stopped"] = True

        if isinstance(reply, dict) and reply.get("_ask_open"):
            done["asked"] = True

        if error:
            done["error"] = error
        try:
            emit(done)
        except Exception as e:
            emit({"type": "done", "error": error or f"{type(e).__name__}: {e}"})
    finally:
        turns.unbind_emit(pid)
        turns.finish(pid, error)

    leftover = turns.unread_interjections(pid)
    if leftover:
        _spawn_unread_followup(pid, leftover)

FOLLOWUP_SLOT_WAIT = 90
FOLLOWUP_SLOT_POLL = 1.0

# Runs a turn whose slot is already claimed on its own thread; the page follows it by polling
def _spawn_turn(workflow: dict, kind: str, run_turn, pre_items=None) -> None:
    from agent import chatlog
    pid = workflow["id"]

    def _go():
        import turns
        try:
            _turn_body(workflow, kind, run_turn, pre_items, None,
                       lambda ev: None)
        except Exception as e:
            chatlog.append(pid, "turn-thread-error",
                           {"error": f"{type(e).__name__}: {e}"})
            turns.finish(pid, str(e))

    threading.Thread(target=_go, daemon=True, name=f"turn-{pid}").start()

def _spawn_unread_followup(pid: str, texts: list) -> None:
    from agent import chatlog

    def _go():
        import turns
        from agent import orchestrator
        deadline = time.time() + FOLLOWUP_SLOT_WAIT
        while not turns.begin(pid, "chat"):
            if time.time() > deadline:
                chatlog.append(pid, "followup-slot-timeout",
                               {"messages": len(texts)})
                return
            time.sleep(FOLLOWUP_SLOT_POLL)
        try:
            workflow = store.load(pid)
            if not workflow:
                turns.finish(pid, "workflow missing")
                return
            content = "\n\n".join(str(t) for t in texts)

            steps.settle_open_asks(workflow)
            if steps.supersede_open_plan(workflow):
                chatlog.append(pid, "plan-answered-by-message", {})
            chatlog.append(pid, "user", {"content": content,
                                         "followup": True})
            orchestrator.mark_issue_in_progress(workflow, content)
            store.save(workflow)
            _turn_body(workflow, "chat",
                       lambda emit: orchestrator.handle_chat_stream(
                           workflow, content, emit),
                       None, None, lambda ev: None)
        except Exception as e:
            chatlog.append(pid, "followup-error",
                           {"error": f"{type(e).__name__}: {e}"})
            turns.finish(pid, str(e))

    threading.Thread(target=_go, daemon=True, name=f"followup-{pid}").start()

# The route table: method, path, handler. A {name} segment matches any one value and reaches the handler as an argument
ROUTES = (
    ("GET",    "api/extensions",                                  "_get_extensions"),
    ("GET",    "api/workflows",                                   "_get_workflows"),
    ("GET",    "api/fs",                                          "_get_fs"),
    ("GET",    "api/workflows/{wid}",                             "_get_workflow"),
    ("GET",    "api/workflows/{wid}/run-preflight",               "_get_run_preflight"),
    ("GET",    "api/workflows/{wid}/turn",                        "_get_turn"),
    ("GET",    "api/workflows/{wid}/runs",                        "_get_runs"),
    ("GET",    "api/workflows/{wid}/runs/{rid}/step-outputs",     "_get_run_step_outputs"),
    ("GET",    "api/workflows/{wid}/nodes/{nid}/evidence",        "_get_node_evidence"),
    ("GET",    "api/blobs/{ref}",                                 "_get_blob"),
    ("GET",    "api/workflows/{wid}/learnings",                   "_get_workflow_learnings"),
    ("GET",    "api/learnings",                                   "_get_learnings"),
    ("GET",    "api/workflows/{wid}/ai-usage",                    "_get_ai_usage"),
    ("GET",    "api/workflows/{wid}/versions",                    "_get_versions"),
    ("GET",    "api/settings",                                    "_get_settings"),
    ("GET",    "api/models/workflow",                             "_get_workflow_models"),
    ("GET",    "api/providers/usage",                             "_get_provider_usage"),
    ("GET",    "api/version",                                     "_get_version"),
    ("GET",    "api/update/check",                                "_get_update_check"),
    ("GET",    "api/workflows/{wid}/tickets/{tid}/share-preview", "_get_ticket_share_preview"),
    ("GET",    "api/build",                                       "_get_build"),
    ("GET",    "api/environments",                                "_get_environments"),
    ("GET",    "api/environments/{eid}",                          "_get_environment"),
    ("POST",   "api/workflows/import",                            "_post_workflow_import"),
    ("POST",   "api/admin/delete-all-data",                       "_post_delete_all_data"),
    ("POST",   "api/fs/pick",                                     "_post_fs_pick"),
    ("POST",   "api/workflows/{wid}/seen",                        "_post_workflow_seen"),
    ("POST",   "api/providers/{pid}/models",                      "_post_provider_models"),
    ("POST",   "api/providers/{pid}/test",                        "_post_provider_test_call"),
    ("POST",   "api/codex/check",                                 "_post_codex_check"),
    ("POST",   "api/claude/check",                                "_post_claude_check"),
    ("POST",   "api/providers/test",                              "_post_provider_test"),
    ("POST",   "api/shutdown",                                    "_post_shutdown"),
    ("POST",   "api/workflows",                                   "_post_workflow"),
    ("POST",   "api/environments",                                "_post_environment"),
    ("POST",   "api/environments/{eid}/duplicate",                "_post_environment_duplicate"),
    ("POST",   "api/environments/{eid}/variables",                "_post_environment_variable"),
    ("POST",   "api/workflows/{wid}/duplicate",                   "_post_workflow_duplicate"),
    ("POST",   "api/workflows/{wid}/variables/use",               "_post_variable_use"),
    ("POST",   "api/workflows/{wid}/versions/{vid}/restore",      "_post_version_restore"),
    ("POST",   "api/workflows/{wid}/variables",                   "_post_workflow_variable"),
    ("POST",   "api/workflows/{wid}/say",                         "_say"),
    ("POST",   "api/workflows/{wid}/run",                         "_run"),
    ("POST",   "api/workflows/{wid}/stop",                        "_stop"),
    ("POST",   "api/workflows/{wid}/tickets/{tid}/investigate",   "_investigate"),
    ("POST",   "api/workflows/{wid}/tickets/{tid}/share",         "_post_ticket_share"),
    ("POST",   "api/workflows/{wid}/runs/{rid}/decline",          "_post_run_decline"),
    ("POST",   "api/workflows/{wid}/runs/{rid}/seen",             "_post_run_seen"),
    ("POST",   "api/workflows/{wid}/runs/{rid}/proceed",          "_post_run_proceed"),
    ("POST",   "api/workflows/{wid}/tickets/{tid}/user-outputs",  "_post_ticket_user_outputs"),
    ("POST",   "api/workflows/{wid}/nodes/{nid}/model",           "_node_model"),
    ("POST",   "api/workflows/{wid}/nodes/{nid}/prompt",          "_node_prompt"),
    ("POST",   "api/workflows/{wid}/samples",                     "_post_sample"),
    ("POST",   "api/blobs",                                       "_post_blob"),
    ("PUT",    "api/interactions/{iid}",                          "_put_interaction"),
    ("PUT",    "api/settings",                                    "_put_settings"),
    ("PUT",    "api/secrets/{name}",                              "_put_secret"),
    ("PUT",    "api/workflows/{wid}/secrets/{name}",              "_put_workflow_secret"),
    ("PUT",    "api/environments/{eid}",                          "_put_environment"),
    ("PUT",    "api/environments/{eid}/variables/{name}",         "_put_environment_variable"),
    ("PUT",    "api/workflows/{wid}/group",                       "_put_workflow_group"),
    ("PUT",    "api/workflows/{wid}/meta",                        "_put_workflow_meta"),
    ("PUT",    "api/workflows/{wid}/environment",                 "_put_workflow_environment"),
    ("PUT",    "api/workflows/{wid}/tickets/{tid}",               "_put_ticket"),
    ("PUT",    "api/workflows/{wid}/nodes/{nid}/invalid-items",   "_put_node_invalid_items"),
    ("PUT",    "api/workflows/{wid}/nodes/{nid}/may-be-empty",    "_put_node_may_be_empty"),
    ("PUT",    "api/workflows/{wid}/nodes/{nid}/approval",        "_put_node_approval"),
    ("PUT",    "api/workflows/{wid}/variables/{name}",            "_put_workflow_variable"),
    ("DELETE", "api/learnings/{lid}",                             "_delete_learning"),
    ("DELETE", "api/secrets/{name}",                              "_delete_secret"),
    ("DELETE", "api/workflows/{wid}/samples/{name}",              "_delete_sample"),
    ("DELETE", "api/workflows/{wid}",                             "_delete_workflow"),
    ("DELETE", "api/workflows/{wid}/variables/{name}",            "_delete_workflow_variable"),
    ("DELETE", "api/environments/{eid}",                          "_delete_environment"),
    ("DELETE", "api/environments/{eid}/variables/{name}",         "_delete_environment_variable"),
)

_ROUTE_TABLE = tuple((m, p.split("/"), h) for m, p, h in ROUTES)

# Matches one request path against one pattern: same length, literals equal; returns the {name} values in order, or None
def _match(pattern: list, parts: list):
    if len(pattern) != len(parts):
        return None
    args = []
    for want, got in zip(pattern, parts):
        if want.startswith("{"):
            args.append(got)
        elif want != got:
            return None
    return args

class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str,
              no_cache: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if no_cache:
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    _LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")

    # Every request passes DNS-rebinding (Host) and CSRF (Origin) checks before any handler runs
    def _guard(self, state_changing: bool) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in self._LOCAL_HOSTS:
            self._json({"error": "forbidden host"}, 403)
            return False
        if state_changing:
            origin = self.headers.get("Origin")
            if origin:
                from urllib.parse import urlparse as _up
                ohost = (_up(origin).hostname or "").lower()
                if ohost not in ("localhost", "127.0.0.1", "::1"):
                    self._json({"error": "forbidden origin"}, 403)
                    return False
        path = urlparse(self.path).path
        if (config.BUILD != "dev" and path.startswith("/api/")
                and not self._token_exempt(path, state_changing)
                and self.headers.get("X-Cryogram-Token") != LOCAL_TOKEN):
            self._json({"error": "missing or wrong token"}, 403)
            return False
        return True

    # Paths that must work without the local token - probes that run before a page is served, and downloads a header cannot reach
    @staticmethod
    # The few routes that work without the page token, such as reading the version
    def _token_exempt(path: str, state_changing: bool) -> bool:
        # Shutdown works without the page token on purpose: the processes that stop a stale server never saw this boot's token, and the worst an abuser gets is a closed app, not data
        if path in ("/api/version", "/api/build", "/api/shutdown"):
            return True
        return path.startswith("/api/blobs/") and not state_changing

    # Serves the UI files; index.html gets the per-start token written into its meta tag
    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (config.FRONTEND_DIR / rel).resolve()
        if config.FRONTEND_DIR not in target.parents and target != config.FRONTEND_DIR:
            return self._send(403, b"forbidden", "text/plain")
        if not target.exists() or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        if target.name == "index.html":
            body = body.replace(b"__CRYOGRAM_LOCAL_TOKEN__", LOCAL_TOKEN.encode())
        self._send(200, body, ctype, no_cache=True)

    # Serves an installed integration's own page files from its own directory
    def _ext_asset(self, path: str) -> None:
        bits = [b for b in path.split("/") if b]
        target = (extensions.asset(bits[1], "/".join(bits[2:]))
                  if len(bits) >= 3 else None)
        if target is None:
            return self._send(404, b"not found", "text/plain")
        ctype = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype, no_cache=True)

    # Lets an installed integration answer a route the app itself does not have
    def _ext(self, method: str, parts: list) -> bool:
        hit = extensions.dispatch(method, parts)
        if hit is None:
            return False
        handler, wildcards = hit
        try:
            status, payload = handler(_ExtCtx(self, method, parts, wildcards))
        except _BadBody as e:
            self._json({"error": str(e)}, 400)
            return True
        except Exception as e:
            self._json({"error": str(e)}, 502)
            return True
        self._json(payload, status)
        return True

    # All read routes: the page files, an integration's page files, then the table
    def do_GET(self) -> None:
        if not self._guard(state_changing=False):
            return
        path = urlparse(self.path).path
        if path.startswith("/ext/"):
            return self._ext_asset(path)
        if not path.startswith("/api/"):
            return self._static(path)
        self._route("GET", path)

    # All create/action routes; a bad body is a 400, never a dropped connection
    def do_POST(self) -> None:
        if not self._guard(state_changing=True):
            return
        try:
            self._route("POST", urlparse(self.path).path)
        except _BadBody as e:
            self._json({"error": str(e)}, 400)
        except secrets_store.SecretsUnavailable as e:
            self._json({"error": str(e)}, 500)

    # All update routes; a bad body is a 400, never a dropped connection
    def do_PUT(self) -> None:
        if not self._guard(state_changing=True):
            return
        try:
            self._route("PUT", urlparse(self.path).path)
        except _BadBody as e:
            self._json({"error": str(e)}, 400)
        except secrets_store.SecretsUnavailable as e:
            self._json({"error": str(e)}, 500)

    # All delete routes
    def do_DELETE(self) -> None:
        if not self._guard(state_changing=True):
            return
        self._route("DELETE", urlparse(self.path).path)

    # One request: the table first, then an installed integration's routes, then 404
    def _route(self, method: str, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        if self._dispatch(method, parts) or self._ext(method, parts):
            return
        self._json({"error": "unknown route"}, 404)

    # Finds the table row for a request and calls its handler with the {name} values; False when no row matches
    def _dispatch(self, method: str, parts: list) -> bool:
        for m, pattern, handler in _ROUTE_TABLE:
            if m != method:
                continue
            args = _match(pattern, parts)
            if args is not None:
                getattr(self, handler)(*args)
                return True
        return False

    def _get_extensions(self):
        return self._json({"items": extensions.manifest(),
                           "problems": [{"name": n, "error": e}
                                        for n, e in extensions.FAILED]})

    def _get_workflows(self):
        return self._json(store.list_workflows())

    def _get_fs(self):
        from urllib.parse import parse_qs
        q = parse_qs(urlparse(self.path).query)
        home = Path.home().resolve()
        raw = (q.get("path") or [str(home)])[0]
        target = Path(raw).expanduser().resolve()
        if target != home and home not in target.parents:
            target = home
        try:
            dirs = sorted(d.name for d in target.iterdir()
                          if d.is_dir() and not d.name.startswith("."))
        except OSError:
            dirs = []
        return self._json({"path": str(target),
                           "parent": str(target.parent)
                           if target != home else None,
                           "dirs": dirs})

    def _get_workflow(self, wid):
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)

        workflow["_variable_resolution"] = environments.resolution_summary(workflow)

        descs: dict = {}
        for n in workflow.get("nodes", []):
            for p in n.get("inputs", []) or []:
                d = str(p.get("description") or "").strip()
                if d and p.get("name") and p["name"] not in descs:
                    descs[p["name"]] = d
        workflow["_variable_descriptions"] = descs

        workflow["_variable_types"] = {
            v["name"]: t for v in workflow.get("variables", []) or []
            if v.get("name") and not v.get("secret")
            and (t := environments.port_type_for(workflow, v["name"]))}

        from storage import blobstore as _blobs
        workflow["_variable_files"] = {
            v["name"]: (_blobs.stat(v["value"]) or {}).get("name") or ""
            for v in workflow.get("variables", []) or []
            if isinstance(v.get("value"), str)
            and v["value"].startswith("blob:")}

        workflow["_ai_defaults"] = {"max_tokens": config.AI_MAX_TOKENS}

        workflow.update(extensions.first("workflow_extra", workflow) or {})

        wait = run_state.latest_halt(wid)

        if wait and (wait.get("halt_ts") or wait.get("started") or 0) < (
                workflow.get("last_run_ts") or 0):
            wait = None
        if wait:
            node = next((n for n in workflow.get("nodes", [])
                         if n.get("id") == wait.get("halted_at")), None)
            workflow["_run_wait"] = {
                "run_id": wait.get("run_id"),
                "node_id": wait.get("halted_at"),
                "node_name": (node or {}).get("name") or "",
                "reason": wait.get("reason"),
                "verdict": wait.get("verdict") or {},
                "ts": wait.get("halt_ts") or wait.get("started")}

        from storage import deliverables as _deliv
        unseen = _deliv.latest_unseen(wid)
        if unseen:
            workflow["_run_unseen"] = unseen

        workflow["_steps"] = steps.step_states(workflow)

        node_stats.attach(workflow["id"], workflow)

        workflow["pending"] = interactions.pending(wid)

        for n in workflow.get("nodes", []) or []:
            n.pop("tests", None)
        for n in (workflow.get("plan") or {}).get("nodes", []) or []:
            n.pop("tests", None)
        return self._json(workflow)

    def _get_run_preflight(self, wid):
        workflow = store.load_ro(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        return self._json({"missing": executor.missing_at_start(workflow)})

    def _get_turn(self, wid):
        import turns
        from urllib.parse import parse_qs
        q = parse_qs(urlparse(self.path).query)
        try:
            after = int((q.get("after") or ["0"])[0])
        except ValueError:
            after = 0
        snap = turns.snapshot(wid, after)
        snap["pending"] = interactions.pending(wid)
        snap["build"] = BUILD_ID
        return self._json(snap)

    def _get_runs(self, wid):
        from storage import deliverables
        from urllib.parse import parse_qs
        if not store.load(wid):
            return self._json({"error": "not found"}, 404)
        q = parse_qs(urlparse(self.path).query)
        try:
            page = int((q.get("page") or ["1"])[0])
        except ValueError:
            page = 1
        return self._json(deliverables.runs_page(wid, page))

    def _get_run_step_outputs(self, wid, rid):
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        rec = run_state.load(rid) or {}
        nid = rec.get("halted_at") or ""
        node = next((n for n in workflow.get("nodes", [])
                     if n["id"] == nid), None)
        if not node:
            return self._json({"error": "run not found"}, 404)
        return self._json({
            "node_id": nid, "step": node.get("name") or nid,
            "fields": executor.unverified_output_fields(node, rid)})

    def _get_node_evidence(self, wid, nid):
        from agent import evidence
        workflow = store.load_ro(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        node = next((n for n in workflow.get("nodes", [])
                     if n.get("id") == nid), None)
        if not node:
            return self._json({"error": "not found"}, 404)
        return self._json({"sample": evidence.recorded_sample(workflow, node)})

    def _get_blob(self, ref):
        import re as _re
        from storage import blobstore
        ref = f"blob:{ref}"
        if not blobstore.exists(ref):
            return self._json({"error": "not found"}, 404)

        from urllib.parse import parse_qs
        wfl = (parse_qs(urlparse(self.path).query).get("wfl") or [""])[0]
        st = blobstore.stat(ref, secrets_store.workflow_owner(wfl) if wfl else None)
        data = blobstore.get(ref)
        name = _re.sub(r"[^\w. ()-]", "_", st.get("name") or ref[:12]) or "download"
        mime = st.get("mime") or "application/octet-stream"

        inline = (mime.startswith("image/") or mime == "application/pdf"
                  or mime == "text/plain")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Disposition",
                         f'{"inline" if inline else "attachment"}; '
                         f'filename="{name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return

    def _get_workflow_learnings(self, wid):
        from storage import learnings as _learnings
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        out = []
        for lid in reversed(workflow.get("learning_ids") or []):
            got = _learnings.read(lid)
            if got:
                out.append({"id": got["id"], "title": got["title"],
                            "updated": got["updated"],
                            "content": got["content"]})
        return self._json(out)

    def _get_learnings(self):
        from storage import learnings as _learnings
        idx = _learnings.index()
        for e in idx:
            n = _learnings.read(e["id"])
            e["content"] = (n or {}).get("content", "")
        return self._json(idx)

    def _get_ai_usage(self, wid):
        from storage import db
        return self._json({"usage": db.ai_usage_workflow(wid),
                           "window": "all time"})

    def _get_versions(self, wid):
        from storage import db
        if store.load_ro(wid) is None:
            return self._json({"error": "not found"}, 404)
        return self._json({"versions": db.workflow_versions(wid)})

    def _get_settings(self):
        view = settings.public_view()

        for p in view.get("providers") or []:
            url = settings.KEY_URLS.get(str(p.get("id") or ""))
            if url:
                p["key_url"] = url

        try:
            from agent import codex_engine
            view["codex_connected"] = codex_engine.connected_hint()
        except Exception:
            view["codex_connected"] = False
        try:
            from agent import claude_code
            view["claude_connected"] = claude_code.connected_hint()
            view["claude_program"] = claude_code.program_hint()
        except Exception:
            view["claude_connected"] = False

        try:
            view["codex_program"] = codex_engine.program_hint()
        except Exception:
            pass
        return self._json(view)

    def _get_workflow_models(self):
        import providers
        return self._json({"models": [
            {**m, "ready": providers.is_ready({"model": m["name"],
                                               "provider_id": m["provider_id"]}),

             "reads": providers.model_facts({"model": m["name"],
                                             "provider_id": m["provider_id"]})["reads"]}
            for m in providers.node_models()]})

    def _get_provider_usage(self):
        import providers
        from storage import db
        TOKEN_WINDOW_DAYS = 30
        usage = node_stats.provider_usage()
        tokens = db.ai_usage_by_provider(
            time.time() - TOKEN_WINDOW_DAYS * 86400)
        for pid_, counts in usage.items():
            counts["tokens"] = tokens.get(pid_, {"in": 0, "out": 0})
        return self._json({"usage": usage,
                           "token_window_days": TOKEN_WINDOW_DAYS})

    def _get_version(self):
        import turns as _turns

        try:
            stale = _build_id() != BUILD_ID
        except Exception:
            stale = False
        return self._json({"version": config.VERSION,
                           "channel": config.BUILD,
                           "build": BUILD_ID,
                           "stale": stale,
                           "extensions": extensions.ids(),

                           "busy": _turns.any_running()})

    def _get_update_check(self):
        import updater
        return self._json(updater.check())

    def _get_ticket_share_preview(self, wid, tid):
        import telemetry
        workflow = store.load_ro(wid)
        t = next((x for x in (workflow or {}).get("tickets", [])
                  if x["id"] == tid), None)
        if not t:
            return self._json({"error": "issue not found"}, 404)
        return self._json({"payload": telemetry.issue_report(workflow, t),
                           "mode": telemetry.report_mode()})

    def _get_build(self):
        return self._json({"build": BUILD_ID})

    def _get_environments(self):
        return self._json(environments.list_environments())

    def _get_environment(self, eid):
        env = environments.load(eid)
        return (self._json(environments.annotate_secret_state(env))
                if env else self._json({"error": "not found"}, 404))

    def _post_workflow_import(self):
        from storage import share
        body = self._body()
        r = share.import_doc(body.get("doc"), str(body.get("name") or ""))
        return self._json(r, 200 if r.get("ok") else 400)

    def _post_delete_all_data(self):
        import shutil
        import turns as _turns
        if _turns.any_running():
            return self._json({"error": "Something is still running - stop it first."}, 409)
        for child in sorted(config.DATA_DIR.iterdir()):
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                pass

        from storage import secrets_store as _ss
        _ss.forget_key()
        if _server is not None:
            threading.Thread(target=_server.shutdown, daemon=True).start()
        return self._json({"ok": True, "shutting_down": True})

    def _post_fs_pick(self):
        want = "file" if "want=file" in (urlparse(self.path).query or "") \
            else "folder"
        return self._json(_native_pick_folder(want=want))

    def _post_workflow_seen(self, wid):
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        workflow["attention_seen_ts"] = time.time()
        store.save(workflow)
        return self._json({"ok": True})

    def _post_provider_models(self, pid):
        import providers
        p = next((x for x in settings.get().get("providers", [])
                  if x.get("id") == pid), None)
        if not p:
            return self._json({"error": "unknown provider"}, 404)
        return self._json(providers.list_models(p))

    def _post_provider_test_call(self, pid):
        import providers
        p = next((x for x in settings.get().get("providers", [])
                  if x.get("id") == pid), None)
        if not p:
            return self._json({"error": "unknown provider"}, 404)
        return self._json(providers.test_call(p))

    def _post_claude_check(self):
        from agent import claude_code
        return self._json(claude_code.check_login())

    def _post_codex_check(self):
        from agent import codex_engine
        return self._json(codex_engine.check_login())

    def _post_provider_test(self):
        import providers
        body = self._body()
        key = body.get("key") or secrets_store.get_secret(
            body.get("key_name", ""), secrets_store.OWNER_APP) or ""
        return self._json(providers.check_key(
            body.get("adapter", "anthropic"), body.get("endpoint", ""),
            body.get("model", ""), key))

    def _post_shutdown(self):
        self._json({"shutting_down": True, "build": BUILD_ID})
        if _server is not None:
            threading.Timer(0.2, _server.shutdown).start()
        return

    def _post_workflow(self):
        body = self._body()
        env_ids = [e for e in (body.get("environment_ids") or [])
                   if (e or "").strip()]
        for eid in env_ids:
            if not environments.load(eid):
                return self._json({"error": f"no environment {eid!r}"}, 400)
        workflow = models.Workflow(
            name=(body.get("name") or "").strip() or "Untitled workflow",
            description=(body.get("description") or "").strip(),
            group=(body.get("group") or "").strip(),
            environment_ids=env_ids).to_dict()
        store.save(workflow)
        return self._json(workflow)

    def _post_environment(self):
        body = self._body()
        env = models.Environment(
            name=(body.get("name") or "").strip() or "Untitled environment",
            description=(body.get("description") or "").strip(),
            group=(body.get("group") or "").strip()).to_dict()
        environments.save(env)
        return self._json(env)

    def _post_environment_duplicate(self, eid):
        r = environments.duplicate_environment(eid)
        if r.get("error"):
            return self._json({"error": r["error"]}, 404)
        return self._json({"environment":
                           environments.annotate_secret_state(
                               r["environment"])})

    def _post_environment_variable(self, eid):
        env = environments.load(eid)
        if not env:
            return self._json({"error": "not found"}, 404)
        body = self._body()
        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "variable needs a name"}, 400)
        if not re.fullmatch(r"[a-z0-9_]+", name):
            return self._json({"error": "Variable names use lowercase letters, numbers and underscores only."},
                              400)
        if any(v["name"] == name for v in env["variables"]):
            return self._json({"error": f"variable {name!r} already exists"}, 400)
        secret = bool(body.get("secret"))
        var = {"name": name, "secret": secret,
               "label": (body.get("label") or "").strip(),
               "kind": body.get("kind") or "config",

               "value": None if secret else body.get("value")}
        if secret and body.get("value"):
            secrets_store.set_secret(
                name, body["value"], secrets_store.environment_owner(env["id"]))
        env["variables"].append(var)
        environments.save(env)
        return self._json(env)

    def _post_workflow_duplicate(self, wid):
        body = self._body()
        r = store.duplicate_workflow(wid, name=(body.get("name") or ""))
        return self._json(r, 200 if r.get("ok") else 404)

    def _post_variable_use(self, wid):
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        body = self._body()
        name = (body.get("name") or "").strip()
        source = (body.get("source") or "").strip()
        if not name or not source:
            return self._json({"error": "name and source required"}, 400)
        if source == "workflow":
            if not any(v.get("name") == name
                       for v in workflow.get("variables", [])):
                return self._json({"error": "no workflow variable "
                                   f"named {name!r}"}, 400)
            ref = "workflow"
        else:
            if source not in environments.env_ids(workflow):
                return self._json({"error": "that environment isn't attached to this workflow"}, 400)
            env = environments.load(source)
            if not env or not any(v["name"] == name
                                  for v in env.get("variables", [])):
                return self._json({"error": f"that environment has no "
                                   f"variable named {name!r}"}, 400)
            ref = environments.canonical(source, name)
        workflow.setdefault("env_bindings", {})[name] = ref
        store.save(workflow)
        return self._json({"name": name, "source": source,
                           "resolution":
                           environments.resolution_summary(workflow)})

    def _post_version_restore(self, wid, vid):
        import turns
        if store.load_ro(wid) is None:
            return self._json({"error": "not found"}, 404)
        try:
            vid = int(vid)
        except ValueError:
            return self._json({"error": "bad version id"}, 400)
        if not turns.begin(wid, "restore"):
            return self._json({"error": "The Builder Agent is working on this workflow - wait for it to finish, or stop it first."}, 409)
        try:
            res = store.restore_version(wid, vid)
        finally:
            turns.finish(wid)
        if not res.get("ok"):
            return self._json({"error": res.get("error") or
                               "could not restore"}, 400)
        return self._json({"ok": True, "restored_ts": res.get("restored_ts")})

    def _post_workflow_variable(self, wid):
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        body = self._body()
        name = (body.get("name") or "").strip()
        if not name:
            return self._json({"error": "variable needs a name"}, 400)
        if not re.fullmatch(r"[a-z0-9_]+", name):
            return self._json({"error": "Variable names use lowercase letters, numbers and underscores only."},
                              400)
        if any(v.get("name") == name for v in workflow.get("variables", [])):
            return self._json({"error": f"variable {name!r} already exists"}, 400)
        secret = bool(body.get("secret"))
        if secret and body.get("value"):
            secrets_store.set_secret(name, body["value"],
                                     secrets_store.workflow_owner(workflow["id"]))

        declared = environments.setting_type(body.get("type") or "")
        if body.get("type") and not declared:
            return self._json({"error": "A stored value can be text, a number, a date, true/false, a file or a folder."}, 400)
        port = environments.port_for(workflow, name) or {}
        if declared and not port.get("type"):
            port = {"name": name, "type": declared}
        val, understood = (environments.coerce_to_type(body.get("value"),
                                                       declared)
                           if declared and not port.get("type") else
                           environments.type_variable_value(
                               workflow, name, body.get("value")))
        if not secret and not understood:
            t = declared or environments.port_type_for(workflow, name)
            return self._json({"error": f"That value is used as a {t}, so "
                                        f"{body.get('value')!r} will not "
                                        "work here."}, 400)
        if not secret:
            fits, want = environments.value_fits_port(val, port)
            if not fits:
                return self._json(
                    {"error": f"That value has to be {want}."}, 400)
        workflow.setdefault("variables", []).append(
            {"name": name, "secret": secret,
             "label": (body.get("label") or "").strip(),
             "persistent": body.get("persistent", True) is not False,
             **({"type": declared} if declared else {}),
             "value": None if secret
                      else (val if environments.value_is_set(val) else "")})
        store.save(workflow)
        return self._json(workflow)

    def _post_ticket_share(self, wid, tid):
        import telemetry
        workflow = store.load_ro(wid)
        t = next((x for x in (workflow or {}).get("tickets", [])
                  if x["id"] == tid), None)
        if not t:
            return self._json({"error": "issue not found"}, 404)

        ok = telemetry.send_issue(workflow, t, self.headers.get("User-Agent", ""))
        return self._json({"ok": ok} if ok else
                          {"ok": False,
                           "error": "Couldn't reach Cryogram right now - nothing was sent. Try again later."})

    def _post_run_decline(self, wid, rid):
        if not store.load_ro(wid):
            return self._json({"error": "not found"}, 404)
        rec = run_state.load(rid) or {}
        if rec.get("workflow_id") != wid:
            return self._json({"error": "run not found"}, 404)
        if not run_state.decline_approval(rid):
            return self._json({"error": "That run is no longer waiting for approval."}, 409)
        return self._json({"ok": True})

    def _post_run_seen(self, wid, rid):
        if not store.load_ro(wid):
            return self._json({"error": "not found"}, 404)
        from storage import deliverables as _deliv
        return self._json({"ok": _deliv.mark_seen(wid, rid)})

    def _post_run_proceed(self, wid, rid):
        if not store.load_ro(wid):
            return self._json({"error": "not found"}, 404)
        rec = run_state.load(rid) or {}
        if rec.get("workflow_id") != wid:
            return self._json({"error": "run not found"}, 404)
        forked = run_state.fork_partial(rid)
        if not forked:
            return self._json({"error": "That run has no saved results to proceed with."}, 409)
        done_id, rest_id = forked
        if rest_id:
            workflow = store.load(wid)
            moved = False
            for t in workflow.get("tickets") or []:
                if (t.get("run_id") == done_id
                        and t.get("node_id") == rec.get("halted_at")
                        and t.get("status") in ("open", "in-progress")):
                    t["run_id"] = rest_id
                    moved = True
            if moved:
                store.save(workflow)
        return self._json({"ok": True, "run_id": done_id, "sibling": rest_id})

    def _post_ticket_user_outputs(self, wid, tid):
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        t = next((x for x in workflow.get("tickets", [])
                  if x["id"] == tid), None)
        if not t:
            return self._json({"error": "issue not found"}, 404)
        body = self._body()
        outs = {k: v for k, v in (body.get("outputs") or {}).items()
                if str(k).strip()}
        unsure = [str(u) for u in (body.get("unsure") or [])]
        rec = run_state.load(t.get("run_id") or "")
        if not rec or rec.get("status") != "halted":
            return self._json({"error": "That run can no longer be continued - it was already picked back up, or it has been cleared away."}, 409)
        run_state.record_step(t["run_id"], t.get("node_id") or "", outs,
                              trace={"output": executor.step_preview(
                                         outs, secrets_store.workflow_owner(wid)),
                                     "entered_by_user": True})
        t["user_outputs"] = {"given": sorted(outs),
                             "unsure": sorted(unsure),
                             "ts": time.time()}

        t["continued"] = True
        store.save(workflow)
        return self._json({"ok": True, "given": sorted(outs),
                           "unsure": sorted(unsure)})

    def _post_sample(self, wid):
        import base64
        from storage import blobstore
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        body = self._body()
        try:
            data = base64.b64decode(body.get("data_b64", ""), validate=True)
        except Exception:
            return self._json({"error": "data_b64 is not valid base64"}, 400)
        if not data:
            return self._json({"error": "empty file"}, 400)
        name = (body.get("name") or "sample").strip()
        mime = body.get("mime") or "application/octet-stream"

        if data[:1] in (b"{", b" ", b"\t", b"\n"):
            from agent import capture
            text = data.decode("utf-8", "replace")
            if capture.looks_like_recording(text):
                r = capture.import_recording(workflow, text, name=name)
                if r.get("ok"):
                    r["samples"] = workflow.get("samples", [])
                    return self._json(r)
                return self._json(r, 400)
        ref = blobstore.put(data, mime, {"name": name},
                            owner=secrets_store.workflow_owner(workflow["id"]))
        samples = workflow.setdefault("samples", [])
        samples[:] = [s for s in samples if s.get("name") != name]
        samples.append({"name": name, "ref": ref, "mime": mime,
                        "size": len(data), "ts": time.time()})
        store.save(workflow)
        return self._json({"name": name, "ref": ref, "size": len(data),
                           "samples": samples})

    def _post_blob(self):
        import base64
        from storage import blobstore
        body = self._body()
        try:
            data = base64.b64decode(body.get("data_b64", ""), validate=True)
        except Exception:
            return self._json({"error": "data_b64 is not valid base64"}, 400)
        if not data:
            return self._json({"error": "empty file"}, 400)

        wfl = str(body.get("workflow_id") or "")
        ref = blobstore.put(data, body.get("mime") or "application/octet-stream",
                            {"name": body.get("name", "")},
                            owner=(secrets_store.workflow_owner(wfl) if wfl
                                   else secrets_store.OWNER_APP))
        return self._json({"ref": ref, "size": len(data)})

    def _put_interaction(self, iid):
        body = self._body()

        if interactions.resolve(iid, body.get("answer")):
            return self._json({"id": iid, "answered": True})
        return self._json({"error": "unknown or expired interaction"}, 404)

    def _put_settings(self):
        body = self._body()
        settings.update(body)
        return self._json(settings.public_view())

    def _put_secret(self, name):
        body = self._body()

        secrets_store.set_secret(name, body.get("value", ""),
                                 secrets_store.OWNER_APP)

        settings.note_key_change(name, present=bool(body.get("value", "")))
        return self._json({"name": name, "set": True})

    def _put_workflow_secret(self, wid, name):
        body = self._body()
        if not store.load_ro(wid):
            return self._json({"error": "not found"}, 404)
        secrets_store.set_secret(name, body.get("value", ""),
                                 secrets_store.workflow_owner(wid))
        return self._json({"name": name, "set": True})

    def _put_environment(self, eid):
        body = self._body()
        env = environments.load(eid)
        if not env:
            return self._json({"error": "not found"}, 404)
        for k in ("name", "description", "group"):
            if k in body:
                env[k] = (body.get(k) or "").strip()
        environments.save(env)
        return self._json(env)

    def _put_environment_variable(self, eid, name):
        body = self._body()
        env = environments.load(eid)
        if not env:
            return self._json({"error": "not found"}, 404)
        name = name
        var = next((v for v in env["variables"] if v["name"] == name), None)
        if not var:
            return self._json({"error": "variable not found"}, 404)
        if var.get("secret"):
            secrets_store.set_secret(
                name, body.get("value", ""),
                secrets_store.environment_owner(env["id"]))
            var["value"] = None
            environments.save(env)
            return self._json({"name": name, "secret": True, "set": True})
        var["value"] = body.get("value")
        environments.save(env)
        return self._json({"name": name, "value": var["value"]})

    def _put_workflow_group(self, wid):
        body = self._body()
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        workflow["group"] = (body.get("group") or "").strip()
        store.save(workflow)
        return self._json({"id": wid, "group": workflow["group"]})

    def _put_workflow_meta(self, wid):
        body = self._body()

        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        if (body.get("name") or "").strip():
            workflow["name"] = body["name"].strip()
        if "description" in body:
            workflow["description"] = (body.get("description") or "").strip()
        store.save(workflow)
        return self._json({"id": wid, "name": workflow["name"],
                           "description": workflow["description"]})

    def _put_workflow_environment(self, wid):
        body = self._body()

        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        env_ids = [e for e in (body.get("environment_ids") or [])
                   if (e or "").strip()]
        for eid in env_ids:
            if not environments.load(eid):
                return self._json({"error": f"no environment {eid!r}"}, 400)
        removed = [e for e in environments.env_ids(workflow)
                   if e not in env_ids]
        workflow["environment_ids"] = env_ids

        for eid in removed:
            gone = environments.load(eid)
            if gone:
                environments.sweep_fallbacks(workflow, eid,
                                             gone.get("variables", []))
        workflow["env_bindings"] = {
            n: ref for n, ref in (workflow.get("env_bindings") or {}).items()
            if ref == "workflow"
            or (environments.parse_canonical(ref) or ("",))[0] in env_ids}
        store.save(workflow)
        return self._json({"id": wid, "environment_ids": env_ids})

    def _put_ticket(self, wid, tid):
        body = self._body()

        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        t = next((t for t in workflow.get("tickets", []) if t["id"] == tid), None)
        if not t:
            return self._json({"error": "ticket not found"}, 404)

        if body.get("continued") is True:
            t["continued"] = True

        if body.get("landed") is False:
            t["landed"] = False
        status = body.get("status")
        if status is not None:
            if status not in ("open", "in-progress", "dismissed", "closed"):
                return self._json(
                    {"error": "status must be one of open, in-progress, dismissed, closed"}, 400)
            t["status"] = status
        store.save(workflow)
        return self._json({"id": t["id"], "status": t["status"],
                           "continued": bool(t.get("continued"))})

    def _put_node_invalid_items(self, wid, nid):
        body = self._body()

        pol = str(body.get("policy") or "stop").strip().lower()
        if pol not in ("stop", "proceed", "log"):
            return self._json({"error": "policy must be stop, proceed or log"}, 400)
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        for n in workflow["nodes"]:
            if n["id"] == nid:
                cfg = n.setdefault("config", {})
                if pol == "stop":
                    cfg.pop("on_invalid_items", None)
                else:
                    cfg["on_invalid_items"] = pol
                for pn in (workflow.get("plan") or {}).get("nodes") or []:
                    if pn.get("id") == n["id"] or pn.get("name") == n.get("name"):
                        if pol == "stop":
                            pn.pop("on_invalid_items", None)
                        else:
                            pn["on_invalid_items"] = pol
                store.save(workflow)
                return self._json({"id": n["id"], "on_invalid_items": pol})
        return self._json({"error": "node not found"}, 404)

    def _put_node_may_be_empty(self, wid, nid):
        body = self._body()

        ports = [str(x) for x in (body.get("ports") or []) if x]
        if not ports:
            return self._json({"error": "ports is required"}, 400)
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        for n in workflow["nodes"]:
            if n["id"] == nid:
                marked = []
                for p in n.get("outputs") or []:
                    if p.get("name") in ports:
                        p["may_be_empty"] = True
                        if isinstance(p.get("schema"), dict):
                            p["schema"].pop("x-nonempty", None)
                        marked.append(p["name"])
                for pn in (workflow.get("plan") or {}).get("nodes") or []:
                    if pn.get("id") == n["id"] \
                            or pn.get("name") == n.get("name"):
                        for p in pn.get("outputs") or []:
                            if p.get("name") in ports:
                                p["may_be_empty"] = True
                                if isinstance(p.get("schema"), dict):
                                    p["schema"].pop("x-nonempty", None)
                store.save(workflow)
                return self._json({"id": n["id"], "may_be_empty": marked})
        return self._json({"error": "node not found"}, 404)

    def _put_node_approval(self, wid, nid):
        body = self._body()
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        for n in workflow["nodes"]:
            if n["id"] == nid:
                n["approval_suppressed"] = bool(body.get("suppressed"))
                store.save(workflow)
                return self._json({"id": n["id"], "approval_suppressed": n["approval_suppressed"]})
        return self._json({"error": "node not found"}, 404)

    def _put_workflow_variable(self, wid, name):
        body = self._body()
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        name = name
        var = next((v for v in workflow["variables"] if v["name"] == name), None)
        if not var:
            return self._json({"error": "variable not found"}, 404)
        if var.get("secret"):
            secrets_store.set_secret(name, body.get("value", ""),
                                     secrets_store.workflow_owner(workflow["id"]))
            var["value"] = None
            store.save(workflow)
            return self._json({"name": name, "secret": True, "set": True})

        val, understood = environments.type_variable_value(
            workflow, name, body.get("value"))
        label = (var.get("label") or "").strip() or name
        if not understood:
            t = environments.port_type_for(workflow, name)
            return self._json(
                {"error": f"{label} is used as a {t}, so "
                          f"{body.get('value')!r} will not work here."}, 400)
        fits, want = environments.value_fits_port(
            val, environments.port_for(workflow, name))
        if not fits:
            return self._json(
                {"error": f"{label} has to be {want}."}, 400)
        var["value"] = val
        store.save(workflow)
        return self._json({"name": name, "value": var["value"]})

    def _delete_learning(self, lid):
        from storage import learnings as _learnings
        return self._json({"id": lid,
                           "deleted": _learnings.delete(lid)})

    def _delete_secret(self, name):
        name = name
        if any(p.get("key_name") == name
               for p in settings.get().get("providers", [])):
            return self._json({"error": "a provider still uses this key"}, 409)
        secrets_store.delete_secret(name, secrets_store.OWNER_APP)
        settings.note_key_change(name, present=False)
        return self._json({"name": name, "deleted": True})

    def _delete_sample(self, wid, name):
        from urllib.parse import unquote
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        name = unquote(name)
        before = len(workflow.get("samples", []))
        workflow["samples"] = [s for s in workflow.get("samples", [])
                              if s.get("name") != name]
        if len(workflow["samples"]) == before:
            return self._json({"error": "no such material"}, 404)
        store.save(workflow)
        return self._json({"ok": True, "samples": workflow["samples"]})

    def _delete_workflow(self, wid):
        r = store.delete_workflow(wid)
        return self._json(r, 200 if r.get("ok") else 404)

    def _delete_workflow_variable(self, wid, name):
        workflow = store.load(wid)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        gone = next((v for v in workflow.get("variables", [])
                     if v.get("name") == name), None)
        if gone is None:
            return self._json({"error": "variable not found"}, 404)

        consumed = environments.consumed_names(workflow)
        winner = (environments.resolve(workflow).get(name) or {})
        if (consumed is None or name in consumed) \
                and winner.get("source") == "workflow":
            return self._json({"error": "This value is the one runs use. Switch runs to another value with this name first."}, 409)
        workflow["variables"] = [v for v in workflow.get("variables", [])
                                if v.get("name") != name]

        (workflow.get("env_bindings") or {}).pop(name, None)
        store.save(workflow)

        if gone.get("secret"):
            secrets_store.delete_secret(name, secrets_store.workflow_owner(wid))
        return self._json(workflow)

    def _delete_environment(self, eid):
        environments.delete(eid)
        return self._json({"id": eid, "deleted": True})

    def _delete_environment_variable(self, eid, name):
        env = environments.load(eid)
        if not env:
            return self._json({"error": "not found"}, 404)
        gone = next((v for v in env["variables"] if v["name"] == name),
                    None)
        if not gone:
            return self._json({"error": "variable not found"}, 404)
        env["variables"] = [v for v in env["variables"] if v["name"] != name]
        environments.save(env)
        if gone.get("secret"):
            secrets_store.delete_secret(
                gone["name"], secrets_store.environment_owner(env["id"]))

        for meta in store.list_workflows():
            if env["id"] not in environments.env_ids(meta):
                continue
            p = store.load(meta["id"])
            if p and environments.sweep_fallbacks(p, env["id"], [gone]):
                store.save(p)
        return self._json(env)

    _MAX_BODY = 64 * 1024 * 1024

    # Parses the JSON body, bounded; bad input raises instead of dropping the connection
    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > self._MAX_BODY:
            raise _BadBody(f"request body too large ({length} bytes)")
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as e:
            raise _BadBody(f"malformed JSON body: {e.msg}") from e

    # Runs one agent turn: claims the slot, streams events into the buffer, and always lands a final entry even on failure
    def _turn_stream(self, workflow: dict, kind: str, run_turn, pre=None,
                     done_extra: Optional[dict] = None) -> None:
        import turns
        pid = workflow["id"]
        if not turns.begin(pid, kind):
            return self._json({"error": "The Builder Agent is already working on this workflow - wait for the current turn to finish (it survives page reloads)."},
                              409)

        pre_items = pre() if pre else None
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        _turn_body(workflow, kind, run_turn, pre_items, done_extra,
                   self._ndjson_writer(pid))

    # One writer for every event stream: a client that went away stops the writing and is noted once, and the work carries on
    def _ndjson_writer(self, pid: str):
        from agent import chatlog
        gone = {"v": False}
        wlock = threading.Lock()
        last_write = {"ts": time.time()}

        def _write_line(ev: dict) -> None:
            if gone["v"]:
                return
            try:
                with wlock:
                    self.wfile.write((json.dumps(ev) + "\n").encode())
                    self.wfile.flush()
                last_write["ts"] = time.time()
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                gone["v"] = True
                try:
                    chatlog.append(pid, "stream-client-gone", {
                        "on_event": str(ev.get("type") or ""),
                        "idle_seconds": round(time.time() - last_write["ts"], 1),
                        "error": type(e).__name__})
                except Exception:
                    pass
        return _write_line

    # Everything the user sends: the server lands it the one right way, then answers at once
    def _say(self, workflow_id: str) -> None:
        import turns
        from agent import reply
        body = self._body()
        entry = reply.entry_from(body)
        if not entry["text"] and not entry["files"] and not entry["answers"]:
            return self._json({"error": "nothing to send"}, 400)
        out = reply.deliver(workflow_id, entry)
        if out:
            return self._json(out)
        if turns.running_kind(workflow_id):
            return self._json({"landed": "busy",
                               "kind": turns.running_kind(workflow_id)})
        workflow = store.load(workflow_id)
        if not workflow:
            return self._json({"error": "not found"}, 404)

        if not turns.begin(workflow_id, "chat"):
            if turns.interject(workflow_id, entry):
                return self._json({"landed": "parked"})

            return self._json({"landed": "busy",
                               "kind": turns.running_kind(workflow_id)})
        try:
            drive, items = reply.land(workflow, entry)
            store.save(workflow)
        except Exception as e:
            turns.finish(workflow_id, str(e))
            raise
        if not drive:
            turns.finish(workflow_id, None)
            return self._json({"landed": "noop"})

        from agent import opening
        question = opening.owed(workflow)
        if question is not None:
            items += opening.raise_card(workflow, question)
            turns.finish(workflow_id, None)
            return self._json({"landed": "asked", "items": items})
        _spawn_turn(workflow, "chat",
                    lambda emit: orchestrator.handle_chat_stream(
                        workflow, drive, emit),
                    items)

        return self._json({"landed": "started", "items": items})

    # The Fix click: one agent turn with the failing run's evidence attached
    def _investigate(self, workflow_id: str, ticket_id: str) -> None:
        workflow = store.load(workflow_id)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        ticket = next((t for t in workflow.get("tickets", []) if t["id"] == ticket_id), None)
        if not ticket:
            return self._json({"error": "ticket not found"}, 404)
        node = next((n for n in workflow["nodes"] if n["id"] == ticket.get("node_id")), None)

        def pre():
            line = (f"Investigate issue at "
                    f"\"{(node or {}).get('name', ticket.get('node_id'))}\": "
                    f"{ticket.get('reason')}")
            item = transcript.append_message(workflow, "user", line)
            store.save(workflow)
            return [item] if item else []

        self._turn_stream(workflow, "investigate",
                          lambda emit: orchestrator.investigate(workflow, ticket, emit),
                          pre=pre)

    # The user edits an AI step's prompt from the drawer, or resets it to the agent's last version
    def _node_prompt(self, workflow_id: str, node_id: str) -> None:
        import turns
        body = self._body()
        if not turns.begin(workflow_id, "prompt-edit"):
            return self._json({"error": "the Builder Agent is working on this workflow - try again when it finishes"}, 409)
        try:
            workflow = store.load(workflow_id)
            if not workflow:
                return self._json({"error": "not found"}, 404)
            node = next((n for n in workflow.get("nodes", [])
                         if n["id"] == node_id), None)
            if not node:
                return self._json({"error": "node not found"}, 404)
            if node.get("type") != "ai":
                return self._json({"error": "only an AI step has a prompt"}, 400)
            cfg = node.setdefault("config", {})
            name = node.get("name", node_id)
            if body.get("reset"):
                base = cfg.get("prompt_agent")
                if not base:
                    return self._json({"error": "the Builder Agent's version of this prompt is not recorded"}, 400)
                if base == cfg.get("prompt"):
                    return self._json({"error": "the prompt already matches the Builder Agent's version"}, 400)
                cfg["prompt"] = base
                transcript.append_message(
                    workflow, "user",
                    f'Reset the prompt on "{name}" to the Builder Agent\'s version')
                store.save(workflow)

                store.record_version(workflow, f"prompt-reset::{name}")
                return self._json({"ok": True})
            raw = str(body.get("prompt") or "")
            if not raw.strip():
                return self._json({"error": "the prompt cannot be empty"}, 400)
            if raw == cfg.get("prompt"):
                return self._json({"error": "that is already this step's prompt"}, 400)
            if not cfg.get("prompt_agent"):
                cfg["prompt_agent"] = cfg.get("prompt", "")
            cfg["prompt"] = raw
            transcript.append_message(workflow, "user",
                                      f'Edited the prompt on "{name}"')
            store.save(workflow)
            store.record_version(workflow, f"prompt-edit::{name}")
            return self._json({"ok": True})
        finally:
            turns.finish(workflow_id)

    # Changing a step's model applies it and records the change; nothing is reviewed
    def _node_model(self, workflow_id: str, node_id: str) -> None:
        import providers
        import turns
        if not turns.begin(workflow_id, "model-change"):
            return self._json({"error": "the Builder Agent is working on this workflow - try again when it finishes"}, 409)
        try:
            workflow = store.load(workflow_id)
            if not workflow:
                return self._json({"error": "not found"}, 404)
            node = next((n for n in workflow.get("nodes", [])
                         if n["id"] == node_id), None)
            if not node:
                return self._json({"error": "node not found"}, 404)
            if node.get("type") != "ai":
                return self._json({"error": "only an AI step has a model"}, 400)
            body = self._body()
            cfg = node.get("config", {}) or {}
            old_ref = cfg.get("model") or {}

            changing_model = "model" in body
            if changing_model:
                model = str(body.get("model") or "").strip()
                provider_id = str(body.get("provider_id") or "").strip()
            else:
                model = str(old_ref.get("model") or "").strip()
                provider_id = str(old_ref.get("provider_id") or "").strip()
                if not model:
                    return self._json({"error": "pick a model first"}, 400)
            if not model or not providers.is_ready({"model": model,
                                                    "provider_id": provider_id}):
                return self._json({"error": "pick one of the ready workflow models"}, 400)
            row = providers.find_model(model, for_node=True, provider_id=provider_id)[1]
            temperature = float(old_ref.get("temperature") or 0.0)
            if "temperature" in body:
                if row.get("no_temperature"):
                    return self._json({"error": "this model takes no temperature"}, 400)
                try:
                    t = float(body.get("temperature"))
                except (TypeError, ValueError):
                    return self._json({"error": "the temperature is a number between 0 and 1"}, 400)
                if not 0.0 <= t <= 1.0:
                    return self._json({"error": "the temperature is a number between 0 and 1"}, 400)
                temperature = t
            max_tokens = cfg.get("max_tokens")
            if "max_tokens" in body:
                raw = body.get("max_tokens")
                if raw in ("", None, 0, "0"):
                    max_tokens = None
                else:
                    try:
                        n = int(raw)
                    except (TypeError, ValueError):
                        n = 0
                    if n <= 0:
                        return self._json({"error": "the token limit is a whole number above zero, or blank for the standard"}, 400)
                    max_tokens = None if n == config.AI_MAX_TOKENS else n

            kinds = (cfg.get("sends_file") or {}).get("kinds") or []
            if changing_model and kinds:
                from runtime import env_checks
                f = providers.model_facts({"model": model, "provider_id": provider_id})
                for kind in kinds:
                    if kind in ("pdf", "image") and not f.get(f"reads_{kind}"):
                        return self._json({"error": env_checks.file_unsupported_sentence(
                            kind, f, step=node.get("name") or node_id)}, 400)
            old = old_ref.get("model", "") or "(not set)"
            if changing_model and model == old \
                    and provider_id == (old_ref.get("provider_id") or ""):
                return self._json({"error": "that is already this step's model"}, 400)
            r = steps.set_prompt_model(
                workflow, node_id, cfg.get("prompt", ""), model, temperature,
                provider_id=provider_id)
            if not r.get("ok"):
                return self._json({"error": r.get("error", "could not apply")}, 400)
            node_cfg = node.setdefault("config", {})
            if max_tokens is None:
                node_cfg.pop("max_tokens", None)
            else:
                node_cfg["max_tokens"] = max_tokens

            plan_step = next((s for s in (workflow.get("plan") or {}).get("nodes") or []
                              if s.get("id") == node_id or s.get("name") == node.get("name")), None)
            if plan_step is not None:
                plan_step["model"] = model
                plan_step["provider"] = provider_id
                plan_step["temperature"] = temperature
                if max_tokens is None:
                    plan_step.pop("max_tokens", None)
                else:
                    plan_step["max_tokens"] = max_tokens
            name = node.get("name", node_id)
            changes = []
            if changing_model:
                shown = model
                if provider_id:
                    shown += f" ({providers.find_model(model, for_node=True, provider_id=provider_id)[0].get('name') or provider_id})"
                changes.append(f'Changed the AI model on "{name}" from {old} to {shown}')
            if "temperature" in body:
                changes.append(f'Set the temperature on "{name}" to {temperature:g}')
            if "max_tokens" in body:
                changes.append(f'Set the token limit on "{name}" to '
                               + (f"{max_tokens:,}" if max_tokens else "the standard"))
            if not changes:
                return self._json({"error": "nothing to change"}, 400)
            transcript.append_message(workflow, "user", ". ".join(changes))
            store.save(workflow)
            store.record_version(workflow, f"model-change::{name}")
            return self._json({"ok": True})
        finally:
            turns.finish(workflow_id)

    # Stops the named turn or run at its next safe point
    def _stop(self, workflow_id: str) -> None:
        import turns
        body = self._body()
        stopped = turns.request_stop(
            workflow_id, str(body.get("turn_id") or "") or None)
        self._json({"stopped": stopped})

    # Starts or resumes a workflow run; setup problems are checked before any step executes
    def _run(self, workflow_id: str) -> None:
        import turns
        workflow = store.load(workflow_id)
        if not workflow:
            return self._json({"error": "not found"}, 404)
        body = self._body()

        if body.get("run_id"):
            rec = run_state.load(str(body["run_id"])) or {}
            if not rec or rec.get("workflow_id") != workflow_id:
                return self._json({"error": "run not found"}, 404)
        if not turns.begin(workflow_id, "run"):
            return self._json({"error": "The Builder Agent is already working on this workflow - wait for it to finish, or stop it first."}, 409)

        res = environments.resolve(workflow)
        orphans = {n for n in environments.workflow_variable_names(workflow)
                   if n not in res}
        if environments.ensure_fallback_vars(workflow, orphans):
            store.save(workflow)

        if store.persist_entry_values(workflow, body.get("entry_inputs") or {}):
            store.save(workflow)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        from agent import chatlog
        chatlog.append(workflow_id, "run-start",
                       {"resume": bool(body.get("run_id"))})

        _write_line = self._ndjson_writer(workflow_id)

        def emit(ev: dict) -> None:
            ev.setdefault("ts", round(time.time(), 3))
            chatlog.event(workflow_id, ev)
            turns.record(workflow_id, ev)
            _write_line(ev)

        emit({"type": "turn", "turn_id": turns.current_turn_id(workflow_id)})

        try:
            for nid, outs in (body.get("step_outputs") or {}).items():
                run_state.record_step(body.get("run_id") or "", str(nid), outs,
                                      trace={"output": executor.step_preview(
                                                 outs, secrets_store.workflow_owner(workflow_id)),
                                             "entered_by_user": True})
            for nid in (body.get("unfire") or []):
                run_state.reset_step(body.get("run_id") or "", str(nid))
            try:
                result = executor.run_workflow(
                    workflow, body.get("entry_inputs") or {},
                    run_id=body.get("run_id") or None,
                    approvals=set(body.get("approvals") or []), emit=emit,
                    should_stop=lambda: turns.should_stop(workflow_id),

                    secret_inputs=body.get("secret_inputs") or None,

                    run_values=extensions.first("run_values", workflow),
                    on_user_request=_window_request(workflow_id, emit))
            except NotImplementedError as e:
                result = {"status": "error", "reason": f"not implemented yet: {e}"}
            except Exception as e:
                result = {"status": "error", "reason": f"{type(e).__name__}: {e}"}

            workflow = issues.record_run_outcome(workflow_id, workflow, result)
            emit({"type": "done", "result": result, "workflow": workflow})
        finally:
            turns.finish(workflow_id)

    def log_message(self, *args) -> None:
        pass

# Checks whether another instance of the app already answers on this port
def _already_running(url: str) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{url}/api/settings", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False

# The port occupant's fingerprint; too old to answer is itself proof of staleness
def _occupant_build(url: str) -> Optional[str]:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{url}/api/build", timeout=2) as resp:
            return json.loads(resp.read()).get("build")
    except Exception:
        return None

# Asks an older instance holding the port to shut down, so starting is always safe
def _replace_stale(url: str, port: int) -> bool:
    import signal
    import subprocess
    import urllib.request
    try:
        urllib.request.urlopen(f"{url}/api/shutdown", data=b"{}", timeout=2)
    except Exception:
        try:
            out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                                 capture_output=True, text=True, timeout=5).stdout
            for pid in out.split():
                os.kill(int(pid), signal.SIGTERM)
        except Exception:
            return False
    for _ in range(20):
        time.sleep(0.25)
        if not _already_running(url):
            return True
    return False

# The browser may close a connection while an answer is on its way; that is not an error worth a traceback
class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc = sys.exception()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError,
                            ConnectionAbortedError)):
            return
        import traceback
        print(f"Cryogram could not answer a request: {type(exc).__name__}: {exc}",
              flush=True)
        traceback.print_exc()

# Binds the port, or reports who holds it
def _bind(host: str, port: int) -> Optional[ThreadingHTTPServer]:
    try:
        return _Server((host, port), Handler)
    except OSError as e:
        if e.errno not in (48, 98):
            raise
        return None

_CONSOLE_HELP = ("Commands:  /restart - reload from disk (picks up code changes)  ·  /quit - stop the server  ·  /help - this list  (Ctrl-C also stops)")

# A typed console line to an action, or None to ignore
def _console_action(line: str) -> Optional[str]:
    word = line.strip().lstrip("/").lower()
    if word in ("restart", "reload", "r"):
        return "restart"
    if word in ("quit", "exit", "stop", "q"):
        return "quit"
    if word in ("help", "commands", "h", "?"):
        return "help"
    return None

# The terminal window's own little prompt: open the app, or quit
def _run_console(url: str) -> None:
    for line in sys.stdin:
        action = _console_action(line)
        if action == "help":
            print(_CONSOLE_HELP, flush=True)
        elif action == "quit":
            print("Stopping Cryogram...", flush=True)
            if _server is not None:
                _server.shutdown()
            return
        elif action == "restart":
            print("Restarting Cryogram (reloading from disk)...", flush=True)
            os.environ["CRYOGRAM_RESTART"] = "1"
            os.execv(sys.executable, [sys.executable, *sys.argv])
        elif line.strip():
            print(f"Unknown command {line.strip()!r}. {_CONSOLE_HELP}", flush=True)

# Startup: bind the port (replacing a stale instance), run the pending data moves, then open the browser
def main() -> None:
    global _server
    srv = settings.get()["server"]

    host, port = srv["host"], int(os.environ.get("PORT", srv["port"]))
    url = f"http://{host}:{port}"

    server = _bind(host, port)
    if server is None and _already_running(url):
        if _occupant_build(url) == BUILD_ID:
            print(f"Cryogram is already running at {url} - opening it.")
            if os.environ.get("CRYOGRAM_NO_BROWSER") != "1":
                webbrowser.open(url)
            return
        print(f"A Cryogram from an older build is running at {url} - replacing it.")
        if _replace_stale(url, port):
            server = _bind(host, port)
    if server is None:
        print(f"Port {port} is taken by another program. Free it or change the "
              f"port in Admin > Config, then start again.")
        print(f"  To see what holds it:  lsof -nP -iTCP:{port} -sTCP:LISTEN")
        raise SystemExit(1)
    _server = server
    from storage import lift
    from storage import seed
    seed.run()
    lift.run_pending()
    extensions.fire("start")
    store.recover_stuck_plans()
    from storage import blobstore
    try:
        swept = blobstore.sweep_orphans()
        if swept:
            print(f"Removed {swept} stored file{'s' if swept != 1 else ''} nothing uses any more.")
    except Exception:
        pass
    run_state.sweep_stale()
    corpus.sweep_tmp_rows()

    interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    hint = " · type /restart to reload · /help" if interactive else ""
    print(f"Cryogram running at {url}  ·  started "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')}  (Ctrl-C to stop{hint})")
    if interactive:
        threading.Thread(target=_run_console, args=(url,), daemon=True).start()

    if (os.environ.get("CRYOGRAM_NO_BROWSER") != "1"
            and os.environ.get("CRYOGRAM_RESTART") != "1"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

if __name__ == "__main__":
    main()
