# Subprocess entry point: one step's code, capability surface only; secret values arrive via env, never argv
import json
import os
import re
import signal
import sys

from runtime import capability

SENTINEL = "__CRYO_RESULT__"
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}

# In rehearse mode every attempt to open a connection raises a marker instead, so nothing is sent
def _install_rehearsal_guard() -> None:
    import socket

    def stop(*_a, **_k):
        raise PermissionError(
            "rehearsal-stop: outbound call intercepted at the network boundary (build receipt - nothing was sent)")

    socket.getaddrinfo = stop
    socket.create_connection = stop
    socket.socket.connect = stop
    socket.socket.connect_ex = stop

# Outbound connections are checked against the allowed hosts; anything else is refused with the host named
def _install_egress_guard(allowlist: list) -> None:
    import socket

    allowed = {a.lower() for a in (allowlist or [])} | _LOOPBACK

    def ok(host) -> bool:
        h = str(host or "").lower()
        return h in _LOOPBACK or any(h == a or h.endswith("." + a) for a in allowed)

    real_getaddrinfo, real_create = socket.getaddrinfo, socket.create_connection
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    approved_ips: set = set()

    def guarded_getaddrinfo(host, *a, **k):
        if not ok(host):
            raise PermissionError(f"egress blocked: {host!r} is not on the allowlist")
        res = real_getaddrinfo(host, *a, **k)
        for entry in res:
            addr = entry[4]
            if isinstance(addr, tuple) and addr:
                approved_ips.add(str(addr[0]).lower())
        return res

    def guarded_create_connection(address, *a, **k):
        if not ok(address[0] if address else ""):
            raise PermissionError(f"egress blocked: {address!r} is not on the allowlist")
        return real_create(address, *a, **k)

    def _addr_ok(address) -> bool:
        if not (isinstance(address, tuple) and address):
            return True
        h = str(address[0] or "").lower()
        return ok(h) or h in approved_ips

    def guarded_connect(self, address):
        if not _addr_ok(address):
            raise PermissionError(f"egress blocked: {address!r} is not on the allowlist")
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        if not _addr_ok(address):
            raise PermissionError(f"egress blocked: {address!r} is not on the allowlist")
        return real_connect_ex(self, address)

    socket.getaddrinfo = guarded_getaddrinfo
    socket.create_connection = guarded_create_connection
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex

TRAFFIC_KEEP = 10
TRAFFIC_BODY_CHARS = 4_000
_TRAFFIC: list = []

CALL_HOSTS_KEPT = 5
_CALLS: dict = {"total": 0, "ok": 0, "refused": 0, "statuses": {}, "hosts": [], "groups": {}}

def _call_group(method: str, host: str, url: str) -> str:
    path = str(url).split("?", 1)[0]
    if "://" in path:
        path = "/" + path.split("://", 1)[1].split("/", 1)[-1] if "/" in path.split("://", 1)[1] else "/"
    return f"{str(method).upper()} {host}{re.sub(r'\d+', '#', path)}"

# Records the last outbound HTTP exchanges so a failure can show what the service actually said
def _install_traffic_log() -> None:
    import http.client as hc
    real_putrequest = hc.HTTPConnection.putrequest
    real_putheader = hc.HTTPConnection.putheader
    real_getresponse = hc.HTTPConnection.getresponse

    def putrequest(self, method, url, *a, **kw):
        host = str(getattr(self, "host", ""))
        _TRAFFIC.append({"method": str(method), "host": host,
                         "path": str(url)[:300], "request_headers": {},
                         "status": None, "reason": "", "response_head": ""})
        del _TRAFFIC[:-TRAFFIC_KEEP]
        key = _call_group(method, host, url)
        self._cryogram_group = key
        g = _CALLS["groups"].setdefault(key, {"method": str(method).upper(), "host": host,
                                              "sent": 0, "ok": 0, "statuses": {}})
        g["sent"] += 1
        return real_putrequest(self, method, url, *a, **kw)

    def putheader(self, header, *values):
        if _TRAFFIC:
            name = str(header)
            low = name.lower()

            val = "(sent)" if any(k in low for k in ("auth", "cookie", "token", "key", "secret")) \
                else " ".join(str(v) for v in values)[:200]
            _TRAFFIC[-1]["request_headers"][name] = val
        return real_putheader(self, header, *values)

    def getresponse(self, *a, **kw):
        resp = real_getresponse(self, *a, **kw)
        status = getattr(resp, "status", None)
        if isinstance(status, int):
            _CALLS["total"] += 1
            if 200 <= status < 300:
                _CALLS["ok"] += 1
            else:
                _CALLS["refused"] += 1
            key = str(status)
            _CALLS["statuses"][key] = _CALLS["statuses"].get(key, 0) + 1
            g = _CALLS["groups"].get(getattr(self, "_cryogram_group", ""))
            if g is not None:
                if 200 <= status < 300:
                    g["ok"] += 1
                g["statuses"][key] = g["statuses"].get(key, 0) + 1
            host = str(getattr(self, "host", "") or "")
            if host and host not in _CALLS["hosts"] and len(_CALLS["hosts"]) < CALL_HOSTS_KEPT:
                _CALLS["hosts"].append(host)
        if _TRAFFIC:
            entry = _TRAFFIC[-1]
            entry["status"] = status
            entry["reason"] = str(getattr(resp, "reason", "") or "")
            try:
                entry["response_headers"] = {k: (v[:200]) for k, v in resp.getheaders()
                                             if k.lower() not in ("set-cookie",)}
            except Exception:
                pass
            real_read = resp.read

            def read(amt=None):
                data = real_read(amt)
                if len(entry["response_head"]) < TRAFFIC_BODY_CHARS and data:
                    entry["response_head"] += data[:TRAFFIC_BODY_CHARS].decode("utf-8", "replace")
                    entry["response_head"] = entry["response_head"][:TRAFFIC_BODY_CHARS]
                return data
            resp.read = read
        return resp

    hc.HTTPConnection.putrequest = putrequest
    hc.HTTPConnection.putheader = putheader
    hc.HTTPConnection.getresponse = getresponse

