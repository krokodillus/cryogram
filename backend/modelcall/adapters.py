# The request shapes: Anthropic's messages API, OpenAI's own API, Gemini's generateContent, and the OpenAI-compatible shape
from __future__ import annotations

import base64
import io
import json
import ssl
import threading
import urllib.error
import urllib.request
from typing import Any

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_BASE = "https://api.openai.com/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

DEFAULT_MAX_TOKENS = 32_768
DEFAULT_CALL_TIMEOUT = 300

try:
    import certifi
    _SSL_CTX: ssl.SSLContext | None = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = None

MODEL_ERROR_BODY_CHARS = 400

_TRUNCATED = {"max_tokens", "length", "MAX_TOKENS"}

def _http_error_text(e) -> str:
    return f"(model error {e.code}) {e.read().decode(errors='replace')[:MODEL_ERROR_BODY_CHARS]}"

# Classifies a failed model call so routing decisions never read message text
def _err_kind(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return "auth" if e.code in (401, 403) else f"http-{e.code}"
    reason = getattr(e, "reason", None)
    if isinstance(e, TimeoutError) or isinstance(reason, TimeoutError):
        return "timeout"
    return "transport"

def _err_text(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return _http_error_text(e)
    if _err_kind(e) == "timeout":
        return "(model error) timed out waiting for the model's answer"
    return f"(request failed) {e}"

# A declared answer size is sent as it is; the provider's own ceiling is the only one
def clamp_tokens(value: Any = None) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS
    return n if n > 0 else DEFAULT_MAX_TOKENS

# Temperature defaults to 0 so a step answers the same way every run, unless the plan asked for variety
def clamp_temperature(value: Any = None) -> float:
    try:
        t = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(t, 0.0), 1.0)

def _with_temperature(payload: dict, temperature) -> dict:
    t = clamp_temperature(temperature)
    if t is not None:
        payload["temperature"] = t
    return payload

# Bounds how long an adapter waits on the model; a step's declared timeout can extend the default, never shorten it
def clamp_call_timeout(value=None) -> int:
    try:
        declared = int(float(value))
    except (TypeError, ValueError):
        declared = 0
    return max(DEFAULT_CALL_TIMEOUT, declared)

# Token counts are recorded beside the result, never inside a step's output
_USAGE = threading.local()

def _note_usage(data: dict, shape: str = "anthropic") -> None:
    try:
        u = data.get("usage") or data.get("usageMetadata") or {}
        if shape == "openai":
            rec = {"in": int(u.get("prompt_tokens") or 0),
                   "out": int(u.get("completion_tokens") or 0)}
        elif shape == "gemini":
            rec = {"in": int(u.get("promptTokenCount") or 0),
                   "out": int(u.get("candidatesTokenCount") or 0)}
        else:
            rec = {"in": int(u.get("input_tokens") or 0),
                   "out": int(u.get("output_tokens") or 0)}
        _USAGE.last = rec if (rec["in"] or rec["out"]) else None
    except Exception:
        _USAGE.last = None

# Reading the counts clears them, so a failed later call cannot reuse stale numbers
def take_usage() -> dict | None:
    u = getattr(_USAGE, "last", None)
    _USAGE.last = None
    return u

def _post(url: str, payload: dict, headers: dict, timeout: int | None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"content-type": "application/json", **headers},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=clamp_call_timeout(timeout),
                                context=_SSL_CTX) as resp:
        return json.loads(resp.read())

# Anthropic: a PDF is a document block, an image an image block
def attachment_blocks(attachments: list) -> list:
    blocks = []
    for a in attachments or []:
        b64 = base64.b64encode(a["data"]).decode()
        kind = "document" if a["mime"] == "application/pdf" else "image"
        blocks.append({"type": kind, "source": {"type": "base64",
                                                "media_type": a["mime"], "data": b64}})
    return blocks

# OpenAI's own API: a PDF is a file part, an image an image_url data URI; the compatible shape takes images only
def openai_parts(attachments: list, files_ok: bool = True) -> list:
    parts = []
    for a in attachments or []:
        b64 = base64.b64encode(a["data"]).decode()
        uri = f"data:{a['mime']};base64,{b64}"
        if a["mime"] == "application/pdf":
            if files_ok:
                parts.append({"type": "file", "file": {
                    "filename": str(a.get("name") or "document") + ".pdf",
                    "file_data": uri}})
            continue
        parts.append({"type": "image_url", "image_url": {"url": uri}})
    return parts

# Gemini: every file is inline data with its mime type
def gemini_parts(attachments: list) -> list:
    return [{"inline_data": {"mime_type": a["mime"],
                             "data": base64.b64encode(a["data"]).decode()}}
            for a in attachments or []]

