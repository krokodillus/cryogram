# Sends a deleted file to the computer's own bin instead of unlinking it
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

# A free name in the bin, so a second file of the same name does not clobber the first
def _free(folder: Path, name: str) -> Path:
    target = folder / name
    if not target.exists():
        return target
    stem, dot, ext = name.partition(".")
    for n in range(1, 1000):
        stamp = time.strftime("%H-%M-%S")
        cand = folder / f"{stem} {stamp}{'' if n == 1 else f' {n}'}{dot}{ext}"
        if not cand.exists():
            return cand
    return folder / f"{name}.{int(time.time())}"

def _mac(path: Path) -> bool:
    trash = Path.home() / ".Trash"
    if not trash.is_dir():
        return False
    shutil.move(str(path), str(_free(trash, path.name)))
    return True

def _freedesktop(path: Path) -> bool:
    home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    trash = Path(home) / "Trash"
    files, info = trash / "files", trash / "info"
    try:
        files.mkdir(parents=True, exist_ok=True)
        info.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    target = _free(files, path.name)
    (info / (target.name + ".trashinfo")).write_text(
        "[Trash Info]\n"
        f"Path={quote(str(path))}\n"
        f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    shutil.move(str(path), str(target))
    return True

def _windows(path: Path) -> bool:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT = 3, 0x40, 0x10, 0x4
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(path) + "\0\0", None,
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT, False, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0

# True when the file went to the bin; False means delete it the ordinary way
def to_bin(path) -> bool:
    try:
        p = Path(path)
        if not p.exists() or p.is_dir():
            return False
        if sys.platform == "darwin":
            return _mac(p)
        if sys.platform == "win32":
            return _windows(p)
        return _freedesktop(p)
    except Exception:
        return False
