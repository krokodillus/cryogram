# Exports a workflow as one portable file: the workflow as built, every value blanked, secrets as names only
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Optional

import config
from storage import environments

FORMAT = "cryogram-workflow/1"

IMPORT_PREFIX = "Built by Cryogram. "

STRIP = ("transcript", "transcript_seq", "chat", "samples", "sessions",
         "tickets", "intent",
         "plan", "published", "setup_block", "last_run_ts")

_SHARE_STRIP = ("learning_ids", "config_hash", "config_ts", "group", "origin",
                "environment_ids", "env_bindings",

                "decisions", "attention_seen_ts", "master_model", "environment_id",
                "loaded_skills", "plan_approved_ts", "setup_block", "last_run_ts",

                "approval_grants")
_NODE_STRIP = ("stats", "lineage", "tests",

               "approval_suppressed")

# The workflow as a run needs it: steps, wiring and settings, without chat, samples or history
def runtime_workflow(workflow: dict) -> dict:
    doc = json.loads(json.dumps({k: v for k, v in workflow.items()
                                 if not k.startswith("_")}))
    for k in STRIP:
        doc.pop(k, None)
    have = {v.get("name") for v in doc.get("variables", [])}
    for name, r in environments.resolve(workflow).items():
        if name in have or r.get("source") in (None, "workflow"):
            continue
        doc.setdefault("variables", []).append(
            {"name": name, "secret": bool(r.get("secret")),
             "value": None if r.get("secret") else r.get("value"),
             "persistent": True, "kind": "config"})
    doc["environment_ids"], doc["env_bindings"] = [], {}
    return doc

def _scrub_workflow(doc: dict) -> dict:
    for k in _SHARE_STRIP:
        doc.pop(k, None)
    for v in doc.get("variables") or []:
        v["value"] = None
    for n in doc.get("nodes") or []:
        for k in _NODE_STRIP:
            n.pop(k, None)
        n["tests"] = []
    return doc

# The shareable file: every value blanked, secrets reduced to names, recorded examples left out
def export_doc(workflow: dict) -> dict:
    doc = _scrub_workflow(runtime_workflow(workflow))

    if workflow.get("intent"):
        doc["intent"] = json.loads(json.dumps(workflow["intent"]))
    packages = sorted({d for n in workflow.get("nodes", [])
                       for d in (n.get("config", {}).get("dependencies") or [])})
    return {"format": FORMAT,
            "app_version": config.VERSION,
            "exported": time.time(),
            "origin": {"id": workflow.get("id"),
                       "name": workflow.get("name") or "",
                       "hash": hashlib.sha256(json.dumps(doc, sort_keys=True,
                                                         default=str).encode()).hexdigest()},
            "workflow": doc,
            "packages": packages}

def _check_payload(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return "That file isn't a Cryogram workflow."
    if payload.get("format") != FORMAT:
        return ("That file comes from a version of Cryogram this app can't read.")
    wf = payload.get("workflow")
    if not isinstance(wf, dict) or not isinstance(wf.get("nodes"), list):
        return "That file isn't a Cryogram workflow."
    for n in wf["nodes"]:
        if not isinstance(n, dict) or not n.get("id") or not n.get("type"):
            return "That file's steps couldn't be read."
    return None

# Imports a shared file as a fresh workflow, scrubbing it again rather than trusting its claim of cleanness
def import_doc(payload: Any, name: str = "") -> dict:
    from storage import secrets_store
    from storage import store
    err = _check_payload(payload)
    if err:
        return {"error": err}
    doc = _scrub_workflow(json.loads(json.dumps(payload["workflow"])))
    doc["id"] = f"wfl_{uuid.uuid4().hex[:8]}"
    doc["name"] = (name or "").strip() or str(doc.get("name") or "").strip() \
        or str((payload.get("origin") or {}).get("name") or "").strip() \
        or "Shared workflow"
    desc = str(doc.get("description") or "").strip()
    if not desc.startswith(IMPORT_PREFIX.strip()):
        desc = IMPORT_PREFIX + desc if desc else IMPORT_PREFIX.strip()
    doc["description"] = desc
    doc["transcript"], doc["transcript_seq"] = [], 0
    doc["samples"], doc["tickets"] = [], []
    doc["plan"] = None
    doc["environment_ids"], doc["env_bindings"] = [], {}
    intent = payload["workflow"].get("intent")
    doc["intent"] = intent if isinstance(intent, dict) else None

    for v in doc.get("variables") or []:
        if v.get("secret"):
            v["value"] = None
    origin = payload.get("origin") if isinstance(payload.get("origin"), dict) else {}
    doc["origin"] = {"id": origin.get("id"), "name": origin.get("name"),
                     "hash": origin.get("hash"), "imported": time.time()}
    store.save(doc)
    return {"ok": True, "workflow": doc}
