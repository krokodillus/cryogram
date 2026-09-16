# Static checks on step code - including the scan that keeps credentials out of anything built or shipped
from __future__ import annotations

import ast
import sys
import re

ALLOWED_IMPORTS: set[str] = {"capability", "re", "json", "math", "datetime", "decimal"}
NETWORK_IMPORTS: set[str] = {"requests", "httpx", "urllib", "socket", "http", "aiohttp"}
SUBPROCESS_IMPORTS: set[str] = {"subprocess", "os", "shutil", "multiprocessing"}
AI_IMPORTS: set[str] = {"openai", "anthropic", "google", "cohere", "mistralai", "groq", "ollama"}

AI_ENDPOINTS: tuple[str, ...] = ("api.openai.com", "api.anthropic.com", "googleapis.com",
                                 "/v1/chat/completions", "/v1/messages", "api.cohere",
                                 "api.mistral", "api.groq.com")

RESERVED_NAMES: set[str] = {"ai_call", "read_input", "write_output",
                            "get_secret", "write_file",
                            "heartbeat", "checkpoint", "checkpoints", "mark_sent",
                            "browser_page", "browser_goto", "confirm_with_user",
                            "unexpected_input",
                            "page"}

PACKAGE_FOR_IMPORT = {
    "bs4": "beautifulsoup4", "yaml": "PyYAML", "PIL": "Pillow", "cv2": "opencv-python",
    "sklearn": "scikit-learn", "dateutil": "python-dateutil", "dotenv": "python-dotenv",
    "googleapiclient": "google-api-python-client", "google": "google-api-python-client",
    "docx": "python-docx", "pptx": "python-pptx", "fitz": "PyMuPDF", "jwt": "PyJWT",
    "nacl": "PyNaCl", "Crypto": "pycryptodome", "attr": "attrs", "magic": "python-magic",
    "gi": "PyGObject", "serial": "pyserial", "usb": "pyusb", "Foundation": "pyobjc",
    "AppKit": "pyobjc", "objc": "pyobjc",
}

# A syntax error, named with its line, before the code is run anywhere
def check_syntax(code: str) -> list[str]:
    try:
        ast.parse(code or "")
    except SyntaxError as e:
        return [f"syntax error at line {e.lineno}: {e.msg}"]
    return []

# The top-level modules the code imports that the standard library does not provide
def import_roots(code: str) -> list[str]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return []
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(_module_root(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add(_module_root(node.module))
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    return sorted(r for r in roots if r and r not in stdlib and r not in sys.builtin_module_names)

def _module_root(name: str | None) -> str:
    return (name or "").split(".")[0]

# The structural checks every piece of step code passes before it can be saved
def check(code: str, declared_vars: list[str] | None = None) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e.msg} (line {e.lineno})"]
    violations: list[str] = []
    violations += _check_except_handlers(tree)
    violations += _check_reserved_names(tree)
    return violations

def _check_except_handlers(tree: ast.AST) -> list[str]:
    out = []
    for h in ast.walk(tree):
        if not isinstance(h, ast.ExceptHandler):
            continue
        if h.type is None:
            out.append(f"swallowed exception (line {h.lineno}): bare 'except:' - "
                       "catch a specific type and handle or re-raise")
            continue
        body_trivial = all(
            isinstance(s, ast.Pass)
            or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                and s.value.value is Ellipsis)
            for s in h.body)
        reraises = any(isinstance(n, ast.Raise) for n in ast.walk(h))
        uses_exc = bool(h.name) and any(
            isinstance(n, ast.Name) and n.id == h.name for n in ast.walk(h))
        if body_trivial and not reraises and not uses_exc:
            out.append(f"swallowed exception (line {h.lineno}): handler does nothing - "
                       "handle the error or re-raise, never fail silently")
    return out

