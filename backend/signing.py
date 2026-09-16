# Release signing: updates and the launch page verify against an offline Ed25519 key pinned in config
from __future__ import annotations

import json
from typing import Any, Optional

import config

SIG_FIELD = "sig"

# The signed bytes: the document minus its sig field, serialised one canonical way
def canonical_bytes(doc: dict) -> bytes:
    body = {k: v for k, v in doc.items() if k != SIG_FIELD}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()

# Used by the release tooling only; the app never holds the private key
def sign(doc: dict, private_key_bytes: bytes) -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    out = {k: v for k, v in doc.items() if k != SIG_FIELD}
    out[SIG_FIELD] = {"alg": "ed25519",
                      "sig": key.sign(canonical_bytes(out)).hex()}
    return out

# Fail closed: once a key is pinned, an unsigned or tampered payload is refused wherever it would have been trusted
def verify(doc: Any, pubkey_hex: Optional[str] = None) -> bool:
    pub = (pubkey_hex if pubkey_hex is not None
           else config.RELEASE_PUBKEY).strip()
    if not pub:
        return True
    if not isinstance(doc, dict):
        return False
    sig = doc.get(SIG_FIELD)
    if not isinstance(sig, dict) or sig.get("alg") != "ed25519":
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)
        from cryptography.exceptions import InvalidSignature
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub))
        key.verify(bytes.fromhex(str(sig.get("sig") or "")),
                   canonical_bytes(doc))
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False
