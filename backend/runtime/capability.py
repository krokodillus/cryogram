# The only operations generated step code may call; secrets travel by name and are scrubbed on the way out
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from runtime import diskguard
from storage import blobstore
import providers

REQUEST_MESSAGE_CAP = 400
REQUEST_PATIENCE = float(os.environ.get("CRYOGRAM_CARD_PATIENCE", "1500"))

_ctx: dict[str, Any] = {"inputs": {}, "outputs": {}, "profile_dir": None,
                        "blob_owner": None, "files": {}, "har": None,
                        "calls": [], "calls_seen": 0, "calls_t0": 0.0}

# Sets the per-run context the helper functions read; called by the subprocess runner before the step's code
def bind(inputs: dict, profile_dir: Optional[str] = None,
         blob_owner: Optional[str] = None) -> None:
    _ctx["inputs"] = inputs or {}
    _ctx["outputs"] = {}
    _ctx["profile_dir"] = profile_dir
    _ctx["blob_owner"] = blob_owner
    _ctx["files"] = {}
    _ctx["har"] = None
    _ctx["window_opened"] = False
    _ctx["read"] = {}

def _owner() -> str:
    return _ctx.get("blob_owner") or blobstore.OWNER_APP

def collected_outputs() -> dict:
    return dict(_ctx["outputs"])

# Every file the step stored, by the name it gave, so a failure keeps them even when nothing was handed back
def window_opened() -> bool:
    return bool(_ctx.get("window_opened"))

# Every file the step stored, by the name it gave, so a failure keeps them even when nothing was handed back
def collected_files() -> dict:
    return dict(_ctx["files"])

# A step's declared answer size, bounded, else the default
def step_max_tokens(node: dict) -> int:
    import providers
    return providers.clamp_tokens((node.get("config") or {}).get("max_tokens"))

# A step's declared timeout can extend the default wait for a model, never shorten it
def step_ai_timeout(node: dict) -> int:
    import providers
    return providers.clamp_call_timeout(
        (node.get("config") or {}).get("timeout_seconds"))

import threading as _threading
_usage_ctx = _threading.local()

def take_ai_usage() -> Optional[dict]:
    u = getattr(_usage_ctx, "last", None)
    _usage_ctx.last = None
    return u

# The one door to a model: step code holds no key, and the answer's shape is enforced by the declared outputs
def ai_call(prompt: str, model_ref: dict, inputs: dict,
            output_ports: list | None = None,
            max_tokens: int | None = None,
            timeout: int | None = None) -> Any:
    import providers
    schema = schema_from_ports(output_ports) if output_ports else None
    if not (model_ref or {}).get("model"):
        picked = providers.pick_model(input_file_kinds(inputs))
        if picked is None:
            _usage_ctx.last = None
            return {"_unparsed": True, "error_kind": "model-not-chosen",
                    "_text": providers.no_pick_sentence(input_file_kinds(inputs))}
        model_ref = {**(model_ref or {}), "model": picked["model"],
                     "provider_id": picked["provider_id"]}
    try:
        deref, attachments = _deref_blobs(inputs, model_ref)
    except FileUnsupported as e:
        _usage_ctx.last = None
        return {"_unparsed": True, "error_kind": "file-unsupported",
                "_text": str(e), "_detail": dict(e.detail or {})}
    res = providers.call(model_ref, prompt, deref, output_schema=schema,
                         attachments=attachments, max_tokens=max_tokens,
                         timeout=timeout)
    _usage_ctx.last = res.get("usage")
    if "structured" in res:
        return _clean_output(res["structured"], output_ports)
    if res.get("error_kind"):
        return {"_unparsed": True, "_text": res.get("text", ""),
                "error_kind": res["error_kind"]}
    return _clean_output(_parse_structured(res.get("text", "")), output_ports)

_TOOLUSE_XML = re.compile(
    r"</?(?:invoke|parameter|function_calls|function_results|antml:[\w-]+)\b[^>]*>",
    re.IGNORECASE)

# Removes tool-protocol tokens and a wrapper tag echoing the field's own name - keyed patterns, never fuzzy text logic
def _strip_scaffolding(value: Any, field_name: str) -> Any:
    if not isinstance(value, str):
        return value
    v = _TOOLUSE_XML.sub("", value).strip()
    fn = re.escape(field_name or "")
    if fn:
        m = re.match(rf"^<{fn}\b[^>]*>(.*)</{fn}>$", v, re.DOTALL | re.IGNORECASE)
        if m:
            v = m.group(1)
        else:
            v = re.sub(rf"^<{fn}\b[^>]*>", "", v, flags=re.IGNORECASE)
            v = re.sub(rf"</{fn}>$", "", v.rstrip(), flags=re.IGNORECASE)
    return v.strip()

