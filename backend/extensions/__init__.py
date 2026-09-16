# The integration seam: each package dropped in this folder adds its own routes, hooks and page
from __future__ import annotations

import importlib
import pkgutil
import threading
from pathlib import Path
from typing import Any, Callable, Optional

# A route segment written as "*" matches anything and is passed to the handler
WILDCARD = "*"

# The file an integration's page is loaded from, inside its web directory
ENTRY_FILE = "main.js"

# An integration's own styling, loaded with its page when the file is there
STYLE_FILE = "style.css"

_ROUTES: dict[tuple[str, tuple[str, ...]], Callable] = {}
_HOOKS: dict[str, list[Callable]] = {}
_VALUES: dict[str, list[Callable]] = {}
_PAGES: list[dict] = []
_ASSETS: dict[str, Path] = {}
_IDS: list[str] = []

# Integrations that could not be loaded, as (name, reason) - read by tests and the log line
FAILED: list[tuple[str, str]] = []

_LOADED = False
_LOCK = threading.Lock()

# What an integration is handed to declare what it adds; nothing lands until register() returns
class Registry:
    def __init__(self, ext_id: str) -> None:
        self.id = ext_id
        self.routes: dict[tuple[str, tuple[str, ...]], Callable] = {}
        self.hooks: list[tuple[str, Callable]] = []
        self.values: list[tuple[str, Callable]] = []
        self.page: Optional[dict] = None
        self.assets: Optional[Path] = None

    # Answers one HTTP path; "*" in the pattern matches a single segment
    def route(self, method: str, pattern, handler: Callable) -> None:
        key = (method.upper(), tuple(pattern))
        if key in self.routes:
            raise ValueError(f"{self.id}: route {method} {'/'.join(pattern)} "
                             "is registered twice")
        self.routes[key] = handler

    # Runs when the named moment happens; the return value is ignored
    def hook(self, name: str, fn: Callable) -> None:
        self.hooks.append((name, fn))

    # Supplies a value the app asks for; the first integration to answer wins
    def value(self, name: str, fn: Callable) -> None:
        self.values.append((name, fn))

    # Gives the integration a sidebar entry and a page, served from its own directory
    def web(self, title: str, directory, icon: str = "") -> None:
        self.assets = Path(directory).resolve()
        self.page = {"id": self.id, "title": title, "icon": icon,
                     "module": f"/ext/{self.id}/{ENTRY_FILE}",
                     "route": f"#/{self.id}"}
        if (self.assets / STYLE_FILE).is_file():
            self.page["style"] = f"/ext/{self.id}/{STYLE_FILE}"

def _merge(reg: Registry) -> None:
    for key in reg.routes:
        if key in _ROUTES:
            method, pattern = key
            raise ValueError(f"{reg.id}: route {method} {'/'.join(pattern)} "
                             "is already answered by another integration")
    _ROUTES.update(reg.routes)
    for name, fn in reg.hooks:
        _HOOKS.setdefault(name, []).append(fn)
    for name, fn in reg.values:
        _VALUES.setdefault(name, []).append(fn)
    if reg.page and reg.assets:
        _PAGES.append(reg.page)
        _ASSETS[reg.id] = reg.assets
    _IDS.append(reg.id)

# Imports every integration in this folder once; a broken one is recorded and skipped
def load() -> None:
    global _LOADED
    with _LOCK:
        if _LOADED:
            return
        _LOADED = True
        here = Path(__file__).resolve().parent
        for name in sorted(m.name for m in pkgutil.iter_modules([str(here)])):
            try:
                module = importlib.import_module(f"{__name__}.{name}")
                register = getattr(module, "register", None)
                if not callable(register):
                    continue
                reg = Registry(name)
                register(reg)
                _merge(reg)
            except Exception as e:
                import traceback
                FAILED.append((name, f"{type(e).__name__}: {e}"))
                print(f"[extensions] {name} could not load and was skipped: "
                      f"{type(e).__name__}: {e}")
                traceback.print_exc()

# Finds the integration that answers this path, with whatever the wildcards matched
def dispatch(method: str, parts) -> Optional[tuple[Callable, list[str]]]:
    load()
    want = method.upper()
    got = tuple(parts)
    exact = _ROUTES.get((want, got))
    if exact is not None:
        return exact, []
    for (m, pattern), handler in _ROUTES.items():
        if m != want or len(pattern) != len(got):
            continue
        caught: list[str] = []
        for seg, real in zip(pattern, got):
            if seg == WILDCARD:
                caught.append(real)
            elif seg != real:
                break
        else:
            return handler, caught
    return None

# Tells every integration that a moment happened; one raising does not stop the others
def fire(name: str, *args: Any) -> None:
    load()
    for fn in list(_HOOKS.get(name, [])):
        try:
            fn(*args)
        except Exception as e:
            FAILED.append((name, f"{type(e).__name__}: {e}"))

# Asks the integrations for a value and returns the first real answer, else None
def first(name: str, *args: Any) -> Any:
    load()
    for fn in list(_VALUES.get(name, [])):
        try:
            got = fn(*args)
        except Exception as e:
            FAILED.append((name, f"{type(e).__name__}: {e}"))
            continue
        if got is not None:
            return got
    return None

# Asks every integration for a value and returns all the real answers, in load order
def every(name: str, *args: Any) -> list:
    load()
    out = []
    for fn in list(_VALUES.get(name, [])):
        try:
            got = fn(*args)
        except Exception as e:
            FAILED.append((name, f"{type(e).__name__}: {e}"))
            continue
        if got is not None:
            out.append(got)
    return out

# The integrations that loaded, by name
def ids() -> list[str]:
    load()
    return list(_IDS)

# What the page needs to add a sidebar entry and load each integration
def manifest() -> list[dict]:
    load()
    return [dict(p) for p in _PAGES]

# Resolves one file inside an integration's own directory, refusing anything outside it
def asset(ext_id: str, rel: str) -> Optional[Path]:
    load()
    root = _ASSETS.get(ext_id)
    if not root:
        return None
    target = (root / rel.lstrip("/")).resolve()
    if root not in target.parents and target != root:
        return None
    return target if target.is_file() else None

# Forgets everything loaded, so a test can register a fresh set
def reset() -> None:
    global _LOADED
    with _LOCK:
        _ROUTES.clear()
        _HOOKS.clear()
        _VALUES.clear()
        _PAGES.clear()
        _ASSETS.clear()
        _IDS.clear()
        FAILED.clear()
        _LOADED = False