def _check_reserved_names(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names = [node.id]
        elif isinstance(node, ast.arg):
            names = [node.arg]
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names = list(node.names)
        for n in names:
            if n in RESERVED_NAMES:
                out.append(f"reserved capability name reassigned: '{n}' - "
                           "the capability surface may be called, never redefined")
    return out

# Code that ignores its declared inputs or outputs only literals is refused - a step must really do its work
def check_fake(code: str, input_names: list[str] | None = None) -> list[str]:
    names = list(input_names or [])
    if not names:
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    def _calls(fn):
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == fn]
    reads, writes = _calls("read_input"), _calls("write_output")

    declared = set(names)
    def _fn_takes_inputs(f) -> bool:
        params = {a.arg for a in (f.args.args + f.args.posonlyargs + f.args.kwonlyargs)}
        return bool(params & declared) or f.args.kwarg is not None
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    takes_params = any(_fn_takes_inputs(f) for f in funcs)
    out: list[str] = []
    if not reads and not takes_params:
        out.append("this step declares inputs but never reads them (read_input) - it ignores what it is given. Compute the result FROM its inputs; never hardcode the answer.")
    # A value with no dependence on anything - a hardcoded output whatever the wrapping
    def _const(node) -> bool:
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return _const(node.operand)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(_const(e) for e in node.elts)
        if isinstance(node, ast.Dict):
            return all(_const(k) and _const(v)
                       for k, v in zip(node.keys, node.values) if k is not None)
        return False
    def _value_is_literal(call):
        if len(call.args) >= 2:
            return _const(call.args[1])
        for kw in call.keywords:
            if kw.arg == "value":
                return _const(kw.value)
        return False
    if writes and all(_value_is_literal(w) for w in writes):
        out.append("every output here is a hardcoded value, not computed from the input - build the real logic. Faking an output to pass a test is never allowed (this is enforced).")
    return out

# A secret may only be read by name through get_secret, never printed, returned or written out
def check_secret_ports(code: str, secret_port_names: list[str] | None = None) -> list[str]:
    names = list(secret_port_names or [])
    if not names or "get_secret" not in code:
        return []
    read_names = read_input_names(code)
    out: list[str] = []
    for name in names:
        if name not in read_names:
            out.append(f"the secret input port {name!r} is never read, while the "
                       "code calls get_secret - the value the run supplies would "
                       f"be discarded. Read it with read_input({name!r}); "
                       "get_secret is only for stored credentials the step declares by name, never a substitute for an input.")
    return out

# The input names the code reads with a literal read_input("name")
def read_input_names(code: str) -> set:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    return {n.args[0].value for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "read_input" and n.args
            and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, str)}

# An input list the code cuts to a fixed number of items, as (input name, number) pairs
def literal_item_caps(code: str) -> list[tuple[str, int]]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return []

    def read_name(expr):
        if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
                and expr.func.id == "read_input" and expr.args
                and isinstance(expr.args[0], ast.Constant)
                and isinstance(expr.args[0].value, str)):
            return expr.args[0].value
        return None

    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name = read_name(node.value)
            if name:
                bound[node.targets[0].id] = name

    def source(expr):
        return read_name(expr) or (bound.get(expr.id) if isinstance(expr, ast.Name) else None)

    def whole(expr):
        return (isinstance(expr, ast.Constant) and isinstance(expr.value, int)
                and not isinstance(expr.value, bool))

    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            sl = node.slice
            name = source(node.value)
            if (name and whole(sl.upper) and sl.step is None
                    and (sl.lower is None or (whole(sl.lower) and sl.lower.value == 0))):
                found.append((name, int(sl.upper.value)))
        elif isinstance(node, ast.Call) and len(node.args) == 2:
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            name = source(node.args[0]) if fname == "islice" else None
            if name and whole(node.args[1]):
                found.append((name, int(node.args[1].value)))
    return found

# The output names the code actually writes, so declared ports and written names can be reconciled
def write_output_names(code: str) -> tuple[set, bool]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set(), False
    names, dynamic = set(), False
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "write_output"):
            continue
        arg = n.args[0] if n.args else next(
            (kw.value for kw in n.keywords if kw.arg == "name"), None)
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            names.add(arg.value)
        else:
            dynamic = True
    return names, dynamic

