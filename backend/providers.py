# The model router: providers, endpoints and keys come from settings; key values stay in the secret store
from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from typing import Any

import config
from modelcall import adapters
from modelcall.adapters import (
    ANTHROPIC_URL, ANTHROPIC_VERSION, MODEL_ERROR_BODY_CHARS, _SSL_CTX,
    _TRUNCATED, _anthropic_url, _err_kind, _err_text, _http_error_text,
    _note_usage, anthropic_messages, anthropic_structured, attachment_blocks,
    clamp_call_timeout, clamp_temperature, clamp_tokens, gemini_chat,
    gemini_structured, openai_chat, openai_structured, take_usage)
from storage import model_catalog
from storage import secrets_store
from storage import settings

adapters.DEFAULT_MAX_TOKENS = config.AI_MAX_TOKENS

# A model that refused a temperature is marked on its rows in the provider settings, so no later call sends one
def _remember_no_temperature(model: str) -> None:
    s = settings.get()
    rows = [dict(p) for p in s.get("providers") or []]
    changed = False
    for p in rows:
        for m in p.get("models") or []:
            if m.get("name") == model and not m.get("no_temperature"):
                m["no_temperature"] = True
                changed = True
    if changed:
        settings.update({"providers": rows})

adapters.remember_no_temperature = _remember_no_temperature
adapters.DEFAULT_CALL_TIMEOUT = config.AI_CALL_TIMEOUT

_PINNED = threading.local()

def _registry() -> list[dict]:
    pinned = getattr(_PINNED, "providers", None)
    return pinned if pinned is not None else settings.get().get("providers", [])

# A run pins the provider registry at start - disabling a provider mid-run changes nothing in flight
@contextlib.contextmanager
def pinned_registry():
    prev = getattr(_PINNED, "providers", None)
    _PINNED.providers = prev if prev is not None \
        else settings.get().get("providers", [])
    try:
        yield
    finally:
        _PINNED.providers = prev

# Absent means enabled - a provider is only off when something explicitly turned it off
def is_enabled(provider: dict) -> bool:
    return provider.get("enabled", True) is not False

# Subscription credentials only power the builder chat; a workflow step's AI call can never use a personal plan
SUBSCRIPTION_AUTHS = frozenset({"claude-subscription", "codex-subscription"})
BUILDER_ONLY_AUTHS = SUBSCRIPTION_AUTHS | {"codex-api-key"}
# Only an enabled workflow provider with an API key path may serve a step's AI call; builder-only models read as missing
def _node_capable(provider: dict) -> bool:
    return (is_enabled(provider)
            and provider.get("use") != "builder"
            and auth_type(provider) not in BUILDER_ONLY_AUTHS)

# Looks a model name up in the right pool: workflow steps see only enabled workflow providers
def find_model(model: str, for_node: bool = False,
               provider_id: str = "") -> tuple[dict, dict]:
    pool = _registry()
    if for_node:
        pool = [p for p in pool if _node_capable(p)]
    if provider_id:
        pool = [p for p in pool if p.get("id") == provider_id]
    matches = [(p, m) for p in pool
               for m in p.get("models", []) if m.get("name") and m["name"] == model]
    if not matches:
        return {}, {}
    if not for_node:
        for p, m in matches:
            if p.get("use") == "builder":
                return p, m
    return matches[0]

# How a provider authenticates; subscription auth works only for the builder, never for a step's AI call
def auth_type(provider: dict) -> str:
    return provider.get("auth") or "api-key"

# The one provider tagged as the builder's, whatever model is ticked; empty when none is set up
def builder_provider() -> dict:
    return next((p for p in _registry() if p.get("use") == "builder"), {})

def provider_for_model(model: str, for_node: bool = False) -> dict:
    return find_model(model, for_node=for_node)[0]

# The one provider the pick looks at first: the settings' default, when it is still an active card
def default_provider_id() -> str:
    want = str(settings.get().get("default_provider") or "")
    return want if any(p.get("id") == want and _node_capable(p) for p in _registry()) else ""

