# Reusable variable sets shared across workflows; one resolver decides which value wins at run time
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from storage import db
from storage import store

VARIABLE_TYPES = ("text", "number", "date", "boolean", "folder", "file",
                  "filepath")
_TYPE_ALIASES = {"longtext": "text"}

FILE_TYPES = ("file", "filepath")

def setting_type(port_type: str) -> str:
    t = _TYPE_ALIASES.get(str(port_type or "").strip(),
                          str(port_type or "").strip())
    return t if t in VARIABLE_TYPES else ""

# 0 and False are values; only genuinely empty counts as unset
def value_is_set(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    return True

# Stores a value in the type the reading step declares; a value that cannot mean that type is refused with a reason
def coerce_to_type(raw: Any, port_type: str) -> tuple[Any, bool]:
    t = str(port_type or "").strip()
    if t in ("text", "longtext"):
        return (raw if raw is None or isinstance(raw, str) else str(raw)), True
    if not value_is_set(raw):
        return raw, True
    if t == "number":
        if isinstance(raw, bool):
            return raw, False
        if isinstance(raw, (int, float)):
            return raw, True
        if isinstance(raw, str):
            for cast in (int, float):
                try:
                    return cast(raw.strip()), True
                except ValueError:
                    pass
        return raw, False
    if t == "boolean":
        if isinstance(raw, bool):
            return raw, True
        if isinstance(raw, (int, float)) and raw in (0, 1):
            return bool(raw), True
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in ("true", "yes", "on", "1"):
                return True, True
            if s in ("false", "no", "off", "0"):
                return False, True
        return raw, False
    return raw, True

# Reads every linked file into the blob store before the run starts, so the run works on one snapshot
def stage_files(varmap: dict, workflow: dict) -> tuple[dict, list]:
    from storage import blobstore
    types = {}
    for n in workflow_variable_names(workflow) or []:
        types[n] = port_type_for(workflow, n)
    for v in workflow.get("variables") or []:
        if v.get("name") and v.get("type"):
            types[v["name"]] = v["type"]
    out, problems = dict(varmap), []
    for name, val in list(varmap.items()):
        if types.get(name) != "filepath" or not isinstance(val, str):
            continue
        if not val.strip():
            continue
        p = Path(val).expanduser()
        label = _label_for(workflow, name)
        if not p.is_file():
            problems.append(
                f"the file for \"{label}\" isn't there any more "
                f"({p}) - point it at the file again on the Inputs tab, or "
                "upload a copy so it travels with the workflow")
            continue
        try:
            data = p.read_bytes()
        except OSError as e:
            problems.append(f"the file for \"{label}\" ({p}) could not be "
                            f"read: {e.strerror or e}")
            continue
        import mimetypes
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        out[name] = blobstore.put(data, mime, {"name": p.name},
                                  owner=blobstore.workflow_owner(workflow["id"]))
    return out, problems

def _label_for(workflow: dict, name: str) -> str:
    for v in workflow.get("variables") or []:
        if v.get("name") == name:
            return v.get("label") or name
    return name

# Checks the types nothing converts - date, enum, folder, file - where the value is stored, in the user's words
def value_fits_port(raw: Any, port: dict) -> tuple[bool, str]:
    from runtime import verifier
    t = str(port.get("type") or "").strip()
    if not value_is_set(raw):
        return True, ""
    if t == "date":
        return ((True, "") if verifier.is_date(raw)
                else (False, "a date, written like 2026-01-31"))
    if t == "enum":
        opts = [str(o) for o in (port.get("options") or [])]
        if opts and str(raw) not in opts:
            return False, "one of: " + ", ".join(opts)
        return True, ""
    if t in ("folder", "file", "filepath"):
        return ((True, "") if isinstance(raw, str)
                else (False, "a single file" if t != "folder" else "a folder"))
    return True, ""

# The input port that reads this name - the declaration a stored value is held to
def port_for(workflow: dict, name: str) -> dict:
    for n in workflow.get("nodes", []) or []:
        for p in n.get("inputs", []) or []:
            if p.get("name") == name:
                return p
    return {}

# The declared type of the ports reading this name, as a setting type; unrepresentable declarations read as untyped
def port_type_for(workflow: dict, name: str) -> str:
    for n in workflow.get("nodes", []) or []:
        for p in n.get("inputs", []) or []:
            if p.get("name") == name:
                t = setting_type(p.get("type"))
                if t:
                    return t

    for v in workflow.get("variables") or []:
        if v.get("name") == name:
            return setting_type(v.get("type") or "")
    return ""

def type_variable_value(workflow: dict, name: str, raw: Any) -> tuple[Any, bool]:
    t = port_type_for(workflow, name)
    return coerce_to_type(raw, t) if t else (raw, True)

def env_ids(workflow: dict) -> list[str]:
    return [e for e in (workflow.get("environment_ids") or []) if e]

# The attached environments' documents in attach order; deleted ones are silently skipped
def attached(workflow: dict) -> list[dict[str, Any]]:
    out = []
    for eid in env_ids(workflow):
        doc = db.env_get(eid)
        if doc:
            out.append(doc)
    return out

def canonical(env_id: str, name: str) -> str:
    return f"env:{env_id}:{name}"

def parse_canonical(ref: str) -> Optional[tuple[str, str]]:
    if isinstance(ref, str) and ref.startswith("env:"):
        rest = ref[4:]
        eid, _, name = rest.partition(":")
        if eid and name:
            return eid, name
    return None

# One precedence everywhere: the user's explicit pick, then the workflow's own value, then a single environment
def resolve(workflow: dict, envs: Optional[list[dict]] = None) -> dict[str, dict]:
    from storage import secrets_store
    envs = attached(workflow) if envs is None else envs
    by_env: dict[str, dict] = {e["id"]: e for e in envs}
    bindings = workflow.get("env_bindings") or {}

    defined: dict[str, list[dict]] = {}
    for e in envs:
        for v in e.get("variables", []) or []:
            defined.setdefault(v["name"], []).append(e)
    proj_vars = {v["name"]: v for v in workflow.get("variables", []) or []}
    out: dict[str, dict] = {}

    def _env_entry(e: dict, name: str) -> dict:
        v = next(x for x in e.get("variables", []) if x["name"] == name)
        sec = bool(v.get("secret"))
        return {"source": e["id"], "env_name": e.get("name") or "",
                "secret": sec,
                "value": None if sec else v.get("value"),
                "owner": secrets_store.environment_owner(e["id"]) if sec else None,
                "ambiguous": []}

    def _workflow_entry(v: dict) -> dict:
        sec = bool(v.get("secret"))
        return {"source": "workflow", "env_name": "", "secret": sec,
                "value": None if sec else v.get("value"),
                "owner": secrets_store.workflow_owner(workflow["id"]) if sec else None,
                "ambiguous": []}

    for name in set(defined) | set(proj_vars):
        holders = defined.get(name, [])
        ref = bindings.get(name) or ""
        pin = parse_canonical(ref)
        if ref == "workflow" and name in proj_vars:
            out[name] = _workflow_entry(proj_vars[name])
        elif pin and pin[0] in by_env and pin[1] == name \
                and any(h["id"] == pin[0] for h in holders):
            out[name] = _env_entry(by_env[pin[0]], name)
        elif name in proj_vars:
            out[name] = _workflow_entry(proj_vars[name])
        elif len(holders) == 1 and not ref:
            out[name] = _env_entry(holders[0], name)
        else:
            out[name] = {"source": None, "env_name": "", "secret":
                         any(bool(next(x for x in h.get("variables", [])
                                       if x["name"] == name).get("secret"))
                             for h in holders),
                         "value": None, "owner": None,
                         "ambiguous": [{"id": h["id"], "name": h.get("name") or ""}
                                       for h in holders]}

    for name, entry in out.items():
        if entry.get("secret") or not value_is_set(entry.get("value")):
            continue
        typed, understood = type_variable_value(workflow, name, entry["value"])
        if understood:
            entry["value"] = typed
    return out

# Which store owner supplies each secret name this workflow may read: its own rows and its attached environments'
def secret_owners(workflow: dict, envs: Optional[list[dict]] = None) -> dict[str, str]:
    from storage import secrets_store
    out = {n: r["owner"] for n, r in resolve(workflow, envs).items()
           if r.get("secret") and r.get("owner")}
    own = secrets_store.workflow_owner(workflow["id"])
    for n in secrets_store.names(own):
        out.setdefault(n, own)
    return out

# Every input port name minus the names some step produces - the set that resolves from settings
def workflow_variable_names(workflow: dict) -> set:
    nodes = workflow.get("nodes", []) or []
    produced = {p["name"] for n in nodes for p in (n.get("outputs") or [])}
    return {p["name"] for n in nodes for p in (n.get("inputs") or [])} - produced

# Creates an empty setting for each value a plan reads that nothing supplies yet
def ensure_fallback_vars(workflow: dict, names, templates: Optional[dict] = None) -> list:
    created = []
    have = {v["name"] for v in workflow.get("variables", []) or []}
    for n in names:
        if n in have:
            continue
        t = (templates or {}).get(n) or {}
        sec = bool(t.get("secret"))
        workflow.setdefault("variables", []).append(
            {"name": n, "label": t.get("label") or "", "secret": sec,
             "persistent": True, "value": None if sec else ""})
        created.append(n)
    return created

# Drops auto-created blank variables nothing reads any more; a value the user typed is theirs to delete
def retire_unused_fallbacks(workflow: dict, plan: Optional[dict] = None) -> list:
    nodes = workflow.get("nodes", []) or []
    steps = (plan or {}).get("nodes") or []
    if not nodes and not steps:
        return []

    mentioned = set(workflow_variable_names(workflow))
    step_reads = {p.get("name") for n in steps for p in (n.get("inputs") or []) if p.get("name")}
    step_makes = {p.get("name") for n in steps for p in (n.get("outputs") or []) if p.get("name")}
    mentioned |= (step_reads - step_makes)
    keep, dropped = [], []
    for v in workflow.get("variables", []) or []:
        n = v.get("name")
        if (n and n not in mentioned and not v.get("secret")
                and not value_is_set(v.get("value"))):
            dropped.append(n)
            continue
        keep.append(v)
    if dropped:
        workflow["variables"] = keep
    return dropped

# When an environment or its variable is removed, affected workflows fall back to their own settings, never to another environment
def sweep_fallbacks(workflow: dict, env_id: str, gone_vars: list) -> bool:
    gone = {v["name"]: v for v in gone_vars or []}
    if not gone:
        return False
    b = workflow.setdefault("env_bindings", {})
    repinned = [n for n, ref in b.items() if n in gone
                and (parse_canonical(ref) or ("",))[0] == env_id]
    for n in repinned:
        b[n] = "workflow"
    used = workflow_variable_names(workflow)
    res = resolve(workflow)
    need = {n for n in (used & set(gone))
            if n not in res or res[n]["source"] is None}
    created = ensure_fallback_vars(workflow, need, gone)
    return bool(repinned or created)

# Names more than one attached environment defines, unresolved - the input for the design checks
def ambiguous_names(workflow: dict, envs: Optional[list[dict]] = None) -> dict[str, list]:
    return {n: [c["name"] for c in r["ambiguous"]]
            for n, r in resolve(workflow, envs).items() if r["ambiguous"]}

SECRET_LITERAL_RE = re.compile(r'get_secret\(\s*["\']([^"\']+)["\']')

def secret_literal_names(workflow: dict) -> set:
    return {name for n in workflow.get("nodes", []) or []
            for name in SECRET_LITERAL_RE.findall(
                (n.get("config") or {}).get("code") or "")}

# The names the built workflow actually looks up; None while that cannot be judged, which reads as assume-read
def consumed_names(workflow: dict) -> Optional[set]:
    status = (workflow.get("plan") or {}).get("status")
    if workflow.get("nodes") and status in (None, "built"):
        return workflow_variable_names(workflow) | secret_literal_names(workflow)
    return None

# The UI-safe table: which instance is in use and whether any step reads the name - no values
def resolution_summary(workflow: dict) -> list[dict]:
    consumed = consumed_names(workflow)
    return [{"name": n, "secret": r["secret"], "in_use": r["source"],
             "read": consumed is None or n in consumed,
             "ambiguous": [c["id"] for c in r["ambiguous"]]}
            for n, r in sorted(resolve(workflow).items())]

# What the builder has been told about environments, compared at every tool boundary
def snapshot(workflow: dict) -> dict[str, list[str]]:
    return {env["id"]: [v.get("name") for v in env.get("variables", [])
                        if v.get("name")]
            for env in attached(workflow)}

# Tells the running agent, once, that an environment was attached or changed while it worked
def changes_since(workflow: dict) -> str:
    seen = (workflow.get("_turn") or {}).get("env_seen")
    if not isinstance(seen, dict):
        return ""
    now = snapshot(workflow)
    if now == seen:
        return ""
    workflow.setdefault("_turn", {})["env_seen"] = now
    names = {e["id"]: e.get("name") or e["id"] for e in attached(workflow)}
    lines = []
    for eid, vars_ in now.items():
        if eid not in seen:
            lines.append(f'the user ATTACHED environment "{names[eid]}" - '
                         f"variables: {', '.join(_flagged(eid, vars_)) or 'none yet'}")
        else:
            added = [v for v in vars_ if v not in seen[eid]]
            if added:
                lines.append(f'environment "{names[eid]}" gained variables: '
                             f"{', '.join(_flagged(eid, added))}")
    for eid in seen:
        if eid not in now:
            doc = db.env_get(eid)
            lines.append(f'the user DETACHED environment '
                         f'"{(doc or {}).get("name") or eid}"')
    if not lines:
        return ""
    return ("[environments changed] " + "; ".join(lines)
            + ". Name a variable EXACTLY to use its value at run time - never ask the user to re-type a value that is already stored.")

def _flagged(env_id: str, names: list[str]) -> list[str]:
    doc = db.env_get(env_id) or {}
    secret = {v.get("name") for v in doc.get("variables", []) if v.get("secret")}
    return [f"{n} (secret)" if n in secret else n for n in names]

# Names-only lines for environments not attached, so the agent can say attach X instead of asking for a stored value
def unattached_summary(workflow: dict) -> list[str]:
    have = set(env_ids(workflow))
    out = []
    for data in db.env_all():
        if data.get("id") in have:
            continue
        names = [v.get("name") for v in data.get("variables", []) if v.get("name")]
        out.append(f'"{data.get("name") or data.get("id")}" '
                   f"({', '.join(_flagged(data['id'], names)) or 'no variables yet'})")
    return sorted(out, key=str.lower)

# Which unattached environments hold a given name - names only, for "attach it" refusals
def unattached_holders(workflow: dict, names) -> list[tuple[str, str]]:
    have = set(env_ids(workflow))
    wanted = {str(n) for n in names or [] if n}
    out: list[tuple[str, str]] = []
    for data in db.env_all():
        if data.get("id") in have:
            continue
        for v in data.get("variables", []) or []:
            if v.get("name") in wanted:
                out.append((v["name"], data.get("name") or data.get("id")))
    return sorted(out)

# The attached environment that already supplies a name, if any
def attached_holder(workflow: dict, name: str) -> Optional[str]:
    for e in attached(workflow):
        if any(v.get("name") == name for v in e.get("variables", []) or []):
            return e.get("name") or e.get("id")
    return None

# Lightweight cards: name, variable count and how many workflows use each
def list_environments() -> list[dict[str, Any]]:
    used: dict[str, int] = {}
    for p in store.list_workflows():
        for eid in env_ids(p):
            used[eid] = used.get(eid, 0) + 1
    out = []
    for data in db.env_all():
        out.append({**{k: data.get(k) for k in ("id", "name", "description", "group")},
                    "variable_count": len(data.get("variables", [])),
                    "used_by": used.get(data.get("id"), 0)})
    out.sort(key=lambda e: (e.get("name") or "").lower())
    return out

def load(env_id: str) -> Optional[dict[str, Any]]:
    return db.env_get(env_id)

# Stamps whether each secret has a value, derived from the store - a secret's stored row is always empty
def annotate_secret_state(env: dict[str, Any]) -> dict[str, Any]:
    from storage import secrets_store
    for v in env.get("variables", []):
        if v.get("secret"):
            v["value_set"] = secrets_store.has_secret(
                v["name"], secrets_store.environment_owner(env["id"]))
    return env

# Copies an environment with its secret values under the copy's own store names; the copy starts unattached
def duplicate_environment(env_id: str) -> dict[str, Any]:
    import json as _json

    import models
    from storage import secrets_store
    src = load(env_id)
    if not src:
        return {"error": "not found"}
    env = _json.loads(_json.dumps(src))
    env["id"] = models.Environment().id
    env["name"] = f"{src.get('name') or 'Untitled environment'} - Copy"
    for v in env.get("variables", []):
        v.pop("value_set", None)
        if v.get("secret"):
            val = secrets_store.get_secret(
                v.get("name"), secrets_store.environment_owner(env_id))
            if val is not None:
                secrets_store.set_secret(
                    v.get("name"), val, secrets_store.environment_owner(env["id"]))
    save(env)
    return {"ok": True, "environment": env}

def save(env: dict[str, Any]) -> None:
    db.env_put(env)

# Removes the environment, detaches it everywhere and deletes its secret values - unreachable once the environment is gone
def delete(env_id: str) -> None:
    env = load(env_id)
    env_vars = (env or {}).get("variables", []) or []
    from storage import secrets_store as _secrets
    for v in env_vars:
        if v.get("secret"):
            _secrets.delete_secret(v["name"], _secrets.environment_owner(env_id))
    db.env_delete(env_id)
    for meta in store.list_workflows():
        if env_id not in env_ids(meta):
            continue
        workflow = store.load(meta["id"])
        if not workflow:
            continue
        workflow["environment_ids"] = [e for e in env_ids(workflow) if e != env_id]

        sweep_fallbacks(workflow, env_id, env_vars)
        workflow["env_bindings"] = {
            n: ref for n, ref in (workflow.get("env_bindings") or {}).items()
            if ref == "workflow"
            or (parse_canonical(ref) or ("", ""))[0] != env_id}
        store.save(workflow)