# Whether the port expects a record or a list, read from the same projection the schema builder uses
def _structured_field(port: dict) -> bool:
    try:
        from runtime import shape
        return (shape.from_port(port) or {}).get("type") in ("object", "array")
    except Exception:
        return False

# Cleans model answers that leak protocol framing or arrive as JSON text where a structure was declared
def _clean_output(obj: Any, ports: list | None) -> Any:
    if not isinstance(obj, dict):
        return obj
    by_name = {p.get("name"): p for p in ports or []}
    out = {}
    for k, v in obj.items():
        if k in by_name:
            v = _strip_scaffolding(v, k)
            if isinstance(v, str) and v.strip()[:1] in ("[", "{") \
                    and _structured_field(by_name[k]):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, (dict, list)):
                        v = parsed
                except ValueError:
                    pass
        out[k] = v
    return out

_FILE_KINDS = {"application/pdf": "pdf", "image/png": "image", "image/jpeg": "image",
               "image/gif": "image", "image/webp": "image"}
_KIND_BY_EXT = {"xlsx": "spreadsheet", "xls": "spreadsheet", "csv": "spreadsheet",
                "tsv": "spreadsheet", "docx": "document", "doc": "document",
                "pptx": "document", "ppt": "document"}

def _file_kind(mime: str, name: str) -> str:
    if mime in _FILE_KINDS:
        return _FILE_KINDS[mime]
    if "wordprocessingml" in mime or "presentationml" in mime or mime == "application/msword":
        return "document"
    if "spreadsheetml" in mime or mime == "application/vnd.ms-excel":
        return "spreadsheet"
    ext = str(name or "").rsplit(".", 1)[-1].lower() if "." in str(name or "") else ""
    return _KIND_BY_EXT.get(ext, "other")

class FileUnsupported(Exception):
    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}

# A rough page count for a PDF, from its own page objects; None when the file does not say
def _pdf_pages(data: bytes) -> Optional[int]:
    import re as _re
    n = len(_re.findall(rb"/Type\s*/Page\b", data))
    return n or None

# The kinds of file among a step's inputs, from the stored files themselves
def input_file_kinds(inputs: dict) -> list:
    kinds: list = []
    for k, v in (inputs or {}).items():
        if isinstance(v, str) and v.startswith("blob:") and blobstore.exists(v):
            st = blobstore.stat(v, _owner()) or {}
            mime = st.get("mime", "")
            if mime.startswith("text/") or mime.endswith(("json", "xml", "csv")):
                continue
            kind = _file_kind(mime, st.get("name") or k)
            if kind not in kinds:
                kinds.append(kind)
    return kinds

# Text blobs become their content before the prompt is built; a PDF or an image goes to the model whole, or the call stops before it is made
def _deref_blobs(inputs: dict, model_ref: Optional[dict] = None) -> tuple[dict, list]:
    import providers
    from runtime import env_checks
    out: dict[str, Any] = {}
    attachments: list[dict] = []
    facts = providers.model_facts(model_ref or {})
    total = 0
    for k, v in inputs.items():
        if isinstance(v, str) and v.startswith("blob:") and blobstore.exists(v):
            st = blobstore.stat(v, _owner())
            mime = st.get("mime", "")
            if mime.startswith("text/") or mime.endswith(("json", "xml", "csv")):
                out[k] = blobstore.get(v).decode("utf-8", errors="replace")
                continue
            kind = _file_kind(mime, st.get("name") or k)
            if kind not in ("pdf", "image") or not facts.get(f"reads_{kind}"):
                raise FileUnsupported(
                                env_checks.file_unsupported_sentence(kind, facts),
                                detail={"port": k, "kind": kind})
            data = blobstore.get(v)
            provider = facts.get("provider") or "its provider"
            limit = facts.get("file_bytes")
            if limit and len(data) > limit:
                raise FileUnsupported(
                                f"This file is {len(data) / 1048576:.0f} MB, and the most "
                                f"{provider} takes in one call is {limit // 1048576} MB. "
                                "Split the file, or choose a provider that takes larger files.",
                                detail={"port": k, "kind": kind})
            total += len(data)
            req = facts.get("request_bytes")
            if req and total > req:
                raise FileUnsupported(
                                f"These files together are {total / 1048576:.0f} MB, and the "
                                f"most {provider} takes in one call is {req // 1048576} MB. "
                                "Send fewer files to this step, or choose a provider that takes larger calls.", detail={"port": k, "kind": kind})
            pages = _pdf_pages(data) if kind == "pdf" else None
            if pages and facts.get("pdf_pages") and pages > facts["pdf_pages"]:
                raise FileUnsupported(
                                f"This PDF has about {pages} pages, and the most {provider} "
                                f"takes in one call is {facts['pdf_pages']}. Split the file, "
                                "or choose a provider that takes longer files.",
                                detail={"port": k, "kind": kind})
            attachments.append({"name": k, "mime": mime, "data": data})
            out[k] = f"(the file for {k} is attached)"
            continue
        out[k] = v
    return out, attachments