def _reads_all(provider: dict, model: str, kinds: list) -> bool:
    f = model_catalog.facts(provider, model)
    return all(f.get(f"reads_{k}") for k in kinds if k in ("pdf", "image"))

def _ranked(m: dict):
    try:
        return float(m.get("cost")) if m.get("cost") not in (None, "") else None
    except (TypeError, ValueError):
        return None

# The pick is code, never the Builder Agent: the cheapest ranked model on the default provider that reads what the step sends
def pick_model(kinds: list | None = None) -> dict | None:
    pid = default_provider_id()
    if not pid:
        return None
    p = next((q for q in _registry() if q.get("id") == pid), None)
    if not p:
        return None
    rows = []
    for m in p.get("models") or []:
        name = m.get("name")
        if not name or _ranked(m) is None:
            continue
        if not is_ready({"model": name, "provider_id": pid}):
            continue
        if not _reads_all(p, name, list(kinds or [])):
            continue
        q = m.get("quality")
        try:
            q = float(q) if q not in (None, "") else 0.0
        except (TypeError, ValueError):
            q = 0.0
        rows.append((_ranked(m), -q, name))
    if not rows:
        return None
    rows.sort()
    return {"model": rows[0][2], "provider_id": pid,
            "provider": p.get("name") or pid}

# A model the user named, found in the set-up list from their words: the exact name, or the one name the words fit
def match_model(text: str, provider_id: str = "") -> tuple[dict | None, str]:
    want = str(text or "").strip().lower()
    if not want:
        return None, "no model was named"
    rows = [m for m in node_models() if not provider_id or m.get("provider_id", "") == provider_id]
    exact = [m for m in rows if m["name"].lower() == want]
    if exact:
        ready = [m for m in exact if m.get("ready")] or exact
        return sorted(ready, key=lambda m: not m.get("default"))[0], ""
    words = [w for w in want.replace("_", " ").replace("-", " ").replace(".", " ").split() if w]
    def fits(m):
        hay = m["name"].lower().replace("-", " ").replace("_", " ").replace(".", " ")
        return all(w in hay for w in words)
    hits = [m for m in rows if fits(m)]
    ready = [m for m in hits if m.get("ready")]
    pool = ready or hits
    names = sorted({m["name"] for m in pool})
    if len(names) == 1:
        return sorted(pool, key=lambda m: not m.get("default"))[0], ""
    if names:
        return None, (f"'{text}' fits more than one set-up model ({', '.join(names)}); "
                      "ask the user which one, or pass the exact name.")
    listed = sorted({m["name"] for m in rows if m.get("ready")})
    return None, (f"no set-up model matches '{text}'. The set-up models are: "
                  + (", ".join(listed) if listed else "none") + ". Tell the user, and ask them to add it on the Admin page or to choose one of these.")

# The one sentence for a step nothing can be picked for, in the user's words
def no_pick_sentence(kinds: list | None = None, step: str = "") -> str:
    who = f'the step "{step}"' if step else "this step"
    pid = default_provider_id()
    if not pid:
        return (f"No AI provider is set up for {who}: save a key on a provider card "
                "on the Admin page, and it becomes the default.")
    p = next((q for q in _registry() if q.get("id") == pid), {})
    pname = p.get("name") or pid
    wants = [k for k in (kinds or []) if k in ("pdf", "image")]
    if wants and not any(_reads_all(p, m.get("name") or "", wants) for m in p.get("models") or []):
        what = " and ".join({"pdf": "PDFs", "image": "images"}[k] for k in wants)
        return (f"{who[0].upper() + who[1:]} sends {what} to its model, and {pname} does not "
                f"read {what}. Pick a model from another provider in the step's panel, "
                "or ask in chat to add a step that reads the file first.")
    return (f"No model on {pname} has a Cost yet, so none can be picked for {who}. "
            f"Give one a Cost on the Admin page, or pick a model in the step's panel.")

