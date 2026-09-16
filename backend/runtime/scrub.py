# Masks credentials out of captured web traffic before any of it is stored
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

AUTH_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie",
                "x-api-key", "api-key", "x-auth-token", "x-access-token",
                "x-session-id", "x-csrf-token", "x-xsrf-token"}

TOKEN_KEY = re.compile("(token|secret|passw|apikey|api_key|credential|auth|session|bearer|signature)", re.I)
BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")

def mask(value: Any) -> str:
    return f"<redacted, {len(str(value))} chars>"

def headers(hs) -> Any:
    if isinstance(hs, dict):
        return {k: (mask(v) if k.lower() in AUTH_HEADERS or TOKEN_KEY.search(k)
                    or BEARER.search(str(v)) else v)
                for k, v in hs.items()}
    if isinstance(hs, list):
        return [{**h, "value": (mask(h.get("value", ""))
                                if str(h.get("name", "")).lower() in AUTH_HEADERS
                                or TOKEN_KEY.search(str(h.get("name", "")))
                                or BEARER.search(str(h.get("value", "")))
                                else h.get("value"))} for h in hs]
    return hs

def url(u: str) -> str:
    parts = urlsplit(u)
    if not parts.query:
        return u
    q = [(k, mask(v) if TOKEN_KEY.search(k) else v)
         for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit(parts._replace(query=urlencode(q)))

# A value under a token-like key is masked whatever its shape - plain, list or nested
def mask_under_token_key(v: Any) -> Any:
    if isinstance(v, list):
        return [mask_under_token_key(x) for x in v]
    if isinstance(v, dict):
        return {k: mask_under_token_key(x) for k, x in v.items()}
    return mask(v)

def obj(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: (mask_under_token_key(v) if TOKEN_KEY.search(str(k))
                    else obj(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [obj(x) for x in o]
    if isinstance(o, str):
        return BEARER.sub(lambda m: mask(m.group(0)), o)
    return o

# JSON bodies are walked key by key; other text gets the bearer-pattern sweep
def body(b: Any) -> Any:
    if isinstance(b, (dict, list)):
        return obj(b)
    if isinstance(b, str):
        try:
            return json.dumps(obj(json.loads(b)))
        except Exception:
            return BEARER.sub(lambda m: mask(m.group(0)), b)
    return b

# A recording in the internal {calls} shape, masked before it is stored
def recording(rec: dict) -> dict:
    out = dict(rec)
    calls = []
    for c in rec.get("calls") or []:
        c = dict(c)
        if c.get("url"):
            c["url"] = url(str(c["url"]))
        for side in ("request", "response"):
            part = c.get(side)
            if isinstance(part, dict):
                part = dict(part)
                if "headers" in part:
                    part["headers"] = headers(part["headers"])
                if "body" in part:
                    part["body"] = body(part["body"])
                if part.get("url"):
                    part["url"] = url(str(part["url"]))
                c[side] = part
        calls.append(c)
    out["calls"] = calls
    out["actions"] = obj(rec.get("actions") or [])
    out["scrubbed"] = True
    return out

# A cookie is a credential by definition, so every value in a cookie list goes
def _cookies(cs) -> Any:
    if not isinstance(cs, list):
        return cs
    return [{**c, "value": mask(c.get("value", ""))} if isinstance(c, dict) else c
            for c in cs]

# A browser's own HAR, masked but still a HAR, so later steps can still read it
def har(doc: Any) -> Optional[dict]:
    if not isinstance(doc, dict):
        return None
    log = doc.get("log")
    if not isinstance(log, dict) or not isinstance(log.get("entries"), list):
        return None
    out = dict(doc)
    olog = dict(log)
    entries = []
    for e in log["entries"]:
        if not isinstance(e, dict):
            entries.append(e)
            continue
        e = dict(e)
        req = e.get("request")
        if isinstance(req, dict):
            req = dict(req)
            if req.get("url"):
                req["url"] = url(str(req["url"]))
            if "headers" in req:
                req["headers"] = headers(req["headers"])
            if "queryString" in req:
                req["queryString"] = headers(req["queryString"])
            if "cookies" in req:
                req["cookies"] = _cookies(req["cookies"])
            post = req.get("postData")
            if isinstance(post, dict) and post.get("text") is not None:
                post = dict(post)
                post["text"] = body(post["text"])
                req["postData"] = post
            e["request"] = req
        resp = e.get("response")
        if isinstance(resp, dict):
            resp = dict(resp)
            if "headers" in resp:
                resp["headers"] = headers(resp["headers"])
            if "cookies" in resp:
                resp["cookies"] = _cookies(resp["cookies"])
            if resp.get("redirectURL"):
                resp["redirectURL"] = url(str(resp["redirectURL"]))
            content = resp.get("content")
            if isinstance(content, dict) and content.get("text") is not None:
                content = dict(content)
                content["text"] = body(content["text"])
                resp["content"] = content
            e["response"] = resp
        entries.append(e)
    olog["entries"] = entries
    out["log"] = olog
    out["scrubbed"] = True
    return out

# The whole file at once: parse, mask, hand back bytes, or None when it cannot be read
def har_bytes(raw: bytes) -> Optional[bytes]:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    out = har(doc)
    if out is None:
        return None
    return json.dumps(out).encode("utf-8")