# Builds the JSON schema the model must answer in, from the step's declared outputs
def schema_from_ports(ports: list) -> dict:
    from runtime import shape as _shape
    props = {}
    for p in ports or []:
        frag = _ai_frag(_shape.from_port(p))
        if not p.get("optional") and frag.get("type") == "string" \
                and "enum" not in frag and not p.get("secret"):
            frag["minLength"] = 1
        desc = str(p.get("description") or "").strip() or p.get("label")
        if desc:
            frag.setdefault("description", desc)

        for f in p.get("item_fields") or []:
            if isinstance(f, dict) and f.get("name") and (f.get("description") or f.get("label")):
                sub = ((frag.get("items") or {}).get("properties") or {}).get(f["name"])
                if isinstance(sub, dict):
                    sub.setdefault("description",
                                   str(f.get("description") or "").strip() or f.get("label"))
        props[p["name"]] = frag
    return {"type": "object", "properties": props,
            "required": [p["name"] for p in ports or []
                         if not p.get("optional")],
            "additionalProperties": False}

# The model-facing view of a shape: private markers stripped, objects closed against invented fields
def _ai_frag(sch: dict) -> dict:
    if not sch or "type" not in sch:
        return {}
    out: dict = {"type": sch["type"]}
    if sch.get("x-nullable"):
        out["type"] = [sch["type"], "null"]
    if sch.get("enum") is not None:
        out["enum"] = list(sch["enum"])
    if sch.get("format") == "date":
        out["description"] = "ISO date, YYYY-MM-DD"
    if sch["type"] == "object":
        req = list(sch.get("required") or [])
        exp = [k for k in (sch.get("x-expected") or []) if k not in req]
        props = {}
        for k, v in (sch.get("properties") or {}).items():
            frag = _ai_frag(v)
            if k in req and "enum" not in frag \
                    and (frag.get("type") == "string"
                         or frag.get("type") == ["string", "null"]):
                frag["minLength"] = 1
            if k in exp and "type" in frag and not isinstance(frag["type"], list):
                frag["type"] = [frag["type"], "null"]
            props[k] = frag
        out["properties"] = props
        out["required"] = req + exp
        out["additionalProperties"] = False
    elif sch["type"] == "array" and sch.get("items"):
        out["items"] = _ai_frag(sch["items"])
    return out

_TEXT_MIMES = ("application/json", "application/xml", "application/csv",
               "application/javascript", "application/x-ndjson")

# Node code consumed an input against its declared contract; surfaces as a reasoned check verdict, never a traceback
class InputContractError(Exception):
    pass

# The input is not what this step parses; carries found and expected so the stopped run shows them instead of a KeyError
class UnexpectedInputError(Exception):
    def __init__(self, reason: str, found=None, expected=None):
        super().__init__(reason)
        self.found = found
        self.expected = expected

# Step code calls this when its input is not the kind of data it parses; the run stops with found-vs-expected shown
def unexpected_input(reason: str, found=None, expected=None):
    raise UnexpectedInputError(str(reason), found=found, expected=expected)

# How step code reads a declared input; path=True writes the data to a file and returns its path
def read_input(name: str, binary: bool = False, path: bool = False,
               kind: Optional[str] = None, url: Optional[str] = None,
               status: Optional[int] = None, method: Optional[str] = None,
               ids: Optional[list] = None) -> Any:
    value = _read_input(name, binary=binary, path=path)
    if any(f is not None for f in (kind, url, status, method, ids)):
        if path or binary:
            raise InputContractError("a filter reads the recording's contents; leave path and binary off")
        value = filter_recording(name, value, kind=kind, url=url, status=status,
                                 method=method, ids=ids)
    if not path:
        _ctx.setdefault("read", {})[name] = value
    return value