# A name the code reads but never defines is a guaranteed NameError, caught at save instead of on the first run
def check_undefined_names(code: str) -> list[str]:
    import builtins as _bt
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    bound: set[str] = set(RESERVED_NAMES)
    bound |= {"__name__", "__file__", "__doc__", "__builtins__"}
    bound |= set(dir(_bt))
    loads: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                loads.add(node.id)
            else:
                bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            bound.add(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            a = node.args
            for arg in (a.posonlyargs + a.args + a.kwonlyargs
                        + ([a.vararg] if a.vararg else [])
                        + ([a.kwarg] if a.kwarg else [])):
                bound.add(arg.arg)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound.add(alias.asname or _module_root(alias.name))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return []
                bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, ast.MatchAs) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)

    return [f"'{name}' is read but never defined anywhere in the code - a "
            "guaranteed NameError on the first run that reaches it. Define it, import it, or remove the call; if it was meant to ask the user something, that pause already exists (the approval popup, a user-input step, the value form) - run-time code cannot talk to the user."
            for name in sorted(loads - bound)]

# A run-time step must never leave a browser running past its own end - a detached launch is build technique
def check_detached_browser(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    has_popen = any(isinstance(n, ast.Call)
                    and ((isinstance(n.func, ast.Name)
                          and n.func.id == "Popen")
                         or (isinstance(n.func, ast.Attribute)
                             and n.func.attr == "Popen"))
                    for n in ast.walk(tree))
    if not has_popen:
        return []
    binaryish = ("google chrome", "chrome.exe", "google-chrome", "chromium")
    has_binary = any(isinstance(n, ast.Constant)
                     and isinstance(n.value, str)
                     and any(b in n.value.lower() for b in binaryish)
                     for n in ast.walk(tree))
    if not has_binary:
        return []
    return ["launches a browser DETACHED (Popen on a Chrome binary) - that is build-time capture technique, never run-time step code: a window can never outlive its step (the profile lock allows one Chrome, so the next browser step crashes on it). Run-time login is ONE step: check the saved session, open with launch_persistent_context, poll for the session cookie, save it, close the window."]

# Code that opens a real browser window itself, instead of asking the app for one
OWN_BROWSER = re.compile("launch_persistent_context\\(|chromium\\.launch\\(|connect_over_cdp\\(|sync_playwright\\(")
# Code that drives a real browser window, the app's way or its own
LAUNCHES_BROWSER = re.compile(r"browser_page\(|" + OWN_BROWSER.pattern)

_NETWORK_ROOTS = frozenset((
    "urllib", "http", "requests", "httpx", "urllib3", "aiohttp", "socket",
    "smtplib", "imaplib", "poplib", "ftplib", "websocket", "websockets",
    "googleapiclient", "gspread", "google", "boto3", "botocore", "slack_sdk",
    "msal", "O365", "exchangelib", "praw", "tweepy", "stripe", "openai",
    "anthropic", "notion_client", "pyairtable", "psycopg2", "pymysql",
    "sqlalchemy", "pymongo", "redis", "paramiko"))
_URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")

_SECOND_LEVEL = frozenset(("co", "com", "org", "net", "ac", "gov", "edu"))

# The service a host belongs to: googleapis.com for both oauth2. and sheets.googleapis.com
def _service_of(host: str) -> str:
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()

# What kinds of work a piece of code does: drives a browser, talks to which outside services, calls the AI
def work_signature(code: str) -> dict:
    sig = {"browser": False, "http_client": False, "services": [], "ai": False}
    text = code or ""
    if not text.strip():
        return sig
    if LAUNCHES_BROWSER.search(text) or check_detached_browser(text):
        sig["browser"] = True
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return sig
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            if any(_module_root(a.name) in _NETWORK_ROOTS for a in n.names):
                sig["http_client"] = True
        elif isinstance(n, ast.ImportFrom):
            if _module_root(n.module) in _NETWORK_ROOTS:
                sig["http_client"] = True
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            if n.func.id == "ai_call":
                sig["ai"] = True
            elif n.func.id == "urlopen":
                sig["http_client"] = True
    services = set()
    for m in _URL_RE.finditer(text):
        host = m.group(1)
        if host in ("localhost", "127.0.0.1") or host.replace(".", "").isdigit():
            continue
        services.add(_service_of(host))
    sig["services"] = sorted(services)
    return sig

# The libraries a step needs that the app knows about, added to the ones it declared
def required_packages(code: str, declared: list | None = None,
                      is_browser: bool = False) -> list:
    out = [str(p) for p in (declared or [])]
    if is_browser and not any(
            str(p).split("[")[0].split("=")[0].strip().lower() == "playwright"
            for p in out):
        out.append("playwright")
    return out

# A browser step opens its window the app's way, so the flags, the recording and the closing are never its problem
def check_recording(code: str) -> list[str]:
    if not OWN_BROWSER.search(code or ""):
        return []
    return ["opens its own browser window - a browser step is handed `page` already open on this workflow's profile, with the flags a logged-in site needs, recording every request and response, keeping the session and closing after the checks, so write the page work alone. Navigate with browser_goto(page, url) - it retries and remembers where the step means to be, so a sign-in redirect is undone for you. Everything else on the page stays yours: clicking, typing, scrolling, frames, downloads, loops."]

# A long loop that says nothing is stopped for looking stuck; the step reports as it goes
def check_silent_loop(code: str) -> list[str]:
    if "heartbeat(" in (code or ""):
        return []
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return []
    outside = ("goto", "urlopen", "request", "get", "post", "screenshot",
               "wait_for_selector", "wait_for_timeout", "click", "content")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.While)):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in outside):
                return ["works through a loop that reaches outside without ever calling heartbeat() - a step that says nothing for the whole silence limit is stopped for looking stuck, however well it is working. Call heartbeat(\"item 3 of 20\") at the top of each iteration; when ONE call genuinely takes longer than the limit, declare timeout_seconds on the step instead."]
    return []