# The models a person could choose for a step when nothing can be picked: the default provider's first, then the other active ones
def model_options(kinds: list | None = None) -> list[dict]:
    out = [m for m in node_models()
           if m.get("ready") and _reads_all(next((p for p in _registry()
                                                  if p.get("id") == m["provider_id"]), {}),
                                            m["name"], list(kinds or []))]
    for m in out:
        m["label"] = f"{m['name']} ({m['provider']})" if m.get("ambiguous") else m["name"]
    return out

# The models a workflow step may choose from, the default provider's first and the cheapest ranked before the rest; only configured models appear
def node_models() -> list[dict]:
    out = []
    default = str(settings.get().get("default_provider") or "")
    for p in _registry():
        if not _node_capable(p):
            continue
        for m in p.get("models", []):
            if m.get("name"):
                out.append({"name": m["name"], "cost": m.get("cost"),
                            "quality": m.get("quality"),
                            "provider_id": p.get("id") or "",
                            "provider": p.get("name") or p.get("id") or "",
                            "no_temperature": bool(m.get("no_temperature")),
                            "default": p.get("id") == default,
                            "ready": is_ready({"model": m["name"], "provider_id": p.get("id") or ""})})

    counts: dict[str, int] = {}
    for m in out:
        counts[m["name"]] = counts.get(m["name"], 0) + 1
    for m in out:
        m["ambiguous"] = counts[m["name"]] > 1

    def _q(m):
        try:
            return -float(m.get("quality")) if m.get("quality") not in (None, "") else 0.0
        except (TypeError, ValueError):
            return 0.0
    return sorted(out, key=lambda m: (not m["default"], m["provider_id"],
                                      _ranked(m) is None, _ranked(m) or 0.0, _q(m), m["name"]))

# Resolves a provider given by name or id to its id; blank when no workflow provider matches
def provider_id_for(name_or_id: str) -> str:
    want = str(name_or_id or "").strip()
    if not want:
        return ""
    pool = [p for p in _registry() if _node_capable(p)]
    for p in pool:
        if p.get("id") == want:
            return p["id"]
    for p in pool:
        if str(p.get("name") or "").strip().lower() == want.lower():
            return p.get("id") or ""
    return ""

# Turns a step's model name into endpoint, adapter and key name through its provider
def resolve(model_ref: dict) -> dict:
    p, m = find_model(model_ref.get("model", ""), for_node=True,
                      provider_id=model_ref.get("provider_id", ""))
    return {"endpoint": m.get("endpoint") or p.get("endpoint", ""),
            "model": model_ref.get("model", ""),
            "adapter": p.get("adapter", "anthropic"),

            "route": model_catalog.facts(p, model_ref.get("model", ""))["adapter"],
            "key_name": p.get("key_name", ""),
            "auth": auth_type(p),

            "temperature": None if m.get("no_temperature")
            else clamp_temperature(model_ref.get("temperature"))}

def key_for(profile: dict) -> str | None:
    return secrets_store.get_secret(profile.get("key_name", ""), secrets_store.OWNER_APP)

# Says whether a model can run, and if not why: ready, no key, provider switched off, or not set up
def node_model_state(model: str, provider_id: str = "") -> tuple[str, dict]:
    p, m = find_model(model, for_node=True, provider_id=provider_id)
    if m:
        if "local" in (p.get("tags") or []):
            return "ready", p
        key_name = p.get("key_name", "")
        if key_name and secrets_store.has_secret(key_name, secrets_store.OWNER_APP):
            return "ready", p
        return "no-key", p
    for q in _registry():
        if q.get("use") == "builder" or auth_type(q) in BUILDER_ONLY_AUTHS:
            continue
        if any(r.get("name") == model for r in q.get("models", [])):
            return "disabled", q
    return "missing", {}

# Registered under a node-capable provider with its key set; local providers need none
def is_ready(model_ref: dict) -> bool:
    return node_model_state(model_ref.get("model", ""),
                            model_ref.get("provider_id", ""))[0] == "ready"

