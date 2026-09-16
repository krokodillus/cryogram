# Workflow persistence: each workflow is one folder (a bundle) that archives, restores and deletes as a unit
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from typing import Any, Optional

import config
from runtime import corpus
from storage import db

_SAFE_ID = re.compile(r"^[A-Za-z0-9][\w.-]*$")

def _safe_id(workflow_id: str) -> bool:
    return bool(_SAFE_ID.match(str(workflow_id or ""))) and ".." not in str(workflow_id)

def _ensure_dirs() -> None:
    config.WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)

# Anything from the assistant since the user last opened the workflow; opening it is the acknowledgement
def _needs_attention(data: dict) -> bool:
    seen = data.get("attention_seen_ts") or 0
    for item in data.get("transcript", []):
        if (item.get("at") or 0) <= seen:
            continue

        if item.get("kind") == "request":
            return True
        if item.get("kind") == "message" and item.get("from") == "assistant" \
                and str(item.get("text") or "").strip():
            return True
    return any(t.get("status") == "open" and (t.get("ts") or 0) > seen
               for t in data.get("tickets", []))

# A value typed into a run form fills the matching empty setting, so the next run does not ask again
def persist_entry_values(workflow: dict, entry: dict) -> bool:
    changed = False
    for v in workflow.get("variables", []) or []:
        val = (entry or {}).get(v.get("name"))
        if (v.get("persistent", True) is not False and not v.get("secret")
                and val not in (None, "")
                and str(v.get("value") or "").strip() == ""):
            v["value"] = val
            changed = True
    return changed

def _card(data: dict) -> dict:
    nodes = data.get("nodes", [])
    return {
        **{k: data.get(k) for k in ("id", "name", "description",
                                    "group", "environment_ids")},

        "last_run_ts": data.get("last_run_ts"),
        "attention_seen_ts": data.get("attention_seen_ts") or 0,
        "needs_attention": _needs_attention(data),
        "stats": {
            "nodes": len(nodes),
            "ai_nodes": sum(1 for n in nodes if n.get("type") == "ai"),
            "connectors": sum(1 for n in nodes if n.get("type") == "connector"),

            "runs": corpus.run_count(data.get("id") or ""),
        },
    }

# Reads the document and its last-write stamp, read-only - a writable open would touch every bundle folder on each listing
def _read_doc_ts(db_path) -> Optional[tuple[dict, float]]:
    if not db_path.exists():
        return None
    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro",
                                     uri=True, timeout=10)) as conn:
            row = conn.execute("SELECT data, ts FROM workflow LIMIT 1").fetchone()
    except sqlite3.Error:
        try:
            with closing(sqlite3.connect(str(db_path), timeout=10)) as conn:
                row = conn.execute("SELECT data, ts FROM workflow LIMIT 1").fetchone()
        except sqlite3.Error as e:
            raise Unreadable(str(e)) from None
    try:
        return (json.loads(row[0]), row[1] or 0.0) if row else None
    except (json.JSONDecodeError, TypeError) as e:
        raise Unreadable(str(e)) from None

class Unreadable(Exception):
    pass

# Reads a workflow without touching its bundle; anything that will write uses load
def load_ro(workflow_id: str) -> Optional[dict[str, Any]]:
    try:
        found = _read_doc_ts(config.workflow_db(workflow_id))
    except Unreadable:
        return None
    return found[0] if found else None

# The dashboard's listing: light metadata per workflow, read without loading whole bundles
def list_workflows() -> list[dict[str, Any]]:
    _ensure_dirs()
    out = []
    for path in sorted(config.WORKFLOWS_DIR.glob("*/workflow.db")):
        try:
            found = _read_doc_ts(path)
        except (Unreadable, sqlite3.Error, json.JSONDecodeError, OSError):
            out.append({"id": path.parent.name,
                        "name": f"{path.parent.name} (unreadable)",
                        "description": "this workflow's data could not be read",
                        "unreadable": True, "stats": {"nodes": 0, "ai_nodes": 0,
                                                      "connectors": 0, "runs": 0},
                        "updated_ts": 0.0})
            continue
        if found:
            data, ts = found
            card = _card(data)

            card["updated_ts"] = data.get("config_ts") or ts
            out.append(card)

    try:
        halted: dict = {}
        for r in db.runstate_all():
            if r.get("status") == "halted":
                pid_ = r.get("workflow_id")
                halted[pid_] = max(halted.get(pid_, 0),
                                   r.get("started") or 0)
        for c in out:
            if halted.get(c.get("id"), 0) > (c.get("attention_seen_ts") or 0):
                c["needs_attention"] = True
    except Exception:
        pass
    out.sort(key=lambda c: c["updated_ts"], reverse=True)
    return out