# An endpoint override routes this adapter too; the default is the standard API
def _anthropic_url(endpoint: str = "") -> str:
    return f"{endpoint.rstrip('/')}/messages" if endpoint else ANTHROPIC_URL

def _anthropic_headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}

OPTIONAL_SETTINGS: dict[str, dict[str, tuple]] = {
    "anthropic": {"temperature": ("temperature",)},
    "openai": {"temperature": ("temperature",)},
    "gemini": {"temperature": ("generationConfig", "temperature")},
}

_REFUSED: dict[str, set] = {}

remember_refusal = None

# Remembers a model that refused a setting, in memory and through the installed hook
def _learned_refusal(model: str, setting: str) -> None:
    _REFUSED.setdefault(model, set()).add(setting)
    if remember_refusal is not None:
        try:
            remember_refusal(model, setting)
        except Exception:
            pass

def _has(payload: dict, path: tuple) -> bool:
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True

def _without(payload: dict, path: tuple) -> dict:
    if not _has(payload, path):
        return payload
    if len(path) == 1:
        return {k: v for k, v in payload.items() if k != path[0]}
    return {**payload, path[0]: _without(payload[path[0]], path[1:])}

# One request that leaves out what the model refused before, and on a 400 naming an optional setting sends once more without it
def _send_optional(route: str, model: str, payload: dict, send) -> dict:
    settings = OPTIONAL_SETTINGS.get(route, {})
    for name in _REFUSED.get(model, ()):
        if name in settings:
            payload = _without(payload, settings[name])
    tried: set = set()
    while True:
        try:
            return send(payload)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            hit = next((n for n, path in settings.items()
                        if e.code == 400 and n not in tried and n in body and _has(payload, path)), None)
            if not hit:
                raise urllib.error.HTTPError(e.url, e.code, e.reason, e.headers,
                                             io.BytesIO(body.encode()))
            tried.add(hit)
            _learned_refusal(model, hit)
            payload = _without(payload, settings[hit])

# One Anthropic POST through the optional-setting rule
def _anthropic_post(url: str, payload: dict, headers: dict, timeout: int | None) -> dict:
    return _send_optional("anthropic", str(payload.get("model") or ""), payload,
                          lambda p: _post(url, p, headers, timeout))

# The plain-text Anthropic call
def anthropic_messages(model: str, messages: list[dict], api_key: str,
                       system: str | None = None,
                       max_tokens: int | None = None,
                       temperature: float | None = None,
                       endpoint: str = "",
                       timeout: int | None = None) -> str:
    payload: dict = _with_temperature({"model": model, "max_tokens": clamp_tokens(max_tokens),
                                       "messages": messages}, temperature)
    if system:
        payload["system"] = system
    try:
        data = _anthropic_post(_anthropic_url(endpoint), payload, _anthropic_headers(api_key), timeout)
        _note_usage(data)
        if str(data.get("stop_reason") or "") in _TRUNCATED:
            return ("(the answer was cut off before it finished - this step needs more room than it was given)")
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text").strip()
        return text or "(empty response)"
    except urllib.error.HTTPError as e:
        return _http_error_text(e)
    except Exception as e:
        return f"(request failed) {e}"

_ANTHROPIC_UNSUPPORTED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                          "multipleOf", "minLength", "maxLength", "maxItems", "pattern"}

_ANTHROPIC_FORMATS = {"date-time", "time", "date", "duration", "email", "hostname",
                      "uri", "ipv4", "ipv6", "uuid"}

ANTHROPIC_UNION_LIMIT = 16