# True when at least one model is fully usable - listed under a workflow provider and with its key set
def any_node_model_ready() -> bool:
    return any(is_ready({"model": m["name"]}) for m in node_models())

MODEL_LIST_TIMEOUT = 15

# Fetches the provider's own live model list for the refresh picker
def list_models(provider: dict) -> dict:
    api_key = secrets_store.get_secret(provider.get("key_name", ""),
                                       secrets_store.OWNER_APP) or ""
    if not api_key and "local" not in (provider.get("tags") or []):
        return {"ok": False, "error_kind": "auth",
                "message": "save an API key first - the model list comes from the provider"}
    ep = (provider.get("endpoint") or "").rstrip("/")
    if provider.get("adapter", "anthropic") == "gemini":
        if "aiplatform.googleapis.com" in ep:
            return {"ok": False, "error_kind": "unsupported",
                    "message": "Google AI Platform has no model list for a key. Add the models by name with \"+ Add model\" (gemini-3.5-flash, gemini-3.1-pro-preview, ...)."}
        url = f"{ep or 'https://generativelanguage.googleapis.com/v1beta'}/models?pageSize=200"
        headers = {"x-goog-api-key": api_key}
    elif provider.get("adapter", "anthropic") == "openai":
        url = f"{ep or 'https://api.openai.com/v1'}/models"
        headers = {"authorization": f"Bearer {api_key}"}
    else:
        url = f"{ep or 'https://api.anthropic.com/v1'}/models"
        headers = {"x-api-key": api_key,
                   "anthropic-version": ANTHROPIC_VERSION}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=MODEL_LIST_TIMEOUT,
                                    context=_SSL_CTX) as resp:
            data = json.loads(resp.read())

        rows = data.get("data") or data.get("models") or []
        ids = sorted({str(m.get("id") or m.get("name") or "").removeprefix("models/")
                      for m in rows if (m.get("id") or m.get("name"))})
        return {"ok": True, "models": [i for i in ids if i]}
    except Exception as e:
        return {"ok": False, "error_kind": _err_kind(e), "message": _err_text(e)}

TEST_CALL_TIMEOUT = 30
TEST_CALL_MAX_TOKENS = 32
TEST_CALL_SCHEMA = {"type": "object",
                    "properties": {"response": {"type": "boolean"}},
                    "required": ["response"], "additionalProperties": False}

# The provider page's Test: one forced-shape call on the cheapest listed model, judged by its answer
def test_call(provider: dict) -> dict:
    rows = sorted([m for m in provider.get("models") or [] if m.get("name")],
                  key=lambda m: (m.get("cost") or 99))
    if not rows:
        return {"ok": False, "model": "", "message": "add a model to this provider first"}
    model = rows[0]["name"]
    state, _p = node_model_state(model, provider.get("id") or "")
    if state == "no-key":
        return {"ok": False, "model": model, "message": "save an API key first"}
    if state == "disabled":
        return {"ok": False, "model": model, "message": "switch the provider on first"}
    r = call({"model": model, "provider_id": provider.get("id") or ""},
             "Answer with response set to true.", {},
             output_schema=TEST_CALL_SCHEMA, max_tokens=TEST_CALL_MAX_TOKENS,
             timeout=TEST_CALL_TIMEOUT)
    if r.get("error_kind") or "structured" not in r:
        return {"ok": False, "model": model,
                "message": str(r.get("text") or "no answer came back")}
    got = (r.get("structured") or {}).get("response")
    if got is True:
        return {"ok": True, "model": model, "message": ""}
    return {"ok": False, "model": model,
            "message": f"the model answered {got!r} where true was asked for, "
                       "so it is not holding the answer shape"}

KEY_CHECK_TIMEOUT = 15

KEY_CHECK_MAX_TOKENS = 16