# Reads a workflow for writing; an unreadable bundle reads as absent, never an error mid-sweep
def load(workflow_id: str) -> Optional[dict[str, Any]]:
    try:
        return db.workflow_get(workflow_id)
    except (sqlite3.DatabaseError, json.JSONDecodeError, Unreadable):
        return None

# A readable marker in the bundle folder, written only when its content changed
def _write_about(workflow: dict[str, Any]) -> None:
    try:
        d = config.workflow_dir(workflow["id"])
        if not d.is_dir():
            return
        f = d / "about.txt"
        want = f"{workflow.get('name') or '(unnamed)'}\n{workflow['id']}\n"
        if f.exists() and f.read_text() == want:
            return
        f.write_text(want)
    except OSError:
        pass

_SUBSET_KEYS = ("name", "description", "group", "nodes", "edges", "variables",
                "environment_ids", "env_bindings",
                "egress_allowlist", "deliverables", "plan", "intent")

def _config_subset(doc: dict[str, Any]) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    for k in _SUBSET_KEYS:
        if k == "nodes":
            cfg["nodes"] = [{kk: v for kk, v in n.items() if kk != "stats"}
                            for n in doc.get("nodes", []) or []]
        elif k == "edges":
            cfg["edges"] = doc.get("edges") or []
        else:
            cfg[k] = doc.get(k)
    return cfg

def _config_fingerprint(doc: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(_config_subset(doc), sort_keys=True,
                                     default=str).encode()).hexdigest()

# Saves a version row at meaningful moments only - a finished build, just before a restore, a moment an integration names
def record_version(workflow: dict[str, Any], reason: str,
                   number: Optional[int] = None) -> Optional[int]:
    clean = {k: v for k, v in workflow.items() if not k.startswith("_")}
    try:
        return db.workflow_version_add(workflow["id"], _config_fingerprint(clean),
                                       reason, _config_subset(clean), number=number)
    except sqlite3.Error:
        return None

# Restores an older version of the workflow while the user's current setting values stay in place
def restore_version(workflow_id: str, version_id: int) -> dict[str, Any]:
    cur = load(workflow_id)
    if cur is None:
        return {"ok": False, "error": "workflow not found"}
    ver = db.workflow_version_get(workflow_id, version_id)
    if ver is None:
        return {"ok": False, "error": "that earlier version no longer exists"}
    record_version(cur, "pre-restore")
    data = ver.get("data") or {}
    cur_vars = {v.get("name"): v for v in cur.get("variables", []) or []}
    for key in _SUBSET_KEYS:
        cur[key] = data.get(key)
    for n in cur.get("nodes", []) or []:
        n.pop("stats", None)
    for v in cur.get("variables", []) or []:
        m = cur_vars.get(v.get("name"))
        if m is not None:
            v["value"] = m.get("value")
            v["persistent"] = m.get("persistent", v.get("persistent", True))
    from storage import secrets_store
    owner = secrets_store.workflow_owner(cur["id"])
    for v in cur.get("variables", []) or []:
        if v.get("secret"):
            v["value"] = True if secrets_store.has_secret(v.get("name"), owner) else None
    save(cur)

    record_version(cur, f"restore::{version_id}", number=ver.get("number"))
    return {"ok": True, "workflow": cur, "restored_ts": ver.get("ts")}

# Writes the workflow to its bundle in one transaction; keys starting with _ are derived and never stored
def save(workflow: dict[str, Any]) -> None:
    if not workflow.get("id"):
        raise ValueError("a workflow document needs an id before it is saved")
    clean = {k: v for k, v in workflow.items() if not k.startswith("_")}
    fp = _config_fingerprint(clean)
    if clean.get("config_hash") != fp:
        clean["config_hash"] = fp
        clean["config_ts"] = time.time()

        workflow["config_hash"] = fp
        workflow["config_ts"] = clean["config_ts"]
    db.workflow_put(clean)
    _write_about(workflow)

# When a turn finishes, the user's own edits made meanwhile win over the turn's stale copy of them
def merge_user_owned(workflow: dict[str, Any], cur: dict[str, Any]) -> None:
    workflow["samples"] = cur.get("samples", [])

    if str(cur.get("description") or "").strip():
        workflow["description"] = cur["description"]

    for key in ("environment_ids", "env_bindings",
                "name", "group", "attention_seen_ts"):
        if key in cur:
            workflow[key] = cur[key]

    cur_tickets = {t.get("id"): t for t in cur.get("tickets", [])}
    for t in workflow.get("tickets", []):
        c = cur_tickets.get(t.get("id"))
        if c and c.get("status") in ("dismissed", "resolved") \
                and t.get("status") not in ("dismissed", "resolved"):
            t["status"] = c["status"]

    cur_nodes = {n.get("id"): n for n in cur.get("nodes", [])}
    for n in workflow.get("nodes", []):
        c = cur_nodes.get(n.get("id"))
        if c is not None and "approval_suppressed" in c:
            n["approval_suppressed"] = c["approval_suppressed"]
    mine = {v.get("name"): v for v in workflow.get("variables", [])}
    for dv in cur.get("variables", []):
        m = mine.get(dv.get("name"))
        if m is None:
            workflow.setdefault("variables", []).append(dv)
        else:
            disk_val = dv.get("value")
            if str(disk_val or "").strip() \
                    or not str(m.get("value") or "").strip():
                m["value"] = disk_val
            m["persistent"] = dv.get("persistent", m.get("persistent", True))