_DISK_HEADS = ("Users", "home", "Volumes", "mnt", "media", "root", "tmp", "var",
               "private", "opt", "etc", "srv", "Desktop", "Documents", "Downloads")
_HOME_CALLS = ("expanduser", "home")

# A literal path to a folder outside the workflow's allowed ones is a design gap: declare it or make it a folder setting
def check_outside_paths(code: str, allowed_roots: list | None = None) -> list[str]:
    import os as _os
    import tempfile as _tf
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    roots = [_os.path.abspath(_os.path.expanduser(str(r))) for r in (allowed_roots or [])
             if str(r).strip()]
    roots.append(_tf.gettempdir())

    def under(p: str) -> bool:
        p = _os.path.abspath(_os.path.expanduser(p))
        return any(p == r or p.startswith(r.rstrip(_os.sep) + _os.sep) for r in roots)

    def diskish(v: str) -> bool:
        if "://" in v or "\n" in v or len(v) < 3:
            return False
        if v.startswith("~/") or v.startswith("~\\") or v == "~":
            return True
        if len(v) > 2 and v[1] == ":" and v[2] in "\\/":
            return True
        if v.startswith("/"):
            head = v.split("/", 2)[1] if "/" in v[1:] else v[1:]
            return head in _DISK_HEADS
        return False

    out: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and diskish(n.value) and not under(n.value):
            out.append(f"reads or writes {n.value!r}, a place on this computer "
                       "outside the workflow's allowed folders - declare the folder in the step's `paths` (the user is asked once), or make it a folder setting the user picks; a file the workflow works on is a file/filepath setting read with read_input")
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr in _HOME_CALLS:
            out.append("reaches into the home folder (expanduser / Path.home) - a step may only touch its declared `paths` or a folder setting the user picks")
    return sorted(set(out))

# The fields of an input the code actually reads - static and conservative, never a false positive
def record_field_reads(code: str, input_name: str) -> set:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()

    def is_read_call(node) -> bool:
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "read_input" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == input_name)

    tracked: set = set()
    elem: set = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and is_read_call(n.value):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    tracked.add(t.id)
    for n in ast.walk(tree):
        if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) \
                and n.iter.id in tracked and isinstance(n.target, ast.Name):
            elem.add(n.target.id)

    def is_tracked(base) -> bool:
        return is_read_call(base) or (isinstance(base, ast.Name)
                                      and base.id in tracked | elem)

    keys: set = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Subscript) and is_tracked(n.value):
            sl = n.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                keys.add(sl.value)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "get" and n.args \
                and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str) and is_tracked(n.func.value):
            keys.add(n.args[0].value)
    return keys

