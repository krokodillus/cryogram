# Bounds where step code may read and write on this computer: its own scratch, the app's helpers, and the folders the user allowed
from __future__ import annotations

import builtins
import contextlib
import io
import os
import sys
import sysconfig
import tempfile
import threading

BLOCK_PREFIX = "path blocked: "

_state: dict = {"installed": False, "harness": [], "readonly": [], "data_dir": None,
                "allowed": [], "user": [], "system": []}
_local = threading.local()

# The one message shape every reader of a blocked path parses
def message(path) -> str:
    return f"{BLOCK_PREFIX}{str(path)!r} is outside this workflow's allowed folders"

# Inside this block the capability surface may reach the app's own files (blobs, checkpoints) on the step's behalf
@contextlib.contextmanager
def harness():
    depth = getattr(_local, "depth", 0)
    _local.depth = depth + 1
    try:
        yield
    finally:
        _local.depth = depth

def _in_harness() -> bool:
    return getattr(_local, "depth", 0) > 0

def _norm(p) -> str:
    try:
        return os.path.realpath(os.path.abspath(os.fspath(p)))
    except (TypeError, ValueError):
        return ""

def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)

# The read-only folders every Python process needs: the interpreter, its packages, the OS libraries, temp
def system_roots() -> list[str]:
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix,
             tempfile.gettempdir()}
    for k in ("stdlib", "platstdlib", "purelib", "platlib", "data", "include"):
        try:
            roots.add(sysconfig.get_path(k))
        except (KeyError, AttributeError):
            pass
    if os.name == "nt":
        for k in ("SYSTEMROOT", "WINDIR", "PROGRAMFILES", "PROGRAMFILES(X86)",
                  "PROGRAMDATA"):
            if os.environ.get(k):
                roots.add(os.environ[k])
    else:
        roots.update(["/usr", "/lib", "/lib64", "/lib32", "/etc", "/dev", "/proc",
                      "/sys", "/bin", "/sbin", "/opt", "/System", "/Library",
                      "/Applications", "/private/etc", "/var/db", "/nix"])
    for k in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"):
        if os.environ.get(k):
            roots.add(os.environ[k])
    return sorted({_norm(r) for r in roots if r} - {""})

# Whether a path may be touched: the harness's own folders first, then the app's data folder is refused, then the allowed folders, then the system's
def allowed(path, write: bool = False) -> bool:
    if not _state["installed"] or _in_harness() or isinstance(path, int):
        return True
    p = _norm(path)
    if not p:
        return True
    if any(_under(p, r) for r in _state["harness"]):
        return True
    if any(_under(p, r) for r in _state["readonly"]):
        return not write
    dd = _state["data_dir"]
    if dd and _under(p, dd):
        return False
    if any(_under(p, r) for r in _state["allowed"]):
        return True
    if write:
        return False
    return any(_under(p, r) for r in _state["system"])

def _check(path, write: bool = False) -> None:
    if not allowed(path, write):
        raise PermissionError(message(path))

# Patches the file-opening and folder-listing functions in this process; every other route (shelling out) is documented as open
def install(allowed_roots: list, harness_roots: list, data_dir,
            readonly_roots: list | None = None) -> None:
    _state["allowed"] = sorted({_norm(r) for r in list(allowed_roots or [])
                                + [tempfile.gettempdir()] if r} - {""})

    _state["user"] = sorted({_norm(r) for r in list(allowed_roots or []) if r} - {""})
    prefixes = [sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix]
    _state["harness"] = sorted({_norm(r) for r in list(harness_roots or []) if r} - {""})
    _state["readonly"] = sorted({_norm(r) for r in list(readonly_roots or []) + prefixes
                                 if r} - {""})
    _state["data_dir"] = _norm(data_dir) if data_dir else None
    _state["system"] = system_roots()
    _state["installed"] = True

    real_open = builtins.open

    def g_open(file, mode="r", *a, **k):
        _check(file, any(c in str(mode) for c in "wax+"))
        return real_open(file, mode, *a, **k)

    builtins.open = g_open
    io.open = g_open

    real_os_open = os.open
    wflags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

    def g_os_open(path, flags, *a, **k):
        _check(path, bool(flags & wflags))
        return real_os_open(path, flags, *a, **k)

    os.open = g_os_open

    def _wrap_read(name):
        real = getattr(os, name)

        def g(path=".", *a, **k):
            _check("." if path is None else path)
            return real(path, *a, **k) if path is not None else real(*a, **k)
        g.__name__ = name
        setattr(os, name, g)

    def _wrap_write(name, both=False):
        real = getattr(os, name)

        def g(path, *a, **k):
            _check(path, True)
            if both and a:
                _check(a[0], True)
            return real(path, *a, **k)
        g.__name__ = name
        setattr(os, name, g)

    for n in ("listdir", "scandir"):
        _wrap_read(n)
    for n in ("rmdir", "mkdir", "makedirs", "truncate"):
        _wrap_write(n)

    # A delete in one of the person's own folders goes to the bin, not away
    def _wrap_delete(name):
        real = getattr(os, name)

        def g(path, *a, **k):
            _check(path, True)
            if not _in_harness() and any(_under(_norm(path), r)
                                         for r in _state["user"]):
                from runtime import recycle

                with harness():
                    went = recycle.to_bin(path)
                if went:
                    return None
            return real(path, *a, **k)
        g.__name__ = name
        setattr(os, name, g)

    for n in ("remove", "unlink"):
        _wrap_delete(n)
    for n in ("rename", "replace"):
        _wrap_write(n, both=True)

    import sqlite3
    real_connect = sqlite3.connect

    def g_connect(database, *a, **k):
        db = os.fspath(database) if not isinstance(database, str) else database
        if isinstance(db, str) and db and db != ":memory:":
            target, write = db, True
            if db.startswith("file:"):
                from urllib.parse import unquote, urlparse
                u = urlparse(db)
                target = unquote(u.path)
                write = "mode=ro" not in (u.query or "")
            _check(target, write)
        return real_connect(database, *a, **k)

    sqlite3.connect = g_connect
