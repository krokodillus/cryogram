# What a completed run keeps, and the run history; intermediates are never stored
from __future__ import annotations

import json
import time
from typing import Any, Optional

from storage import blobstore
import config

_INLINE_CAP = 64 * 1024

def outputs_dir(workflow_id: str):
    return config.workflow_dir(workflow_id) / "outputs"

# Validates the declaration and pins each entry to a node id; errors are reasoned sentences
def normalise(workflow: dict, items: list) -> tuple[list, list[str]]:
    by_id = {n["id"]: n for n in workflow.get("nodes", [])}
    by_name = {n.get("name"): n for n in workflow.get("nodes", [])}
    norm, errors = [], []
    for i, it in enumerate(items or []):
        node = by_id.get(it.get("node")) or by_name.get(it.get("node"))
        if not node:
            errors.append(f"deliverable {i}: no node {it.get('node')!r} in the workflow")
            continue
        port = str(it.get("port") or "").strip()
        declared = {p["name"]: p for p in node.get("outputs", [])}
        if port not in declared:
            errors.append(f"deliverable {i}: node {node.get('name')!r} has no "
                          f"output {port!r} - declared outputs: {sorted(declared)}")
            continue
        if declared[port].get("type") == "secret":
            errors.append(f"deliverable {i}: {port!r} is a secret - secrets are "
                          "never stored as results")
            continue
        label = str(it.get("label") or "").strip() \
            or declared[port].get("label") or port
        norm.append({"node": node["id"], "port": port, "label": label})
    return norm, errors

# Every non-secret value the run started from, labelled; a secret is named, never valued
def run_inputs(workflow: dict, entry_inputs: Optional[dict] = None) -> list:
    from storage import environments
    out = []
    try:
        resolved = environments.resolve(workflow)

        used = environments.workflow_variable_names(workflow)
    except Exception:
        resolved, used = {}, set()
    labels = {v.get("name"): v.get("label")
              for v in workflow.get("variables") or []}
    for name, value in (entry_inputs or {}).items():
        out.append({"name": name, "label": labels.get(name) or name,
                    "value": value, "source": "asked at the start"})
    seen = {e["name"] for e in out}
    for name, r in sorted(resolved.items()):
        if name in seen or name not in used:
            continue
        if r.get("secret"):
            out.append({"name": name, "label": labels.get(name) or name,
                        "secret": True, "source": "kept privately"})
        elif r.get("value") not in (None, ""):
            out.append({"name": name, "label": labels.get(name) or name,
                        "value": r.get("value"), "source": "a saved setting"})
    return out