# The calls of a listing or a traffic recording that match the filters, in the shape they came in
def filter_recording(name: str, value: Any, kind=None, url=None, status=None,
                     method=None, ids=None) -> Any:
    want_ids = {int(i) for i in (ids or [])}

    def keep(c_kind, c_url, c_status, c_method, c_type, idx) -> bool:
        if want_ids and idx not in want_ids:
            return False
        if kind is not None and str(kind).lower() not in (str(c_kind or "").lower(),
                                                          str(c_type or "").lower().split("/")[0]):
            return False
        if url is not None and str(url).lower() not in str(c_url or "").lower():
            return False
        if status is not None and int(status) != int(c_status or 0):
            return False
        if method is not None and str(method).upper() != str(c_method or "").upper():
            return False
        return True
    if isinstance(value, list) and all(isinstance(c, dict) and "url" in c for c in value):
        return [c for i, c in enumerate(value)
                if keep(c.get("kind"), c.get("url"), c.get("status"), c.get("method"),
                        c.get("type"), int(c.get("id", i)))]
    entries = (value.get("log") or {}).get("entries") if isinstance(value, dict) else None
    if isinstance(entries, list):
        kept = []
        for i, e in enumerate(entries):
            req, res = e.get("request") or {}, e.get("response") or {}
            mime = (res.get("content") or {}).get("mimeType") or ""
            if keep(e.get("_resourceType"), req.get("url"), res.get("status"),
                    req.get("method"), mime, i):
                kept.append(e)
        return {**value, "log": {**value["log"], "entries": kept}}
    raise InputContractError(f"input {name!r} is not a calls listing or a traffic "
                             "recording, so kind, url, status, method and ids do not apply to it - read it without filters")

def _read_input(name: str, binary: bool, path: bool) -> Any:
    if name not in _ctx["inputs"]:
        raise KeyError(f"input {name!r} not declared/available")
    val = _ctx["inputs"][name]
    if isinstance(val, str) and val.startswith("blob:"):
        with diskguard.harness():
            data = blobstore.get(val)
            mime = blobstore.stat(val, _owner()).get("mime", "")
            fname = str(blobstore.stat(val, _owner()).get("name") or "")
        if path:
            return _materialise(name, data, fname or None)
        if binary:
            return data
        if fname.endswith(".json") or mime.endswith("json"):
            return json.loads(data.decode("utf-8"))
        if mime.startswith("text/") or mime in _TEXT_MIMES:
            return data.decode("utf-8", errors="replace")
        if not mime or mime == "application/octet-stream":
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                raise InputContractError(
                    f"input {name!r} is binary content; this node reads inputs as "
                    f"text - use read_input({name!r}, binary=True) for bytes, or "
                    "convert the file upstream") from None
        raise InputContractError(
            f"input {name!r} is {mime} (binary); this node reads inputs as text - "
            f"use read_input({name!r}, binary=True) for bytes, or convert the "
            "file upstream")

    if path and val is not None:
        if (isinstance(val, str) and len(val) < 1024 and "\n" not in val
                and os.path.exists(val)):
            return val
        if isinstance(val, (bytes, bytearray)):
            data = bytes(val)
        elif isinstance(val, str):
            data = val.encode("utf-8")
        else:
            data = json.dumps(val).encode("utf-8")
        return _materialise(name, data)
    return val

# An error message with the values the step read replaced by the input's name, so a quoted file never floods the message
def name_read_values(text: str) -> str:
    text = str(text or "")
    for name, value in (_ctx.get("read") or {}).items():
        if not isinstance(value, str):
            continue
        label = f"<the contents of input '{name}'>"
        if len(value) <= len(label):
            continue
        for form in (repr(value), repr(value)[1:-1], value):
            if form and form in text:
                text = text.replace(form, label)
    return text

# Writes an input's bytes to a real file for libraries that open paths; keeps the original extension
def _materialise(name: str, data: bytes, orig_name: Optional[str] = None) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(orig_name or name)) or "input"
    safe = safe.lstrip(".") or "input"
    with diskguard.harness():
        f = _scratch_dir() / safe
        f.write_bytes(data)
    return str(f)

_scratch: Optional[Path] = None

# One private scratch folder per step process, removed when the process ends
def _scratch_dir() -> Path:
    global _scratch
    if _scratch is None or not _scratch.is_dir():
        import atexit
        import shutil
        import tempfile
        _scratch = Path(tempfile.mkdtemp(prefix="cryogram_inputs_"))
        atexit.register(shutil.rmtree, str(_scratch), True)
    return _scratch

# The workflow's own Chrome profile folder, the same one for build-time tries and real runs
def browser_profile() -> str:
    d = _ctx.get("profile_dir")
    if not d:
        raise RuntimeError("browser_profile() is only available inside a step (the app supplies the folder)")
    with diskguard.harness():
        Path(d).mkdir(parents=True, exist_ok=True)
    return str(d)

class BrowserClosedError(Exception):
    pass

class NobodyAnsweredError(Exception):
    pass

