# Secrets encrypted at rest; the key lives in the OS keychain where available, never beside the data
from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

from storage import db

_PREFIX = "enc1:"
_key_cache: bytes | None = None
_warned: set = set()

def _say_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        print(msg)

def _fallback_key_file() -> Path:
    env = os.environ.get("CRYOGRAM_SECRET_KEY_FILE")
    if env:
        return Path(env)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Cryogram" / "secret.key"
    return Path.home() / ".config" / "cryogram" / "secret.key"

def _read_key_file(path: Path) -> bytes | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if os.name == "nt" and raw[:4] == b"DPA1":
        try:
            return _dpapi(raw[4:], protect=False)
        except OSError:
            return None
    return raw if len(raw) == 32 else None

def _write_key_file(path: Path, key: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = key
    if os.name == "nt":
        try:
            raw = b"DPA1" + _dpapi(key, protect=True)
        except OSError:
            pass
    path.write_bytes(raw)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

# Binds the key file to this user on this machine, so a copied file decrypts nowhere else
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    import ctypes.wintypes as wt

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inb = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = (ctypes.windll.crypt32.CryptProtectData if protect
          else ctypes.windll.crypt32.CryptUnprotectData)
    if not fn(ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)

def _keychain_get() -> bytes | None:
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "Cryogram",
             "-a", "secrets-key", "-w"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        key = bytes.fromhex(r.stdout.strip())
    except ValueError:
        return None
    return key if len(key) == 32 else None

def _keychain_set(key: bytes) -> bool:
    try:
        r = subprocess.run(
            ["security", "add-generic-password", "-s", "Cryogram",
             "-a", "secrets-key", "-w", key.hex(), "-U"],
            capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

# Loads or creates the encryption key: the login keychain on macOS, DPAPI on Windows, else an owner-only file outside the data folder
def _load_key() -> bytes:
    pinned = os.environ.get("CRYOGRAM_SECRET_KEY_FILE")
    path = _fallback_key_file()
    if not pinned and sys.platform == "darwin":
        key = _keychain_get()
        if key:
            return key

        key = _read_key_file(path)
        if key:
            _keychain_set(key)
            return key
        key = os.urandom(32)
        if _keychain_set(key):
            return key

    key = _read_key_file(path)
    if key:
        return key
    key = os.urandom(32)
    _write_key_file(path, key)
    return key

def _key() -> bytes:
    global _key_cache
    if _key_cache is None:
        _key_cache = _load_key()
    return _key_cache

# Removes the encryption key from wherever it lives, so deleting all data leaves nothing of the app behind
def forget_key() -> None:
    global _key_cache
    _key_cache = None
    pinned = os.environ.get("CRYOGRAM_SECRET_KEY_FILE")
    if not pinned and sys.platform == "darwin":
        try:
            subprocess.run(["security", "delete-generic-password", "-s",
                            "Cryogram", "-a", "secrets-key"],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        _fallback_key_file().unlink(missing_ok=True)
    except OSError:
        pass

def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        return None
    return AESGCM(_key())

class SecretsUnavailable(RuntimeError):
    pass

def _encrypt(value: str) -> str:
    gcm = _aesgcm()
    if gcm is None:
        raise SecretsUnavailable(
            "This secret can't be stored: the part of Cryogram that encrypts secrets is missing. Quit and start Cryogram again; the launcher reinstalls it.")
    nonce = os.urandom(12)
    ct = gcm.encrypt(nonce, value.encode(), None)
    return _PREFIX + base64.b64encode(nonce + ct).decode()

# A value that cannot be decrypted reads as unset with a plain explanation, never a crash
def _decrypt(name: str, stored: str) -> str | None:
    if not stored.startswith(_PREFIX):
        return stored
    gcm = _aesgcm()
    if gcm is None:
        _say_once("no-crypto", "[secrets] the cryptography package is not installed - stored values cannot be read until it is")
        return None
    try:
        raw = base64.b64decode(stored[len(_PREFIX):])
        return gcm.decrypt(raw[:12], raw[12:], None).decode()
    except Exception:
        _say_once(f"undec:{name}",
                  f'[secrets] "{name}" cannot be read on this machine (the '
                  "key that protected it is gone - this happens after a keychain reset or a move to a new computer). Enter it again where it is used.")
        return None

OWNER_APP = "app"

def workflow_owner(workflow_id: str) -> str:
    return f"wfl:{workflow_id}"

def environment_owner(env_id: str) -> str:
    return f"env:{env_id}"

# Values are encrypted before they reach the database; every row belongs to one owner
def set_secret(name: str, value: str, owner: str) -> None:
    db.secret_set(_owner(owner), name, _encrypt(value))

def has_secret(name: str, owner: str) -> bool:
    return bool(db.secret_get(_owner(owner), name))

def get_secret(name: str, owner: str) -> str | None:
    stored = db.secret_get(_owner(owner), name)
    if stored is None:
        return None
    return _decrypt(name, stored)

def names(owner: str) -> list[str]:
    return db.secret_names(_owner(owner))

def delete_secret(name: str, owner: str) -> None:
    db.secret_delete(_owner(owner), name)

SCRUB_MIN_LEN = 4

# The one cross-owner read: every stored value, no names - for scrubbing outgoing text only
def all_values(min_len: int = SCRUB_MIN_LEN) -> list[str]:
    out = []
    for owner, name, stored in db.secret_rows():
        try:
            v = _decrypt(name, stored)
        except Exception:
            v = None
        if v and len(v) >= min_len:
            out.append(v)
    return out

def _owner(owner: str) -> str:
    if not isinstance(owner, str) or not owner:
        raise ValueError("a secret needs an owner: the app, a workflow or an environment")
    return owner
