# Imports a browser recording as a sample; cookies and auth headers are scrubbed before anything is stored
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from runtime import scrub
from storage import blobstore
from storage import store

# The hostnames a recording actually touched - candidates for the step's allowed domains
def domains_of(rec: dict) -> list[str]:
    hosts = set()
    for c in rec.get("calls") or []:
        h = urlsplit(str(c.get("url") or "")).hostname
        if h:
            hosts.add(h)
    return sorted(hosts)

# Uploads that look like browser recordings route through the scrub instead of being stored raw
def looks_like_recording(text: str) -> bool:
    t = (text or "").lstrip()
    if not t.startswith("{"):
        return False
    try:
        rec = json.loads(t)
    except Exception:
        return False
    if not isinstance(rec, dict):
        return False
    log = rec.get("log")
    if isinstance(log, dict) and isinstance(log.get("entries"), list):
        return True
    return bool(isinstance(rec.get("calls"), list) and rec.get("calls"))

# Normalises a real HAR into the internal shape so one scrub engine serves every capture route
def _from_har(rec: dict) -> Optional[dict]:
    entries = ((rec.get("log") or {}).get("entries")
               if isinstance(rec.get("log"), dict) else None)
    if not isinstance(entries, list):
        return None
    calls = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        req = e.get("request") or {}
        resp = e.get("response") or {}
        call: dict = {"url": req.get("url") or "",
                      "method": req.get("method") or "GET",
                      "ts": e.get("startedDateTime") or ""}
        r: dict = {"headers": req.get("headers") or []}
        post = req.get("postData") or {}
        if post.get("text"):
            r["body"] = post["text"]
        call["request"] = r
        content = resp.get("content") or {}
        s: dict = {"status": resp.get("status"),
                   "headers": resp.get("headers") or []}
        if content.get("text"):
            s["body"] = content["text"]
        call["response"] = s
        calls.append(call)
    return {"version": "har", "calls": calls, "actions": []}

# Any uploaded recording goes through the scrub on import, whatever it claims to be
def import_recording(workflow: dict, text: str, name: Optional[str] = None) -> dict:
    try:
        rec = json.loads(text or "")
    except Exception:
        return {"error": "that doesn't look like a recording - it should be the captured JSON (a HAR, or a capture file), unchanged"}
    if isinstance(rec, dict):
        rec = _from_har(rec) or rec
    if not isinstance(rec, dict) or not (rec.get("calls") or rec.get("actions")):
        return {"error": "the recording has no captured calls or actions - start recording BEFORE performing the task, then stop"}
    clean = scrub.recording(rec)
    data = json.dumps(clean, indent=1).encode()
    ref = blobstore.put(data, "application/json", {"name": name or "recording"},
                        owner=blobstore.workflow_owner(workflow["id"]))
    entry_name = name or f"recording-{time.strftime('%Y%m%d-%H%M%S')}.json"
    samples = [s for s in workflow.get("samples", []) if s.get("name") != entry_name]
    samples.append({"name": entry_name, "ref": ref, "mime": "application/json",
                    "size": len(data), "kind": "recording",
                    "domains": domains_of(clean)})
    workflow["samples"] = samples
    store.save(workflow)
    return {"ok": True, "name": entry_name,
            "calls": len(clean.get("calls") or []),
            "actions": len(clean.get("actions") or []),
            "domains": domains_of(clean)}
