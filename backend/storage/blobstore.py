# Content-addressed byte store: files and large payloads live here, everything else keeps a blob:<sha256> reference
from __future__ import annotations

import hashlib
import json
import os
import uuid
import re
from typing import Optional

import config
from storage import db

from storage.secrets_store import OWNER_APP, workflow_owner

_HASH = re.compile(r"^[0-9a-f]{64}$")

def _ensure() -> None:
    config.BLOBS_DIR.mkdir(parents=True, exist_ok=True)

def _hash(ref: str) -> str:
    h = ref.split("blob:", 1)[1] if ref.startswith("blob:") else ref
    if not _HASH.match(h or ""):
        raise FileNotFoundError(f"not a blob reference: {ref!r}")
    return h

def _path(h: str):
    return config.BLOBS_DIR / h

# Stores bytes under their own hash and returns the blob reference; identical content stores once, named per owner
def put(data: bytes, mime: str = "application/octet-stream",
        meta: Optional[dict] = None, owner: str = "") -> str:
    if not owner:
        raise ValueError("a blob needs an owner: the app, a workflow or an environment")
    _ensure()
    h = hashlib.sha256(data).hexdigest()
    blob = _path(h)
    if not blob.exists():
        tmp = blob.with_name(f"{blob.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_bytes(data)
        try:
            os.replace(tmp, blob)
        except OSError:
            if not blob.exists():
                raise
            tmp.unlink(missing_ok=True)
    if not db.blob_meta_get(owner, h):
        db.blob_meta_put(owner, h, mime, len(data), meta)
    return f"blob:{h}"

# Gives up one owner's copy of a blob; the bytes go only when no owner is left holding them
def forget(ref: str, owner: str) -> bool:
    if not owner:
        raise ValueError("forgetting a blob needs the owner giving it up")
    try:
        h = _hash(ref)
    except FileNotFoundError:
        return False
    if db.blob_meta_drop(owner, h):
        return False
    blob = _path(h)
    if not blob.exists():
        return False
    try:
        blob.unlink()
    except OSError:
        return False
    return True

# Gives up every blob one owner holds and removes the files it was the last to hold; returns how many files went
def forget_owner(owner: str) -> int:
    if not owner:
        raise ValueError("forgetting an owner's blobs needs the owner")
    gone = 0
    for h in db.blob_meta_hashes(owner):
        if db.blob_meta_drop(owner, h):
            continue
        try:
            _path(h).unlink()
            gone += 1
        except OSError:
            continue
    return gone

# Removes every blob file no owner holds a row for; returns how many went
def sweep_orphans() -> int:
    if not config.BLOBS_DIR.is_dir():
        return 0
    known = db.blob_meta_known_hashes()
    gone = 0
    for f in config.BLOBS_DIR.iterdir():
        if not f.is_file() or not _HASH.match(f.name) or f.name in known:
            continue
        try:
            f.unlink()
            gone += 1
        except OSError:
            continue
    return gone

def exists(ref: str) -> bool:
    try:
        return _path(_hash(ref)).exists()
    except FileNotFoundError:
        return False

def get(ref: str) -> bytes:
    return _path(_hash(ref)).read_bytes()

def stat(ref: str, owner: Optional[str] = None) -> dict:
    try:
        h = _hash(ref)
    except FileNotFoundError:
        return {}
    rec = db.blob_meta_get(owner, h) if owner else None
    if not rec:
        rec = db.blob_meta_any(h)
    if not rec:
        return {}
    return {"mime": rec["mime"], "size": rec["size"], "ts": rec["ts"],
            **(rec["meta"] or {})}