CAPTURE_SECONDS = 15

def _looks_like_page(v) -> bool:
    return (not isinstance(v, type) and callable(getattr(v, "evaluate", None))
            and callable(getattr(v, "screenshot", None)) and hasattr(v, "url"))

# The page a browser step was on when it failed: its address, the whole page and a screenshot, stored as files
def _capture_page(ns: dict) -> dict:
    import threading
    pages = [v for v in list(ns.values()) if _looks_like_page(v)]
    if not pages:
        return {}
    page = pages[-1]
    out: dict = {}

    def work():
        try:
            out["url"] = str(page.url)
        except Exception:
            pass
        try:
            html = capability._page_html(page)
            out["page"] = capability.write_file("page-at-failure.html", html.encode("utf-8"))
            try:
                import re as _re
                m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.I | _re.S)
                if m:
                    out["title"] = m.group(1).strip()[:200]
            except Exception:
                pass
        except Exception:
            pass
        try:
            png = page.screenshot(timeout=5000)
            out["screenshot"] = capability.write_file("page-at-failure.png", png)
        except Exception:
            pass

        try:
            page.context.close()
        except Exception:
            pass
        ref = _store_har()
        if ref:
            out["har"] = ref

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(CAPTURE_SECONDS)
    return out

# The pages this step kept, stored with how much of each one had loaded
def _store_snapshots() -> list:
    out = []
    for snap in capability.kept_snapshots():
        try:
            with open(snap["path"], "rb") as f:
                entry = {"url": snap.get("url") or "",
                         "ref": capability.write_file("page.html", f.read())}
        except Exception:
            continue
        for k in ("bytes", "seen", "height", "elements", "links"):
            if snap.get(k) is not None:
                entry[k] = snap[k]
        out.append(entry)
    return out

# What a window kept, listed and stored - for a build cell, whose recording is the evidence
def _stored_captures() -> dict:
    cap: dict = {}
    har = _store_har()
    if har:
        cap["har"] = har
    pages = _store_snapshots()
    if pages:
        cap["pages"] = pages
        cap["snapshot"] = pages[-1]["ref"]
    seen = capability.browser_calls()
    if seen["total"]:
        cap.update(_listed_calls(seen))
    return cap

# Every call the window made: folded inside the result, whole in a file of its own
def _listed_calls(seen: dict) -> dict:
    whole = [{"id": i, **c} for i, c in enumerate(seen["calls"])]
    out: dict = {"calls": capability.fold_calls(seen["calls"]),
                 "calls_total": seen["total"]}
    try:
        out["calls_file"] = capability.write_file(
            "calls.json", json.dumps(whole, indent=1, default=str).encode())
    except Exception:
        pass
    return out

# Keeps the browser's own recording of the failed run, masked, once Chrome has flushed it
def _store_har() -> str:
    from runtime import scrub
    path = capability.har_recording()
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as f:
            clean = scrub.har_bytes(f.read())
        if clean is None:
            return ""
        return capability.write_file("browser-traffic.har", clean)
    except Exception:
        return ""

_NS: dict = {}
_SECRET_VALUES: list = []

# The failure report: the error, what the step saw, and what it had written so far
def _failure_payload(error: str, kind: str) -> dict:
    payload = {"ok": False, "error": error, "error_kind": kind}
    if _CALLS["total"]:
        payload["calls"] = dict(_CALLS)
    try:
        saw = _saw(_NS)
        if saw:
            payload["saw"] = saw
    except Exception:
        pass
    try:
        written = capability.collected_outputs()
        if written:
            payload["written"] = written
    except Exception:
        pass
    try:
        files = capability.collected_files()
        if files:
            payload["files"] = files
    except Exception:
        pass
    try:
        if capability.window_opened():
            payload["window_opened"] = True

            cap = _stored_captures()
            if cap:
                payload["$capture"] = cap
    except Exception:
        pass
    return payload