# Merges what the user changed on disk into the turn's copy, before every tool call
def refresh_from_disk(workflow: dict[str, Any]) -> None:
    cur = load_ro(workflow["id"]) if workflow.get("id") else None
    if cur is not None:
        merge_user_owned(workflow, cur)

# Saves a finished turn's copy after folding in anything the user changed meanwhile
def save_turn(workflow: dict[str, Any]) -> None:
    cur = load(workflow["id"])
    if cur is not None:
        merge_user_owned(workflow, cur)

    from storage import secrets_store
    owner = secrets_store.workflow_owner(workflow["id"])
    for v in workflow.get("variables", []):
        if v.get("secret"):
            v["value"] = True if secrets_store.has_secret(v.get("name"), owner) else None
    save(workflow)

# Startup repair: a build that died with the process goes back to draft instead of showing as stuck
def recover_stuck_plans() -> int:
    n = 0
    for meta in list_workflows():
        peek = load_ro(meta["id"])
        if not peek or (peek.get("plan") or {}).get("status") != "building":
            continue
        p = load(meta["id"])
        if p and (p.get("plan") or {}).get("status") == "building":
            p["plan"]["status"] = "draft"
            save(p)
            n += 1
    return n

# Copies a workflow as a new independent one; run history and issues start empty
def duplicate_workflow(workflow_id: str, name: str = "") -> dict:
    import shutil
    import uuid
    if not _safe_id(workflow_id):
        return {"error": "not found"}
    src_doc = load(workflow_id)
    if not src_doc:
        return {"error": "not found"}
    new_id = f"wfl_{uuid.uuid4().hex[:8]}"
    src_dir = config.workflow_dir(workflow_id)
    dst_dir = config.workflow_dir(new_id)
    _ensure_dirs()
    dst_dir.mkdir(parents=True)

    doc = json.loads(json.dumps(src_doc))
    doc["id"] = new_id
    doc["name"] = (name or "").strip() \
        or f"{src_doc.get('name') or 'Untitled'} - Copy"
    doc["tickets"] = []
    doc.pop("last_run_ts", None)
    doc.pop("setup_block", None)

    doc.pop("config_hash", None)
    doc.pop("config_ts", None)
    for n in doc.get("nodes", []):
        n.pop("stats", None)
        n["lineage"] = {"from_node": n.get("id"), "from_project": workflow_id}
    save(doc)

    from storage import secrets_store
    for v in doc.get("variables", []) or []:
        if v.get("secret") and v.get("name"):
            val = secrets_store.get_secret(v["name"], secrets_store.workflow_owner(workflow_id))
            if val is not None:
                secrets_store.set_secret(v["name"], val, secrets_store.workflow_owner(new_id))

    for item in ("corpus.db", "cells.db"):
        s = src_dir / item
        if s.is_dir():
            shutil.copytree(s, dst_dir / item)
        elif s.is_file():
            shutil.copy2(s, dst_dir / item)
    return {"ok": True, "workflow": doc}

# Deletes the workflow's whole bundle folder
def delete_workflow(workflow_id: str) -> dict:
    import shutil
    if not _safe_id(workflow_id):
        return {"error": "not found"}
    src = config.workflow_dir(workflow_id)
    if not (src / "workflow.db").exists():
        return {"error": "not found"}
    shutil.rmtree(src)
    from storage import secrets_store
    owner = secrets_store.workflow_owner(workflow_id)
    for n in secrets_store.names(owner):
        secrets_store.delete_secret(n, owner)
    from storage import blobstore
    blobstore.forget_owner(owner)
    shutil.rmtree(config.browser_profile_dir(workflow_id), ignore_errors=True)
    for f in (config.DATA_DIR / "checkpoints").glob(f"cell_{workflow_id}_*.json"):
        f.unlink(missing_ok=True)
    for rec in db.runstate_all():
        if rec.get("workflow_id") == workflow_id and rec.get("run_id"):
            db.runstate_delete(rec["run_id"])
    for name in (f"{workflow_id}.jsonl", f"{workflow_id}.1.jsonl"):
        (config.DATA_DIR / "logs" / name).unlink(missing_ok=True)
    return {"ok": True}