# Writes the run's manifest: the kept results, timings, what the run was given, and any set-aside items
def persist(workflow: dict, run_id: str, outputs: dict, path: list,
            usage: Optional[list] = None, started: Optional[float] = None,
            paused: bool = False, entry_inputs: Optional[dict] = None,
            set_aside: Optional[dict] = None,
            steps: Optional[list] = None,
            partial: Optional[dict] = None) -> dict:
    results = []
    owner = blobstore.workflow_owner(workflow.get("corpus_workflow_id") or workflow["id"])
    for d in workflow.get("deliverables", []) or []:
        entry = {"node": d["node"], "port": d["port"], "label": d["label"]}
        node_out = outputs.get(d["node"])
        if not isinstance(node_out, dict) or d["port"] not in node_out:
            entry["skipped"] = True
        else:
            entry.update(_bound(node_out[d["port"]], d["label"], owner))
        results.append(entry)
    manifest = {"run_id": run_id, "ts": time.time(), "status": "completed",
                "path": list(path or []), "results": results}

    if workflow.get("config_hash"):
        manifest["config_hash"] = workflow["config_hash"]
    given = run_inputs(workflow, entry_inputs)
    if given:
        manifest["given"] = given
    if started:
        manifest["started"] = started
        if paused:
            manifest["paused"] = True
    if set_aside:
        by_id = {n["id"]: n.get("name") or n["id"] for n in workflow.get("nodes", [])}
        manifest["set_aside"] = [
            {"node": nid, "name": by_id.get(nid, nid), "policy": e.get("policy"),
             "ports": e.get("ports") or []}
            for nid, e in set_aside.items()]
    if partial:
        manifest["partial"] = dict(partial)
    if steps:
        manifest["steps"] = bounded_trace(steps)
    if usage:
        manifest["ai_usage"] = {
            "steps": usage,
            "total": {"in": sum(u.get("in", 0) for u in usage),
                      "out": sum(u.get("out", 0) for u in usage)}}
    d = outputs_dir(workflow["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{run_id}.json").write_text(json.dumps(manifest, indent=2, default=str))
    return manifest

# One value, manifest-shaped: blob refs stay refs, small values inline, big ones become blobs
def _bound(value: Any, label: str, owner: str) -> dict:
    if isinstance(value, str) and value.startswith("blob:") and blobstore.exists(value):
        st = blobstore.stat(value, owner)
        return {"ref": value, "mime": st.get("mime", ""), "size": st.get("size"),
                "name": st.get("name", "")}
    encoded = json.dumps(value, default=str)
    if len(encoded.encode()) <= _INLINE_CAP:
        return {"value": value}
    ref = blobstore.put(encoded.encode(), "application/json", {"name": f"{label}.json"},
                        owner=owner)
    return {"ref": ref, "mime": "application/json", "size": len(encoded.encode()),
            "name": f"{label}.json"}

TRACE_VALUE_HEAD = 500
TRACE_VALUE_TAIL = 100
TRACE_LIST_ITEMS = 5
TRACE_RECORD_FIELDS = 12
TRACE_DEPTH = 3
TRACE_STEP_BYTES = 24 * 1024
TRACE_NOTES = 5

# One value as the run history shows it: capped at every level, every cut marked
def trace_preview(value: Any, depth: int = TRACE_DEPTH,
                  owner: Optional[str] = None) -> Any:
    if isinstance(value, str):
        if value.startswith("blob:") and blobstore.exists(value):
            st = blobstore.stat(value, owner)
            name = st.get("name") or "file"
            size = st.get("size")
            return (f"a stored file: {name}"
                    + (f" ({size:,} bytes)" if size else ""))
        cap = TRACE_VALUE_HEAD + TRACE_VALUE_TAIL
        if len(value) > cap + 80:
            return (value[:TRACE_VALUE_HEAD]
                    + f" ...[shortened - {len(value):,} characters in the "
                      "full value]... "
                    + value[-TRACE_VALUE_TAIL:])
        return value
    if isinstance(value, list):
        if depth <= 0:
            return (f"a list of {len(value)} "
                    f"item{'s' if len(value) != 1 else ''}")
        out = [trace_preview(v, depth - 1, owner) for v in value[:TRACE_LIST_ITEMS]]
        if len(value) > TRACE_LIST_ITEMS:
            fields = ""
            if value and isinstance(value[0], dict):
                fields = " (fields: " + ", ".join(
                    sorted(str(k) for k in value[0])[:6]) + ")"
            out.append(f"...and {len(value) - TRACE_LIST_ITEMS} more "
                       f"items{fields}")
        return out
    if isinstance(value, dict):
        if depth <= 0:
            return (f"a record with {len(value)} "
                    f"field{'s' if len(value) != 1 else ''}")
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= TRACE_RECORD_FIELDS:
                out["..."] = f"and {len(value) - TRACE_RECORD_FIELDS} more fields"
                break
            out[str(k)] = trace_preview(v, depth - 1, owner)
        return out
    return value

# Holds one run's step trace to its per-step budget: an oversized step's values are shown at less depth, and say so
def bounded_trace(entries: list) -> list:
    out: list = []
    for e in entries or []:
        e = dict(e)
        size = len(json.dumps(e, default=str).encode())
        if size > TRACE_STEP_BYTES:
            for k in ("inputs", "output"):
                if k in e:
                    e[k] = trace_preview(e[k], depth=1)
            e["note"] = "shortened - this step's values were large"
            size = len(json.dumps(e, default=str).encode())
        out.append(e)
    return out

RUNS_PER_PAGE = 25

# Completed manifests plus still-paused runs, newest first; nothing appears twice
def list_runs(workflow_id: str, limit: int | None = None,
              offset: int = 0) -> list[dict]:
    runs: list[dict] = []
    d = outputs_dir(workflow_id)
    if d.exists():
        for f in d.glob("run_*.json"):
            try:
                runs.append(json.loads(f.read_text()))
            except Exception:
                continue
    from storage import db
    from runtime import run_state
    for rec in db.runstate_all():
        if rec.get("workflow_id") == workflow_id and rec.get("status") == "halted":
            row = {"run_id": rec["run_id"], "ts": rec.get("started"),
                   "status": "halted", "halted_at": rec.get("halted_at"),
                   "reason": rec.get("reason"), "results": []}

            if rec.get("progress"):
                row["progress"] = {k: v for k, v in rec["progress"].items()
                                   if k not in ("items", "done_keys")}
            if rec.get("partial"):
                row["partial"] = dict(rec["partial"])
            if rec.get("waiting_on_you") is not None:
                row["waiting_on_you"] = bool(rec.get("waiting_on_you"))
            if rec.get("verdict"):
                row["verdict"] = rec["verdict"]
            if rec.get("seen"):
                row["seen"] = True

            trace = run_state.trace(rec)
            if trace:
                row["steps"] = bounded_trace(trace)
            runs.append(row)
    runs.sort(key=lambda r: r.get("ts") or 0, reverse=True)
    if limit is None:
        return runs[offset:]
    return runs[offset:offset + limit]

# The user has seen this run's outcome, live or on arrival - the manifest or the buffer remembers it
def mark_seen(workflow_id: str, run_id: str) -> bool:
    f = outputs_dir(workflow_id) / f"{run_id}.json"
    if f.exists():
        try:
            m = json.loads(f.read_text())
            m["seen"] = True
            f.write_text(json.dumps(m, indent=2, default=str))
            return True
        except Exception:
            return False
    from runtime import run_state
    return run_state.mark_seen(run_id)

# The newest run whose outcome nobody has seen yet, so the page opens its modal on arrival
def latest_unseen(workflow_id: str) -> Optional[dict]:
    from runtime import run_state
    rows = list_runs(workflow_id, limit=1)
    if not rows:
        return None
    r = rows[0]
    if r.get("seen"):
        return None
    if r.get("status") == "halted" and (
            r.get("reason") in run_state.USER_ENDED or not r.get("halted_at")):
        return None
    return {k: r.get(k) for k in ("run_id", "status", "reason", "halted_at",
                                  "waiting_on_you", "verdict", "ts", "partial")
            if r.get(k) is not None}

# Run history a page at a time, 25 per page
def runs_page(workflow_id: str, page: int = 1) -> dict:
    every = list_runs(workflow_id)
    total = len(every)
    pages = max(1, -(-total // RUNS_PER_PAGE))
    page = min(max(1, int(page or 1)), pages)
    start = (page - 1) * RUNS_PER_PAGE
    return {"runs": every[start:start + RUNS_PER_PAGE], "page": page,
            "pages": pages, "total": total, "per_page": RUNS_PER_PAGE}