# Keeps the window's recording when the declared outputs are wrong, before the window closes
def _keep_if_the_outputs_are_wrong(ports: list, out) -> None:
    if not ports or not isinstance(out, dict):
        return
    try:
        from runtime import port_checks
        if not port_checks.check_ports(ports, out):
            return
        if not capability.window_opened():
            return
        cap = _stored_captures()
        if cap:
            out["$capture"] = cap
    except Exception:
        pass

# When the parent stops a silent step, the child reports what it saw before it goes
def _on_terminate(signum, frame) -> None:
    try:
        body = json.dumps(_failure_payload("stopped before finishing", "Terminated"),
                          default=str)
        sys.stdout.write(SENTINEL + capability.scrub(body, _SECRET_VALUES) + "\n")
        sys.stdout.flush()
    finally:
        os._exit(1)

# What the step saw at the moment it failed - traffic and page - for the failure report
def _saw(ns: dict) -> dict:
    saw: dict = {}
    if _TRAFFIC:
        saw["traffic"] = [dict(e) for e in _TRAFFIC[-TRAFFIC_KEEP:]]
    page = _capture_page(ns)

    har = page.pop("har", "") if page else _store_har()
    if page:
        saw["page"] = page
    if har:
        saw["har"] = har
    pages = _store_snapshots()
    if pages:
        saw["pages"] = pages
    return saw

# Reads the job from stdin, runs the step's code, and prints the result on a marked line
def main() -> None:
    job = json.loads(sys.stdin.read() or "{}")
    # A browser step imports the app's own playwright, from the app's packages, behind the workflow's own
    if job.get("app_site"):
        sys.path.append(job["app_site"])
    code, entry = job.get("code", ""), job.get("entry")
    inputs = job.get("inputs", {})
    secret_names = set(job.get("secret_names", []))
    secret_values = [os.environ[n] for n in secret_names if n in os.environ]

    if job.get("rehearse"):
        _install_rehearsal_guard()
    elif job.get("egress_mode") == "allowlist":
        _install_egress_guard(job.get("egress_allowlist", []))

    from runtime import diskguard
    diskguard.install(job.get("path_roots") or [], job.get("harness_roots") or [],
                      job.get("data_dir"), job.get("readonly_roots") or [])

    from runtime import procguard
    procguard.install()

    import socket as _socket_mod
    _socket_mod.setdefaulttimeout(30)
    _install_traffic_log()

    capability.bind(inputs, profile_dir=job.get("profile_dir"),
                    blob_owner=job.get("blob_owner"))

    surface = {"read_input": capability.read_input, "write_output": capability.write_output,
               "get_secret": capability.get_secret, "ai_call": capability.ai_call,
               "write_file": capability.write_file,
               "heartbeat": capability.heartbeat,
               "checkpoint": capability.checkpoint,
               "checkpoints": capability.checkpoints,
               "mark_sent": capability.mark_sent,
               "browser_goto": capability.browser_goto,
               "confirm_with_user": capability.confirm_with_user,
               "unexpected_input": capability.unexpected_input}
    ns = dict(surface)
    global _NS, _SECRET_VALUES
    _NS, _SECRET_VALUES = ns, secret_values
    if hasattr(signal, "SIGTERM") and os.name != "nt":
        signal.signal(signal.SIGTERM, _on_terminate)

    import contextlib
    stack = contextlib.ExitStack()
    try:
        if job.get("browser"):
            with diskguard.harness():
                ns["page"] = stack.enter_context(capability.browser_page(
                    **dict(job.get("browser_options") or {})))
            url = str(job.get("browser_url") or "")
            if url:
                capability.browser_goto(ns["page"], url)
        exec(code, ns)

        produced = capability.collected_outputs()
        if produced:
            out = produced
        else:
            fn = ns.get(entry) if entry else None
            if not callable(fn):
                raise NameError(
                    "node produced no output: write declared outputs with write_output(name, value), or define a function that returns them" + (f" (looked for {entry!r})" if entry else ""))

            kwargs = {name: (capability.get_secret(name) if name in secret_names
                             else capability.read_input(name)) for name in inputs}
            result = fn(**kwargs)
            out = result if result is not None else capability.collected_outputs()

        if ns.get("page") is not None:
            try:
                capability.snapshot_page(ns["page"])
            except Exception:
                pass

        _keep_if_the_outputs_are_wrong(job.get("output_ports") or [], out)

        stack.close()
        if job.get("keep_captures") and isinstance(out, dict):
            cap = _stored_captures()
            if cap:
                out["$capture"] = cap
        body = json.dumps({"ok": True, "output": capability.scrub_value(out, secret_values),
                           **({"calls": dict(_CALLS)} if _CALLS["total"] else {})},
                          default=str)
    except Exception as e:
        stack.close()
        payload = _failure_payload(capability.name_read_values(str(e)), type(e).__name__)

        if type(e).__name__ == "UnexpectedInputError":
            payload["found"] = getattr(e, "found", None)
            payload["expected"] = getattr(e, "expected", None)
        body = json.dumps(capability.scrub_value(payload, secret_values), default=str)

    print(SENTINEL + capability.scrub(body, secret_values))

if __name__ == "__main__":
    main()