# A browser step asks the person at the machine to do something in the window, and waits for them to confirm
def confirm_with_user(message: str, page=None) -> bool:
    path = os.environ.get("CRYOGRAM_REQUEST_FILE")
    if not path:
        if os.environ.get("CRYOGRAM_HEARTBEAT_FILE"):
            raise RuntimeError(
                "only a browser step can ask the person at the machine to do something - it is the window that makes that possible. A value the run lacks pauses with its own form, a choice partway is a user-input step, and a send is fronted by its approval.")
        return True
    import time as _time
    answer_path = f"{path}.answer"
    with diskguard.harness():
        for p in (path, answer_path):
            try:
                os.remove(p)
            except OSError:
                pass
        with open(path, "w") as f:
            f.write(str(message or "")[:REQUEST_MESSAGE_CAP])
    if page is not None:
        _window_on_screen(page, True)
    deadline = _time.time() + REQUEST_PATIENCE
    while _time.time() < deadline:
        _time.sleep(0.3)
        if page is not None:
            try:
                if page.is_closed():
                    raise BrowserClosedError("the window was closed")
            except BrowserClosedError:
                raise
            except Exception:
                raise BrowserClosedError("the window was closed")
        with diskguard.harness():
            if os.path.exists(answer_path):
                with open(answer_path) as f:
                    done = f.read(20).strip() == "done"
                if done:
                    _window_on_screen(page, False)
                    _back_where_it_meant_to_be(page)
                    return True
                break
    _window_on_screen(page, False)
    raise NobodyAnsweredError(str(message or "")[:REQUEST_MESSAGE_CAP])

# A site that sends you somewhere else after a login is undone: the step goes back to the page it was opening
def _back_where_it_meant_to_be(page) -> None:
    meant = str(_ctx.get("meant_to_be") or "")
    if not (page is not None and meant) or _page_url(page) == meant:
        return
    try:
        page.goto(meant, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1000)
    except Exception:
        pass

# Brings the window where the person can see it, and puts it back afterwards
def _window_on_screen(page, show: bool) -> None:
    if page is None:
        return
    try:
        ctx = _ctx.get("browser") or page.context
        cdp = ctx.new_cdp_session(page)
        wid = cdp.send("Browser.getWindowForTarget")["windowId"]
        bounds = ({"left": 80, "top": 60, "windowState": "normal"} if show
                  else {"left": -32000, "top": -32000})
        cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": bounds})
    except Exception:
        pass

_BROWSER_CANDIDATES_MAC = ["Google Chrome.app/Contents/MacOS/Google Chrome",
                           "Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
                           "Chromium.app/Contents/MacOS/Chromium",
                           "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
_BROWSER_CANDIDATES_LINUX = ["google-chrome", "google-chrome-stable", "chromium",
                             "chromium-browser", "microsoft-edge"]

# The browser a step drives: Chrome, Chrome Beta, Chromium or Edge, wherever it normally lives; None when there is none
def browser_executable() -> Optional[str]:
    import shutil
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        for apps in ("/Applications", os.path.join(home, "Applications")):
            for rel in _BROWSER_CANDIDATES_MAC:
                p = os.path.join(apps, rel)
                if os.path.exists(p):
                    return p
        return None
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        for root in [r for r in roots if r]:
            for rel in (("Google", "Chrome", "Application", "chrome.exe"),
                        ("Microsoft", "Edge", "Application", "msedge.exe")):
                p = os.path.join(root, *rel)
                if os.path.exists(p):
                    return p
        return None
    for name in _BROWSER_CANDIDATES_LINUX:
        found = shutil.which(name)
        if found:
            return found
    return None

_PAGE_PATCH = """
(() => {
  const nativeToString = Function.prototype.toString;
  const natives = new WeakMap();
  const shim = function toString() {
    const target = natives.get(this);
    return nativeToString.call(target ?? this);
  };
  natives.set(shim, nativeToString);
  Function.prototype.toString = shim;
  const disguise = (fake, native, name) => {
    Object.defineProperty(fake, 'name', { value: name, configurable: true });
    Object.defineProperty(fake, 'length', { value: native.length, configurable: true });
    natives.set(fake, native);
    return fake;
  };
  const desc = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
  if (desc && desc.get) {
    const get = disguise(function () { return false; }, desc.get, 'get webdriver');
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get, set: desc.set, enumerable: desc.enumerable, configurable: desc.configurable,
    });
  }
  const origAttach = Element.prototype.attachShadow;
  Element.prototype.attachShadow = disguise(function (init) {
    const root = origAttach.call(this, init);
    if (init && init.mode === 'closed') this.__closedRoot = root;
    return root;
  }, origAttach, 'attachShadow');
})();
"""

_OFF_SCREEN = "--window-position=-32000,-32000"
_LAUNCH_ARGS = [_OFF_SCREEN, "--disable-blink-features=AutomationControlled",
                "--window-size=1280,900",

                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling",

                "--disk-cache-size=1", "--media-cache-size=1"]

SNAPSHOTS_KEPT = 10

# The window a browser step works in: opened, recorded and closed by the app, so the step writes only the page work
def browser_page(on_screen: bool = False, **context_options):
    from contextlib import contextmanager

    @contextmanager
    def _open():
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "this step drives a browser and the app's own playwright package could not be imported - the app installs it at its first start, so start Cryogram again") from e

        from step_types.browser import APP_WINDOW_OPTIONS
        for own in APP_WINDOW_OPTIONS:
            context_options.pop(own, None)

        with diskguard.harness():
            pw = sync_playwright().start()
        front = _front_app()
        args = list(_LAUNCH_ARGS)
        if on_screen:
            args = [a for a in args if a != _OFF_SCREEN]

        exe = browser_executable()
        where = {"executable_path": exe} if exe else {"channel": "chrome"}
        with diskguard.harness():
            ctx = pw.chromium.launch_persistent_context(
                browser_profile(), headless=False,
                record_har_path=browser_har_path(), record_har_content="embed",
                args=args, ignore_default_args=["--enable-automation"],
                **where, **context_options)
        ctx.add_init_script(_PAGE_PATCH)
        _put_session_back(ctx)
        _watch_calls(ctx)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        _ctx["browser"] = ctx

        _ctx["window_opened"] = True
        _window_on_screen(page, on_screen)
        _hand_focus_back(front)
        try:
            yield page
        finally:
            _ctx["browser"] = None
            try:
                snapshot_page(page)
            except Exception:
                pass
            _keep_session(ctx)
            try:
                ctx.close()
            finally:
                pw.stop()
    return _open()