# The declared schema in the form Anthropic's JSON output takes: our markers and unsupported limits left out
def _anthropic_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_anthropic_schema(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if str(k).startswith("x-") or k in _ANTHROPIC_UNSUPPORTED:
            continue
        if k == "format" and v not in _ANTHROPIC_FORMATS:
            continue
        if k == "minItems" and v not in (0, 1):
            continue
        out[k] = v if k in ("enum", "const", "required") else _anthropic_schema(v)
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
    return out

def _union_count(schema: Any) -> int:
    if isinstance(schema, list):
        return sum(_union_count(v) for v in schema)
    if not isinstance(schema, dict):
        return 0
    here = 1 if isinstance(schema.get("type"), list) or "anyOf" in schema else 0
    return here + sum(_union_count(v) for v in schema.values() if isinstance(v, (dict, list)))

# Every top-level answer field may also be null, so a value the model cannot find comes back empty instead of invented
def anthropic_answer_schema(schema: dict) -> dict:
    out = _anthropic_schema(schema)
    props = out.get("properties")
    if not isinstance(props, dict):
        return out
    adding = [k for k, v in props.items()
              if isinstance(v, dict) and isinstance(v.get("type"), str)]
    if _union_count(out) + len(adding) > ANTHROPIC_UNION_LIMIT:
        return out
    for k in adding:
        frag = dict(props[k])
        frag["type"] = [frag["type"], "null"]
        if "enum" in frag:
            frag["enum"] = list(frag["enum"]) + [None]
        props[k] = frag
    return out

# The structured Anthropic call: Anthropic's JSON output holds the answer to the declared schema
def anthropic_structured(model: str, messages: list[dict], api_key: str,
                         schema: dict, max_tokens: int | None = None,
                         temperature: float | None = None,
                         endpoint: str = "",
                         timeout: int | None = None) -> dict:
    payload = _with_temperature({"model": model, "max_tokens": clamp_tokens(max_tokens),
               "messages": messages,
               "output_config": {"format": {"type": "json_schema",
                                            "schema": anthropic_answer_schema(schema)}}},
                                temperature)
    try:
        data = _anthropic_post(_anthropic_url(endpoint), payload, _anthropic_headers(api_key), timeout)
        _note_usage(data)
        stop = str(data.get("stop_reason") or "")
        if stop in _TRUNCATED:
            return {"text": "the answer was cut off before it finished - this step needs more room than it was given",
                    "error_kind": "max-tokens"}
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text").strip()
        if stop == "refusal":
            return {"text": "the model declined to answer: " + (text or "no reason given")}
        try:
            got = json.loads(text)
        except ValueError:
            return {"text": text or "(no structured output in response)"}
        return {"structured": got} if isinstance(got, dict) else {"text": text}
    except Exception as e:
        return {"text": _err_text(e), "error_kind": _err_kind(e)}

# The shared HTTP POST for OpenAI-shaped endpoints
def _openai_post(base: str, payload: dict, api_key: str,
                 timeout: int | None = None) -> dict:
    def send(p: dict) -> dict:
        data = _post(f"{base}/chat/completions", p,
                     {"authorization": f"Bearer {api_key}"}, timeout)
        _note_usage(data, "openai")
        return data
    def send_either_key(p: dict) -> dict:
        try:
            return send(p)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 400 and "max_completion_tokens" in body \
                    and "max_completion_tokens" in p:
                legacy = {**p, "max_tokens": p["max_completion_tokens"]}
                legacy.pop("max_completion_tokens", None)
                return send(legacy)
            raise urllib.error.HTTPError(e.url, e.code, e.reason, e.headers,
                                         io.BytesIO(body.encode()))

    return _send_optional("openai", str(payload.get("model") or ""), payload, send_either_key)

def _openai_finished_early(data: dict) -> bool:
    return str((data.get("choices") or [{}])[0].get("finish_reason") or "") in _TRUNCATED

# The structured OpenAI-shaped call: a strict response schema makes the answer match the declared shape
def openai_structured(model: str, messages: list[dict], api_key: str, schema: dict,
                      endpoint: str = "", max_tokens: int | None = None,
                      temperature: float | None = None,
                      timeout: int | None = None) -> dict:
    base = (endpoint or OPENAI_BASE).rstrip("/")
    payload = _with_temperature({"model": model, "messages": messages,
               "max_completion_tokens": clamp_tokens(max_tokens),
               "response_format": {"type": "json_schema",
                                   "json_schema": {"name": "node_output",
                                                   "schema": schema, "strict": True}}}, temperature)
    try:
        data = _openai_post(base, payload, api_key, timeout=timeout)
        if _openai_finished_early(data):
            return {"text": "the answer was cut off before it finished - this step needs more room than it was given",
                    "error_kind": "max-tokens"}
        content = data["choices"][0]["message"]["content"] or ""
        try:
            return {"structured": json.loads(content)}
        except Exception:
            return {"text": content}
    except Exception as e:
        return {"text": _err_text(e), "error_kind": _err_kind(e)}

# The plain-text OpenAI-shaped call
def openai_chat(model: str, messages: list[dict], api_key: str, endpoint: str = "",
                max_tokens: int | None = None,
                temperature: float | None = None,
                timeout: int | None = None) -> str:
    base = (endpoint or OPENAI_BASE).rstrip("/")
    payload = _with_temperature({"model": model, "messages": messages,
               "max_completion_tokens": clamp_tokens(max_tokens)}, temperature)
    try:
        data = _openai_post(base, payload, api_key, timeout=timeout)
        if _openai_finished_early(data):
            return ("(the answer was cut off before it finished - this step needs more room than it was given)")
        return (data["choices"][0]["message"]["content"] or "(empty response)").strip()
    except urllib.error.HTTPError as e:
        return _http_error_text(e)
    except Exception as e:
        return f"(request failed) {e}"

def _gemini_url(model: str, endpoint: str = "") -> str:
    base = (endpoint or "").rstrip("/")
    if not base or base.endswith("/openai"):
        base = GEMINI_BASE
    return f"{base}/models/{model}:generateContent"

def _gemini_schema(schema: dict) -> Any:
    if isinstance(schema, dict):
        return {k: _gemini_schema(v) for k, v in schema.items() if not str(k).startswith("x-")}
    if isinstance(schema, list):
        return [_gemini_schema(v) for v in schema]
    return schema

def _gemini_call(model: str, parts: list, api_key: str, schema: dict | None,
                 endpoint: str, max_tokens, temperature, timeout) -> dict:
    gen: dict = _with_temperature({"maxOutputTokens": clamp_tokens(max_tokens)}, temperature)
    if schema is not None:
        gen["responseMimeType"] = "application/json"
        gen["responseJsonSchema"] = _gemini_schema(schema)
    payload = {"contents": [{"role": "user", "parts": parts}],
               "generationConfig": gen}

    data = _send_optional("gemini", model, payload,
                          lambda p: _post(_gemini_url(model, endpoint), p,
                                          {"x-goog-api-key": api_key}, timeout))
    _note_usage(data, "gemini")
    return data

def _gemini_text(data: dict) -> tuple[str, bool]:
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
    return text.strip(), str(cand.get("finishReason") or "") in _TRUNCATED

# The structured Gemini call: a response schema makes the answer match the declared shape
def gemini_structured(model: str, parts: list, api_key: str, schema: dict,
                      endpoint: str = "", max_tokens: int | None = None,
                      temperature: float | None = None,
                      timeout: int | None = None) -> dict:
    try:
        data = _gemini_call(model, parts, api_key, schema, endpoint, max_tokens,
                            temperature, timeout)
        text, cut = _gemini_text(data)
        if cut:
            return {"text": "the answer was cut off before it finished - this step needs more room than it was given",
                    "error_kind": "max-tokens"}
        try:
            return {"structured": json.loads(text)}
        except Exception:
            return {"text": text or "(no structured output in response)"}
    except Exception as e:
        return {"text": _err_text(e), "error_kind": _err_kind(e)}

# The plain-text Gemini call
def gemini_chat(model: str, parts: list, api_key: str, endpoint: str = "",
                max_tokens: int | None = None,
                temperature: float | None = None,
                timeout: int | None = None) -> str:
    try:
        data = _gemini_call(model, parts, api_key, None, endpoint, max_tokens,
                            temperature, timeout)
        text, cut = _gemini_text(data)
        if cut:
            return ("(the answer was cut off before it finished - this step needs more room than it was given)")
        return text or "(empty response)"
    except urllib.error.HTTPError as e:
        return _http_error_text(e)
    except Exception as e:
        return f"(request failed) {e}"

# One call to any vendor: the prompt, the files in that vendor's form, and the answer as an object when a schema is given
def call(adapter: str, model: str, api_key: str, user_text: str,
         attachments: list | None = None, schema: dict | None = None,
         endpoint: str = "", max_tokens: int | None = None,
         temperature: float | None = None, timeout: int | None = None) -> dict:
    attachments = attachments or []
    take_usage()
    if adapter == "gemini":
        parts = gemini_parts(attachments) + [{"text": user_text}]
        res = (gemini_structured(model, parts, api_key, schema, endpoint, max_tokens,
                                 temperature=temperature, timeout=timeout)
               if schema else {"text": gemini_chat(model, parts, api_key, endpoint,
                                                   max_tokens, temperature=temperature,
                                                   timeout=timeout)})
    elif adapter in ("openai", "compatible"):
        parts = openai_parts(attachments, files_ok=(adapter == "openai"))
        content: Any = parts + [{"type": "text", "text": user_text}] if parts else user_text
        messages = [{"role": "user", "content": content}]
        res = (openai_structured(model, messages, api_key, schema, endpoint, max_tokens,
                                 temperature=temperature, timeout=timeout)
               if schema else {"text": openai_chat(model, messages, api_key, endpoint,
                                                   max_tokens, temperature=temperature,
                                                   timeout=timeout)})
    else:
        content = attachment_blocks(attachments) + [{"type": "text", "text": user_text}] \
            if attachments else user_text
        messages = [{"role": "user", "content": content}]
        res = (anthropic_structured(model, messages, api_key, schema, max_tokens,
                                    temperature=temperature, endpoint=endpoint,
                                    timeout=timeout)
               if schema else {"text": anthropic_messages(model, messages, api_key,
                                                          max_tokens=max_tokens,
                                                          temperature=temperature,
                                                          endpoint=endpoint,
                                                          timeout=timeout)})
    usage = take_usage()
    if usage:
        res["usage"] = usage
    return res