# Checks a key with one minimal live call; only a 401 or 403 counts as a bad key
def check_key(adapter: str, endpoint: str, model: str, api_key: str) -> dict:
    if not (api_key or "").strip():
        return {"ok": False, "error_kind": "auth", "message": "no key was provided"}
    if adapter == "openai":
        base = (endpoint or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        payload: dict = {"model": model,
                         "messages": [{"role": "user", "content": "ping"}],
                         "max_completion_tokens": KEY_CHECK_MAX_TOKENS}
        headers = {"content-type": "application/json",
                   "authorization": f"Bearer {api_key}"}
    elif adapter == "gemini":
        from modelcall.adapters import _gemini_url
        url = _gemini_url(model, endpoint)
        payload = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                   "generationConfig": {"maxOutputTokens": KEY_CHECK_MAX_TOKENS}}
        headers = {"content-type": "application/json", "x-goog-api-key": api_key}
    else:
        url = _anthropic_url(endpoint)
        payload = {"model": model, "max_tokens": KEY_CHECK_MAX_TOKENS,
                   "messages": [{"role": "user", "content": "ping"}]}
        headers = {"content-type": "application/json", "x-api-key": api_key,
                   "anthropic-version": ANTHROPIC_VERSION}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=KEY_CHECK_TIMEOUT,
                                    context=_SSL_CTX):
            pass
        return {"ok": True}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "error_kind": "auth",
                    "message": "the service refused this key"}
        return {"ok": True,
                "note": f"the key was accepted (the service answered {e.code})"}
    except Exception as e:
        return {"ok": False, "error_kind": _err_kind(e), "message": _err_text(e)}

# One AI call for a workflow step; with a schema, the output shape is enforced at the API layer
def call(model_ref: dict, prompt: str, inputs: dict,
         output_schema: dict | None = None,
         attachments: list | None = None,
         max_tokens: int | None = None,
         timeout: int | None = None) -> dict:
    _p, _m = find_model(model_ref.get("model", ""), for_node=True,
                        provider_id=model_ref.get("provider_id", ""))
    if not _m:
        owner, _row = find_model(model_ref.get("model", ""))
        if auth_type(owner) in BUILDER_ONLY_AUTHS:
            return {"text": "(auth error) this model's provider only powers the builder chat (a subscription or Codex account) - AI steps inside a workflow need a workflow provider with an API key. Pick a model from an API-key provider for this step, or add one in Admin.",
                    "error_kind": "auth"}
        if owner and not is_enabled(owner):
            return {"text": "(auth error) this model's provider is turned off - switch it back on in Admin, or pick another model.",
                    "error_kind": "auth"}
        if owner.get("use") == "builder":
            return {"text": "(auth error) this model is only set up for the builder - AI steps inside a workflow run on the workflow providers. Add it under one in Admin.",
                    "error_kind": "auth"}
        return {"text": "(auth error) this model isn't set up under any workflow provider - add it in Admin.",
                "error_kind": "auth"}
    profile = resolve(model_ref)
    api_key = key_for(profile) or ""
    model = profile.get("model", "")
    user = f"{prompt}\n\nINPUT (JSON):\n{json.dumps(inputs, default=str)}"
    if not output_schema:
        user += "\n\nReturn ONLY a single JSON object, no prose."

    return adapters.call(profile.get("route", "anthropic"), model, api_key, user,
                         attachments=attachments, schema=output_schema,
                         endpoint=profile.get("endpoint", ""), max_tokens=max_tokens,
                         temperature=profile.get("temperature"), timeout=timeout)

# What a step's model can be sent: the catalogue's facts for the model under its provider, with the provider's name
def model_facts(model_ref: dict) -> dict:
    p, _m = find_model(model_ref.get("model", ""), for_node=True,
                       provider_id=model_ref.get("provider_id", ""))
    f = model_catalog.facts(p, model_ref.get("model", ""))
    f["provider"] = p.get("name") or p.get("id") or "its provider"
    f["model"] = model_ref.get("model", "")
    return f