STORAGE_STATE_NAME = "cryogram_storage_state.json"

# Puts back the session the last window of this workflow kept, for any cookie the profile has lost
def _put_session_back(ctx) -> None:
    import json
    import time as _time
    path = os.path.join(browser_profile(), STORAGE_STATE_NAME)
    if not os.path.exists(path):
        return
    try:
        with diskguard.harness():
            with open(path) as f:
                saved = (json.load(f) or {}).get("cookies") or []
        have = {(c.get("name"), c.get("domain"), c.get("path")) for c in ctx.cookies()}
        now = _time.time()
        missing = [c for c in saved
                   if (c.get("name"), c.get("domain"), c.get("path")) not in have
                   and not (isinstance(c.get("expires"), (int, float))
                            and 0 < c["expires"] < now)]
        if missing:
            ctx.add_cookies(missing)
    except Exception:
        pass

# Saves the window's whole session into the workflow's profile before the window closes
def _keep_session(ctx) -> None:
    try:
        with diskguard.harness():
            ctx.storage_state(path=os.path.join(browser_profile(), STORAGE_STATE_NAME))
    except Exception:
        pass

# The app the person is working in, so the window Chrome steals focus with can hand it straight back
def _front_app() -> str:
    if sys.platform != "darwin":
        return ""
    try:
        import subprocess
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        info = subprocess.run(["lsappinfo", "info", "-only", "name", asn],
                              capture_output=True, text=True, timeout=5).stdout
        return info.split('"')[-2] if '"' in info else ""
    except Exception:
        return ""

def _hand_focus_back(app: str) -> None:
    if not app or app == "System Settings" or sys.platform != "darwin":
        return
    try:
        import subprocess
        subprocess.run(["open", "-a", app], timeout=5)
    except Exception:
        pass

# Keeps a copy of the page as it stands, for the failure report; the newest few are kept
def snapshot_page(page) -> str:
    html = _page_html(page)
    keep = _ctx.setdefault("snapshots", [])
    path = str(_scratch_dir() / f"page-{len(keep) + 1}.html")
    with diskguard.harness():
        with open(path, "w") as f:
            f.write(html)
    keep.append({"url": _page_url(page), "path": path,
                 "bytes": len(html.encode("utf-8")), **_page_shape(page)})
    for old in keep[:-SNAPSHOTS_KEPT]:
        try:
            with diskguard.harness():
                os.remove(old["path"])
        except OSError:
            pass
    del keep[:-SNAPSHOTS_KEPT]
    return path

# How much of the page this copy actually holds, so a half-loaded list is visible rather than assumed
_SHAPE_JS = """() => ({
  seen: Math.round(window.scrollY + window.innerHeight),
  height: Math.round(document.documentElement.scrollHeight),
  elements: document.getElementsByTagName('*').length,
  links: document.getElementsByTagName('a').length
})"""

def _page_shape(page) -> dict:
    try:
        got = page.evaluate(_SHAPE_JS)
        return {k: int(got[k]) for k in ("seen", "height", "elements", "links")
                if isinstance(got, dict) and k in got}
    except Exception:
        return {}

# The page copies this step has kept so far, newest last
def kept_snapshots() -> list:
    return list(_ctx.get("snapshots") or [])

