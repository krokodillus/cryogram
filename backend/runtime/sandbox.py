# Runs a step's code in a fresh subprocess; the only helpers available to it are the audited ones in capability.py
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import config
from storage import settings

_RUNNER = Path(__file__).resolve().parent / "_sandbox_runner.py"
SENTINEL = "__CRYO_RESULT__"
# The timeout counts seconds without a progress report, not total runtime, so a step that keeps reporting can run as long as it needs
DEFAULT_TIMEOUT = 120
# A silent step is asked to report what it saw before it is killed; this is how long it gets to answer
KILL_GRACE = 20
STDERR_KEEP_CHARS = 500
MIN_TIMEOUT = 10

# The folders a workflow's steps may read and write: declared on its steps, allowed on a card, or picked as a folder setting
def workflow_roots(workflow: dict, step_paths: Optional[list] = None) -> list[str]:
    from storage import environments as _envs
    raw: list = list(step_paths or []) + list(workflow.get("path_allowlist") or [])
    for v in workflow.get("variables") or []:
        if _envs.setting_type(v.get("type") or "") == "folder":
            raw.append(v.get("value"))
    try:
        for e in _envs.attached(workflow):
            for v in e.get("variables") or []:
                if _envs.setting_type(v.get("type") or "") == "folder":
                    raw.append(v.get("value"))
    except Exception:
        pass
    out = set()
    for r in raw:
        r = os.path.expanduser(str(r or "").strip())
        if r and os.path.isabs(r):
            out.add(os.path.abspath(r))
    return sorted(out)

# Bounds a step's declared timeout; an unusable value falls back to the default instead of failing the run
def clamp_timeout(value) -> Optional[int]:
    try:
        t = int(value)
    except (TypeError, ValueError):
        return None
    return max(MIN_TIMEOUT, t) if t > 0 else None

# The child process receives only these variables, never a copy of os.environ, so shell-exported keys cannot reach step code
_ENV_ALLOWLIST = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP",
    "PYTHONIOENCODING", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "WINDIR",

    "CRYOGRAM_DATA_DIR",

    "CRYOGRAM_CARD_PATIENCE",
)

