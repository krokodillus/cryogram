# Non-secret app settings in app.db; the defaults here are the one source other modules read from
from __future__ import annotations

import json
import uuid
from typing import Any

import config
from storage import secrets_store

GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_PROVIDER_SPECS: tuple[dict, ...] = (
    {"id": "anthropic", "name": "Anthropic", "adapter": "anthropic",
     "endpoint": "", "key": "ANTHROPIC", "host": "",
     "key_url": "https://console.anthropic.com/settings/keys"},
    {"id": "openai", "name": "OpenAI", "adapter": "openai",
     "endpoint": "", "key": "OPENAI", "host": "",
     "key_url": "https://platform.openai.com/api-keys"},
    {"id": "gemini", "name": "Gemini", "adapter": "openai",
     "endpoint": GEMINI_OPENAI_BASE, "key": "GEMINI",
     "host": "generativelanguage.googleapis.com",
     "key_url": "https://aistudio.google.com/api-keys"},
)

DEFAULT_PROVIDER_IDS = tuple(sp["id"] for sp in DEFAULT_PROVIDER_SPECS)

KEY_URLS = {sp["id"]: sp["key_url"] for sp in DEFAULT_PROVIDER_SPECS}

def mint_provider_id() -> str:
    return "p_" + uuid.uuid4().hex[:8]

DEFAULTS: dict[str, Any] = {
    "server": {"host": config.DEFAULT_HOST, "port": config.DEFAULT_PORT},

    "sandbox": {"egress_mode": "allowlist", "egress_allowlist": []},

    "master_ai": {"model": "", "effort": "medium"},

    "default_provider": "",

    "providers": [],

    "preferences": {"notifications": True, "check_updates": True,
                    "share_reports": "ask"},

    "update": {"url": "https://api.github.com/repos/krokodillus/cryogram/releases/latest"},

    "report": {"url": "https://cryogram.app/api/report"}
}

_LEGACY_TOP = ("profiles", "tier_map", "models", "storage")
_LEGACY_MASTER = ("available_models", "profile", "default_model", "key_name")

def _merge(base: dict, over: dict) -> dict:
    import copy as _copy
    out = {k: (_copy.deepcopy(v) if isinstance(v, dict) else v)
           for k, v in base.items()}
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out

def _strip_legacy(s: dict[str, Any]) -> dict[str, Any]:
    for k in _LEGACY_TOP:
        s.pop(k, None)
    for k in _LEGACY_MASTER:
        s.get("master_ai", {}).pop(k, None)

    (s.get("sandbox") or {}).pop("mode", None)
    return s

EFFORT_LEVELS: dict[str, list[str]] = {
    "anthropic": ["low", "medium", "high", "xhigh", "max"],
    "codex": ["minimal", "low", "medium", "high", "xhigh"],
}
EFFORT_DEFAULT = "medium"

# The builder's thinking effort level for the engine family it runs on, medium unless the tab set another
def builder_effort(family: str = "anthropic") -> str:
    level = str((get().get("master_ai") or {}).get("effort") or "").strip().lower()
    return level if level in EFFORT_LEVELS.get(family, []) else EFFORT_DEFAULT

def get() -> dict[str, Any]:
    from storage import db
    row = db.get_settings_row()
    if row is not None:
        return _strip_legacy(_merge(DEFAULTS, row))
    return dict(DEFAULTS)

# Backfills a stable id on any provider entry missing one
def _ensure_provider_ids(providers: list) -> None:
    taken = {p.get("id") for p in providers if isinstance(p, dict) and p.get("id")}
    for p in providers:
        if isinstance(p, dict) and not p.get("id"):
            pid = mint_provider_id()
            while pid in taken:
                pid = mint_provider_id()
            p["id"] = pid
            taken.add(pid)

def update(patch: dict[str, Any]) -> dict[str, Any]:
    from storage import db
    merged = _strip_legacy(_merge(get(), patch))
    if "providers" in patch:
        _ensure_provider_ids(merged.get("providers") or [])
    if "providers" in patch or "default_provider" in patch:
        reconcile_providers(merged)
    db.put_settings_row(merged)
    return merged

# A workflow provider is active when it is switched on and has its key; the Default tick always sits on an active one
def provider_active(provider: dict) -> bool:
    if provider.get("use") == "builder":
        return False
    if provider.get("enabled", True) is False:
        return False
    if "local" in (provider.get("tags") or []):
        return True
    key_name = provider.get("key_name") or ""
    return bool(key_name) and secrets_store.has_secret(key_name, secrets_store.OWNER_APP)

# The rule that keeps the Default tick right: it sits on an active card, or on none
def reconcile_providers(s: dict) -> dict:
    providers = s.get("providers") or []
    active = [p.get("id") for p in providers if provider_active(p)]
    current = str(s.get("default_provider") or "")
    if current not in active:
        s["default_provider"] = active[0] if active else ""
    return s

# A key saved or deleted changes which cards are active, so the Default tick is placed again
def note_key_change(key_name: str, present: bool) -> None:
    s = get()
    update({"default_provider": s.get("default_provider", "")})

# What the UI may see: the install token and other private entries are removed first
def public_view() -> dict[str, Any]:
    from storage import model_catalog
    s = get()
    key_names = {p.get("key_name", "") for p in s.get("providers", [])}
    key_names.discard("")
    s = dict(s)

    s.pop("install", None)
    s["secrets"] = {name: secrets_store.has_secret(name, secrets_store.OWNER_APP)
                    for name in sorted(key_names)}
    s["model_catalog"] = model_catalog.get()
    s["effort_levels"] = {k: list(v) for k, v in EFFORT_LEVELS.items()}
    s["effort_default"] = EFFORT_DEFAULT

    s["default_provider_ids"] = list(DEFAULT_PROVIDER_IDS)

    s["providers"] = [dict(p) for p in s.get("providers") or []]
    for p in s["providers"]:
        p["active"] = provider_active(p)
        if p.get("use") != "builder":
            f = model_catalog.facts(p)
            p["reads"] = model_catalog.reads_phrase(f)

            stated = [set(m["reads"]) for m in p.get("models") or []
                      if isinstance(m.get("reads"), list)]
            reads = set().union(*stated) if stated else set(f.get("reads") or [])
            p["reads_kinds"] = [k for k in ("pdf", "image") if k in reads]
    return s