def _page_url(page) -> str:
    try:
        return str(page.url)
    except Exception:
        return ""

# Goes to a page and keeps going until it is really there; this is where the step means to be
def browser_goto(page, url: str, tries: int = 3, settle_ms: int = 1200):
    import time as _time
    last = ""
    for attempt in range(max(1, tries)):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(settle_ms)
            _ctx["meant_to_be"] = str(url)
            snapshot_page(page)
            return page
        except Exception as e:
            last = str(e)
            if attempt + 1 < max(1, tries):
                _time.sleep(2 * (attempt + 1))
    unexpected_input(f"could not open {url} after {tries} tries",
                     found=last[:300], expected=f"the page at {url}")

# Notes every call the window makes, so the step can see what a page loaded without reading the recording
def _watch_calls(ctx) -> None:
    import time as _time
    keep = _ctx.setdefault("calls", [])
    _ctx["calls_t0"] = _time.time()

    def note(response) -> None:
        try:
            _ctx["calls_seen"] = _ctx.get("calls_seen", 0) + 1
            req = response.request
            headers = response.headers or {}
            size = headers.get("content-length")
            line = {"at": round(_time.time() - _ctx["calls_t0"], 1),
                    "name": _file_name(response.url),
                    "method": req.method,
                    "url": response.url,
                    "status": response.status,
                    "kind": req.resource_type,
                    "type": (headers.get("content-type") or "").split(";")[0]}
            if size is not None and str(size).strip().isdigit():
                line["bytes"] = int(size)
            keep.append(line)
        except Exception:
            pass

    try:
        ctx.on("response", note)
    except Exception:
        pass

# What a call's address calls the thing it returned, for a step working in files
def _file_name(url: str) -> str:
    from urllib.parse import urlsplit, unquote
    try:
        last = urlsplit(str(url)).path.rsplit("/", 1)[-1]
    except Exception:
        return ""
    return unquote(last)[:120]

DATA_KINDS = ("xhr", "fetch", "websocket", "eventsource", "document")