# A step failure crosses the process boundary with its error type, so the run can report the specific problem
class NodeError(RuntimeError):
    def __init__(self, kind: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.detail = detail or {}

# Pressing Stop ends the step's process; the run records it as stopped by the user, not as a failure
class NodeStopped(RuntimeError):
    sent = False

# Stops the child and everything it started: a process group on Unix, the whole tree on Windows
def _stop_tree(proc, grace: float) -> None:
    import signal
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        proc.wait()
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        proc.wait()
        return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()

# A progress note longer than this is shortened, with a marker showing it was shortened
HEARTBEAT_NOTE_CAP = 160

# Runs one step's code in isolation and returns its output; raises when the step fails or is stopped
def run(code: str, entry: Optional[str], inputs: dict, secret_values: dict,
        python_exe: Optional[str] = None,
        extra_allowlist: Optional[list] = None, network: bool = True,
        should_stop=None,
        rehearse: bool = False, timeout: Optional[int] = None,
        on_progress=None,
        profile_dir: Optional[str] = None,
        checkpoint_file: Optional[str] = None,
        blob_owner: Optional[str] = None,
        path_roots: Optional[list] = None,
        on_user_request=None,
        keep_captures: bool = False,
        report: Optional[dict] = None,
        browser: bool = False, browser_url: str = "",
        browser_options: Optional[dict] = None,
        output_ports: Optional[list] = None) -> Any:
    s = settings.get()["sandbox"]

    allow = sorted(set(s.get("egress_allowlist", []) or [])
                   | set(extra_allowlist or [])) if network else []
    return _run_subprocess(code, entry, inputs, secret_values or {},
                           egress_mode=s.get("egress_mode", "allowlist"),
                           egress_allowlist=allow,
                           timeout=timeout or DEFAULT_TIMEOUT,
                           python_exe=python_exe,
                           should_stop=should_stop, rehearse=rehearse,
                           on_progress=on_progress,
                           profile_dir=profile_dir,
                           checkpoint_file=checkpoint_file,
                           blob_owner=blob_owner, path_roots=path_roots,
                           on_user_request=on_user_request,
                           keep_captures=keep_captures, report=report, browser=browser,
                           browser_url=browser_url, browser_options=browser_options,
                           output_ports=output_ports)

# Starts the child process, sends it the job on stdin, and watches it for progress, stop requests and the timeout
def _run_subprocess(code: str, entry: Optional[str], inputs: dict, secret_values: dict,
                    egress_mode: str = "open", egress_allowlist: Optional[list] = None,
                    timeout: int = DEFAULT_TIMEOUT,
                    python_exe: Optional[str] = None,
                    should_stop=None,
                    rehearse: bool = False,
                    on_progress=None, profile_dir: Optional[str] = None,
                    checkpoint_file: Optional[str] = None,
                    blob_owner: Optional[str] = None,
                    path_roots: Optional[list] = None,
                    on_user_request=None,
                    keep_captures: bool = False,
                    report: Optional[dict] = None,
                    browser: bool = False, browser_url: str = "",
                    browser_options: Optional[dict] = None,
                    output_ports: Optional[list] = None) -> Any:
    # rehearse=True makes the child intercept every outbound connection before it opens, proving a sending step's path without sending
    harness_roots = [str(config.BLOBS_DIR), str(config.DATA_DIR / "sandbox_tmp")]
    readonly_roots = [str(_RUNNER.parent.parent)]

    app_site = config.app_site_packages() if browser else ""
    if app_site:
        readonly_roots.append(app_site)
    if profile_dir:
        harness_roots.append(str(profile_dir))
    if checkpoint_file:
        harness_roots.append(str(checkpoint_file))
    job = json.dumps({"code": code, "entry": entry, "inputs": inputs,
                      "secret_names": list(secret_values),
                      "egress_mode": egress_mode, "egress_allowlist": egress_allowlist or [],
                      "rehearse": bool(rehearse),
                      "profile_dir": profile_dir,
                      "blob_owner": blob_owner,
                      "keep_captures": bool(keep_captures),

                      "browser": bool(browser),
                      "app_site": app_site,
                      "browser_url": str(browser_url or ""),

                      "browser_options": dict(browser_options or {}),
                      "output_ports": list(output_ports or []),
                      "path_roots": list(path_roots or []),
                      "harness_roots": harness_roots,
                      "readonly_roots": readonly_roots,
                      "data_dir": str(config.DATA_DIR)})

    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env.update({k: str(v) for k, v in secret_values.items() if v is not None})
    _inject_cert_bundle(env)
    # Step code runs in a scratch folder under the data dir, never in the app's own source tree
    import shutil as _shutil
    import tempfile as _tempfile
    base = config.DATA_DIR / "sandbox_tmp"
    base.mkdir(parents=True, exist_ok=True)
    workdir = Path(_tempfile.mkdtemp(prefix="step_", dir=str(base)))
    env["PYTHONPATH"] = str(_RUNNER.parent.parent)

    # The parent checks for a stop request five times a second, so Stop takes effect within 200ms even during a network call
    import tempfile
    import time as _time

    hb_fd, hb_path = tempfile.mkstemp(prefix="hb_", dir=str(workdir))
    os.close(hb_fd)
    env["CRYOGRAM_HEARTBEAT_FILE"] = hb_path
    # A browser step can ask the person at the machine to do something in the window; the request comes out on this file and the answer goes back beside it
    req_path = ""
    if on_user_request:
        req_path = os.path.join(str(workdir), "request")
        env["CRYOGRAM_REQUEST_FILE"] = req_path
    # Each finished item is written to a checkpoint file at once, so a step that fails halfway does not repeat the items already sent
    if checkpoint_file:
        env["CRYOGRAM_CHECKPOINT_FILE"] = str(checkpoint_file)
    sent_path = str(workdir / "cryogram-sent")
    env["CRYOGRAM_SENT_FILE"] = sent_path
    sent = False
    try:
        with tempfile.TemporaryFile(mode="w+", dir=str(workdir)) as out_f, \
             tempfile.TemporaryFile(mode="w+", dir=str(workdir)) as err_f:
            proc = subprocess.Popen(
                [python_exe or sys.executable, str(_RUNNER)],
                stdin=subprocess.PIPE, stdout=out_f, stderr=err_f,
                env=env, cwd=str(workdir), text=True,

                **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                   if os.name == "nt" else {"start_new_session": True}))
            try:
                proc.stdin.write(job)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            started = _time.time()
            last_note = ""
            silence = 0
            while proc.poll() is None:
                if should_stop and should_stop():
                    _stop_tree(proc, 2)
                    raise NodeStopped("stopped by the user mid-step")
                last_beat = started
                try:
                    last_beat = max(started, os.path.getmtime(hb_path))
                except OSError:
                    pass
                # A long step reports its own progress; secret values are removed from a note before it leaves the process
                if on_progress:
                    try:
                        with open(hb_path) as hf:
                            note = hf.read(HEARTBEAT_NOTE_CAP + 1).strip()
                        if len(note) > HEARTBEAT_NOTE_CAP:
                            note = note[:HEARTBEAT_NOTE_CAP] + "..."
                        if note and note != last_note:
                            last_note = note
                            for v in secret_values.values():
                                if v:
                                    note = note.replace(str(v), "****")
                            on_progress(note)
                    except OSError:
                        pass
                # A step waiting for the person is not a step gone quiet: the silence limit stands down while a request is open
                if req_path and os.path.exists(req_path):
                    try:
                        with open(req_path) as rf:
                            message = rf.read(1000).strip()
                    except OSError:
                        message = ""
                    outcome = on_user_request(message)
                    if outcome == "stopped":
                        _stop_tree(proc, 2)
                        raise NodeStopped("stopped by the user mid-step")
                    with open(f"{req_path}.answer", "w") as af:
                        af.write("done" if outcome == "answered" else "gave-up")
                    try:
                        os.remove(req_path)
                    except OSError:
                        pass

                    started = _time.time()
                    os.utime(hb_path, None)
                    continue
                if _time.time() - last_beat > timeout:
                    _report_then_kill(proc)
                    silence = timeout
                    break
                _time.sleep(0.2)
            out_f.seek(0)
            err_f.seek(0)
            stdout_text, stderr_text = out_f.read(), err_f.read()
    except NodeStopped as e:
        e.sent = os.path.exists(sent_path)
        raise
    finally:
        sent = os.path.exists(sent_path)
        try:
            os.unlink(hb_path)
        except OSError:
            pass
        _shutil.rmtree(str(workdir), ignore_errors=True)

    line = next((ln for ln in stdout_text.splitlines() if ln.startswith(SENTINEL)), None)
    res = json.loads(line[len(SENTINEL):]) if line else None
    if report is not None:
        report["sent"] = sent
        if isinstance(res, dict) and isinstance(res.get("calls"), dict):
            report["calls"] = res["calls"]
    if silence:
        detail = {k: res.get(k) for k in ("saw", "written", "files")
                  if res and res.get(k) is not None}
        detail["timeout_seconds"] = silence
        detail["sent"] = sent
        raise NodeError("NoProgress",
                        f"no sign of progress for {silence} seconds - a long "
                        "step calls heartbeat() between items, or declares timeout_seconds when one call genuinely takes longer",
                        detail=detail)
    if res is None:
        stderr = _scrub(stderr_text, secret_values)
        if len(stderr) > STDERR_KEEP_CHARS:
            stderr = stderr[:STDERR_KEEP_CHARS] + "…"
        raise NodeError("Crashed", f"sandbox: no result (exit {proc.returncode}). {stderr}",
                        detail={"sent": sent})
    if not res.get("ok"):
        raise NodeError(res.get("error_kind") or "Exception", str(res.get("error")),
                        detail={**{k: res.get(k) for k in ("found", "expected", "saw",
                                                           "written", "files",
                                                           "window_opened",

                                                           "$capture")
                                   if res.get(k) is not None}, "sent": sent})
    return res["output"]

# Asks the child to write its failure report, then kills it and everything it started once the grace is up
def _report_then_kill(proc) -> None:
    _stop_tree(proc, KILL_GRACE)

# Some Python installs come without CA certificates; the child is pointed at a bundle so HTTPS requests work
def _inject_cert_bundle(env: dict) -> None:
    if env.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
        env["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        pass

# Secret values are removed from any text before it leaves the child process
def _scrub(text: str, secret_values: dict) -> str:
    for v in secret_values.values():
        if v:
            text = text.replace(str(v), "***")
    return text