# Advisory signals only; these prompt, never block
def inspect(code: str) -> dict:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(_module_root(a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(_module_root(node.module))
    endpoints = [e for e in AI_ENDPOINTS
                 if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and e in n.value
                        for n in ast.walk(tree))]
    return {
        "network": sorted(imports & NETWORK_IMPORTS),
        "subprocess": sorted(imports & SUBPROCESS_IMPORTS),
        "ai_imports": sorted(imports & AI_IMPORTS),
        "ai_endpoints": endpoints,
        "imports": sorted(imports),
    }

# The advisory signals as plain sentences
def advisories(code: str) -> list[str]:
    info = inspect(code)
    out = []
    if info.get("ai_imports") or info.get("ai_endpoints"):
        out.append("this code looks like a direct model call - route ALL intelligence through ai_call (declare an ai node) instead")
    if info.get("network") or info.get("subprocess"):
        touch = sorted(set(info.get("network", []) + info.get("subprocess", [])))
        out.append(f"this code may touch things outside the workflow ({', '.join(touch)}) - "
                   "if it reaches an external system, declare the node a connector and describe its external impact")
    return out

# A secret input carries a name, never the value - code must route it through get_secret
def check_secret_value_use(code: str,
                           secret_port_names: list[str] | None = None) -> list[str]:
    names = set(secret_port_names or [])
    if not names or "read_input" not in code:
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    def is_secret_read(n) -> bool:
        return (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "read_input" and n.args
                and isinstance(n.args[0], ast.Constant)
                and n.args[0].value in names)

    wrapped: set[int] = set()
    secret_vars: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == "get_secret":
            for a in ast.walk(n):
                if is_secret_read(a):
                    wrapped.add(id(a))
        if isinstance(n, ast.Assign) and is_secret_read(n.value):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    secret_vars.add(t.id)

    out: list[str] = []
    for n in ast.walk(tree):
        if is_secret_read(n) and id(n) not in wrapped:
            parented_to_assign = any(
                isinstance(p, ast.Assign) and p.value is n for p in ast.walk(tree))
            if not parented_to_assign:
                out.append(
                    f"read_input({n.args[0].value!r}) on a SECRET port returns "
                    "the secret's NAME, never the value - write "
                    f"value = get_secret(read_input({n.args[0].value!r})). "
                    "Using the name directly sends the literal text "
                    f"'{n.args[0].value}' outward. (Fixable here in the code; "
                    "if this code came from the plan, the plan's code must change.)")
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id in secret_vars \
                and isinstance(getattr(n, "ctx", None), ast.Load):
            inside_get_secret = False
            for c in ast.walk(tree):
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) \
                        and c.func.id == "get_secret" \
                        and any(x is n for x in ast.walk(c)):
                    inside_get_secret = True
            if not inside_get_secret:
                out.append(
                    f"variable '{n.id}' holds a secret port's NAME (from "
                    "read_input), but is used outside get_secret - the real "
                    f"value is get_secret({n.id}). Using the name directly "
                    "sends literal text outward.")
                break
    return out

_CRED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"sk-ant-[A-Za-z0-9_-]{20,}", "an Anthropic API key"),
    (r"sk-[A-Za-z0-9]{32,}", "an OpenAI-style API key"),
    (r"gh[posu]_[A-Za-z0-9]{30,}", "a GitHub token"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "a Slack token"),
    (r"AKIA[0-9A-Z]{16}", "an AWS access key id"),
    (r"AIza[0-9A-Za-z_-]{35}", "a Google API key"),
    (r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.", "a signed token (JWT)"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
    (r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{20,}", "an authorisation header value"),
)

# The credential scan: exact matches against stored values plus known key shapes; findings never quote the match
def find_hardcoded_secrets(text: str,
                           known_values: list[str] | None = None) -> list[str]:
    import re as _re
    body = str(text or "")
    if not body.strip():
        return []
    out: list[str] = []
    for v in (known_values or []):
        if v and len(v) >= 8 and v in body:
            out.append("a value that is stored as one of this computer's saved passwords or keys")
            break
    for pattern, what in _CRED_PATTERNS:
        if _re.search(pattern, body):
            out.append(f"what looks like {what}")
    return out