# A call's address with its numbers and paging masked, so repeats of one address fold together
def _call_pattern(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        u = urlsplit(str(url))
        path = re.sub(r"\d+", "#", u.path)
        return f"{u.scheme}://{u.netloc}{path}"
    except Exception:
        return str(url)

# The listing folded, never cut: repeats of one address become one row, other kinds one row each with a count, every row saying how to unfold
def fold_calls(calls: list) -> list:
    rows: list = []
    groups: dict = {}
    kinds: dict = {}
    for i, c in enumerate(calls or []):
        line = {"id": i, **c}
        kind = str(c.get("kind") or "other")
        data = kind in DATA_KINDS or "json" in str(c.get("type") or "").lower()
        if not data:
            k = kinds.setdefault(kind, {"kind": kind, "count": 0, "ids": [], "bytes": 0})
            k["count"] += 1
            k["ids"].append(i)
            k["bytes"] += int(c.get("bytes") or 0)
            continue
        key = (str(c.get("method") or ""), _call_pattern(c.get("url") or ""))
        g = groups.get(key)
        if g is None:
            groups[key] = {"row": line, "ats": [float(c.get("at") or 0)], "ids": [i],
                           "statuses": {c.get("status")}}
            rows.append(groups[key])
        else:
            g["ats"].append(float(c.get("at") or 0))
            g["ids"].append(i)
            g["statuses"].add(c.get("status"))
    out: list = []
    for g in rows:
        if len(g["ids"]) == 1:
            out.append(g["row"])
            continue
        ats = sorted(g["ats"])
        gaps = [b - a for a, b in zip(ats, ats[1:])]
        first = g["row"]
        out.append({"id": g["ids"][0], "ids": g["ids"], "count": len(g["ids"]),
                    "method": first.get("method"), "url": first.get("url"),
                    "pattern": _call_pattern(first.get("url") or ""),
                    "kind": first.get("kind"), "type": first.get("type"),
                    "status": sorted(s for s in g["statuses"] if s is not None),
                    "first_at": ats[0], "last_at": ats[-1],
                    "interval": round(sum(gaps) / len(gaps), 1) if gaps else 0,
                    "note": f"{len(g['ids'])} calls to one address, folded; "
                            "unfold with read_input(\"calls\", ids=[...])"})
    for kind, k in kinds.items():
        out.append({"kind": kind, "count": k["count"], "ids": k["ids"],
                    **({"bytes": k["bytes"]} if k["bytes"] else {}),
                    "note": f"{k['count']} {kind} calls, folded; the full inventory is "
                            f"read_input(\"calls\", kind=\"{kind}\")"})
    return out

# Every call this step's window made, newest last, with how many were left out
def browser_calls() -> dict:
    keep = list(_ctx.get("calls") or [])
    return {"calls": keep, "listed": len(keep),
            "total": int(_ctx.get("calls_seen") or 0)}

# The file Chrome records its traffic into; the app keeps it when the step fails
def browser_har_path() -> str:
    p = _ctx.get("har")
    if not p:
        p = str(_scratch_dir() / "browser-traffic.har")
        _ctx["har"] = p
    return p

# The recording Chrome was asked to write, if the step asked for one
def har_recording() -> Optional[str]:
    return _ctx.get("har")

_SNAPSHOT_JS = r"""() => {
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const attr = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  const RAW = new Set(['script', 'style']);
  function ser(root, raw) {
    let out = '';
    for (const n of root.childNodes) {
      if (n.nodeType === 3) out += raw ? n.textContent : esc(n.textContent);
      else if (n.nodeType === 8) out += '<!--' + n.textContent + '-->';
      else if (n.nodeType === 1) {
        const tag = n.tagName.toLowerCase();
        out += '<' + tag;
        for (const a of n.attributes) out += ' ' + a.name + '="' + attr(a.value) + '"';
        out += '>';
        if (n.shadowRoot) out += '<template shadowrootmode="open">' + ser(n.shadowRoot, false) + '</template>';
        out += ser(n, RAW.has(tag)) + '</' + tag + '>';
      }
    }
    return out;
  }
  return '<!DOCTYPE html>\n' + ser(document, false);
}"""

# The whole page as it stands now, shadow roots included, as html
def _page_html(page) -> str:
    return page.evaluate(_SNAPSHOT_JS)

# How step code hands back a declared output
def write_output(name: str, value: Any) -> None:
    _ctx["outputs"][name] = value

# A long step calls this between items; it resets the silence timeout and shows the note as live progress
def heartbeat(note: Optional[str] = None) -> None:
    path = os.environ.get("CRYOGRAM_HEARTBEAT_FILE")
    if not path:
        return
    try:
        with open(path, "w") as f:
            f.write(str(note or ""))
    except OSError:
        pass

# Records one finished item, so a step that fails partway continues without repeating it
def checkpoint(key: str, value: Any = True) -> None:
    mark_sent()
    path = os.environ.get("CRYOGRAM_CHECKPOINT_FILE")
    if not path:
        return
    try:
        done = checkpoints()
        done[str(key)] = value
        tmp = f"{path}.tmp"
        with diskguard.harness():
            with open(tmp, "w") as f:
                json.dump(done, f)
            os.replace(tmp, path)
    except (OSError, TypeError, ValueError) as e:
        raise RuntimeError(f"the step's checkpoint could not be written ({e}), "
                           "so it stopped before the next item; nothing recorded so far will be repeated") from e

# Says a write has gone out, so a failure after it never re-sends on resume; checkpoint() says it for you
def mark_sent() -> None:
    path = os.environ.get("CRYOGRAM_SENT_FILE")
    if not path:
        return
    try:
        with diskguard.harness():
            with open(path, "a"):
                pass
    except OSError:
        pass

# What this step already recorded; a long loop reads it first and skips those keys, so a re-run continues instead of starting over
def checkpoints() -> dict:
    path = os.environ.get("CRYOGRAM_CHECKPOINT_FILE")
    if not path or not os.path.exists(path):
        return {}
    try:
        with diskguard.harness():
            with open(path) as f:
                got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}

# Stores a produced file and returns the reference a file-typed output carries
def write_file(name: str, data) -> str:
    import mimetypes

    from storage import blobstore
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("write_file takes text or bytes")
    mime = mimetypes.guess_type(str(name))[0] or "application/octet-stream"
    with diskguard.harness():
        ref = blobstore.put(bytes(data), mime, {"name": str(name)}, owner=_owner())

    _ctx["files"][str(name)] = ref
    return ref

# Step code asks for a secret by name; the value is injected at the process boundary and the AI never sees it
def get_secret(name: str) -> str:
    val = os.environ.get(name)
    if val is None:
        raise KeyError(f"secret {name!r} not injected")
    return val

# Redacts known secret values from anything written, logged or returned to the model
def scrub(text: str, secret_values: list[str]) -> str:
    for v in secret_values:
        if v:
            text = text.replace(v, "***")
    return text

# Redacts secret values inside a structure, before it is serialised, so escaping cannot hide one
def scrub_value(value, secret_values: list[str]):
    if isinstance(value, str):
        return scrub(value, secret_values)
    if isinstance(value, dict):
        return {k: scrub_value(v, secret_values) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_value(v, secret_values) for v in value]
    return value

# Best-effort parse of a completion into data; failure returns a flagged dict so the run routes it rather than crashing
def _parse_structured(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return {"_unparsed": True, "_text": text}
