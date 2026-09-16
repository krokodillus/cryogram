# Step code may not start a process; the harness's own browser launch may
from __future__ import annotations

import os
import subprocess

from runtime import diskguard

BLOCK_PREFIX = "process blocked: "

_state: dict = {"installed": False}

# The one message shape, so every reader recognises a refused process
def message(what) -> str:
    return (f"{BLOCK_PREFIX}{str(what)!r} - a step may not start another "
            "program. Work in Python: declare the package the step needs. A browser is the one exception and it belongs to a browser step, which opens its window through the harness.")

def _refuse(what):
    if not diskguard._in_harness():
        raise PermissionError(message(what))

# Patches process creation in this process; the harness's own launches pass
def install() -> None:
    if _state["installed"]:
        return
    _state["installed"] = True

    real_popen_init = subprocess.Popen.__init__

    def g_popen_init(self, args, *a, **k):
        _refuse(args[0] if isinstance(args, (list, tuple)) and args else args)
        return real_popen_init(self, args, *a, **k)

    subprocess.Popen.__init__ = g_popen_init

    def _wrap(mod, name):
        real = getattr(mod, name, None)
        if real is None:
            return

        def g(*a, **k):
            _refuse(a[0] if a else name)
            return real(*a, **k)
        g.__name__ = name
        setattr(mod, name, g)

    for n in ("system", "popen", "fork", "forkpty", "posix_spawn",
              "posix_spawnp", "execv", "execve", "execvp", "execvpe",
              "execl", "execle", "execlp", "execlpe",
              "spawnv", "spawnve", "spawnvp", "spawnvpe",
              "spawnl", "spawnle", "spawnlp", "spawnlpe"):
        _wrap(os, n)
