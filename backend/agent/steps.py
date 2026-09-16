# What every part of the app knows about steps: finding one, its kind and ports, the plan's shape, the recorded evidence, the cards' payloads, and saving the workflow
from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import asdict
from typing import Optional
from storage import blobstore
import config
from runtime import corpus
from runtime import sandbox
from storage import deliverables
from agent import gate
from agent import plan_logic
import providers
from agent import transcript as _transcript
from agent import turnstate
from storage import store
from runtime import verifier
from models import ModelRef, Node, NodeType
from step_types import CODE_TYPES, OUTSIDE_TYPES
from models import canonicalise_refs, humanise_name
from models import code_key

PORT_TYPE_NAMES = {"code", "connector", "browser", "ai", "user-input"}

PLAN_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "nodes": {"type": "array",
                  "description": "The steps, in the order the work happens. A first save carries every step and every line between them; a later save carries only the steps it changes or adds.",
                  "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "type": {"type": "string", "enum": sorted(PORT_TYPE_NAMES)},
                "description": {"type": "string"},
                "read_only": {"type": "boolean"},
                "url": {"type": "string"},
                "inputs": {"type": "array", "items": {"type": "object"}},
                "outputs": {"type": "array", "items": {"type": "object"}},
                "domains": {"type": "array", "items": {"type": "string"}},
                "paths": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "type"]}},
        "edges": {"type": "array",
                  "description": "The lines: which step follows which. REQUIRED whenever the save carries the plan's steps - every step of two or more needs at least one line (from each step to the one that follows it; a step needing two earlier ones gets a line from each), and a save without them is refused. Leave the key out on a save that carries only a changed step, so the saved lines stay; an edges list that is present replaces every line.",
                  "items": {
            "type": "object",
            "properties": {"src": {"type": "string"}, "dst": {"type": "string"},
                           "when": {"type": "string"}},
            "required": ["src", "dst"]}},
        "changes": {"type": "object"},
        "fix_note": {"type": "object"},
        "change_note": {"type": "object"},
        "replace": {"type": "boolean"},
    },
}

DOMAIN = re.compile("^[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?(\\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?)+$")

_HARDNESS = {"hard", "soft"}

EXPECT = {"ok", "reject"}

PORT_TYPES = {"folder",
              "text", "longtext", "number", "boolean", "date", "enum",
              "file", "record", "list", "secret"}

UNTYPED_PORTS = ("every input and output of a step needs its type before the step is tried or built (text, longtext, number, boolean, date, enum, file, folder, record, list, secret) - set it on the plan step with save_plan")

# The ports of a step that have no type yet, as "input name" / "output name"
def untyped_ports(step: dict) -> list[str]:
    out = []
    for side in ("inputs", "outputs"):
        for p in step.get(side) or []:
            if isinstance(p, dict) and not p.get("type"):
                out.append(f"{side[:-1]} {p.get('name')!r}")
    return out

# The setting type an input of this name is read as, the plan's steps first, then the built ones
def setting_type_for(workflow: dict, plan: Optional[dict], name: str) -> str:
    from storage import environments as _envs
    for n in (plan or {}).get("nodes") or []:
        for p in n.get("inputs") or []:
            if p.get("name") == name:
                t = _envs.setting_type(p.get("type"))
                if t:
                    return t
    return _envs.port_type_for(workflow, name)

# Every stored setting takes the type of the input that reads it; the names whose value cannot mean that type come back
def retype_settings(workflow: dict, plan: Optional[dict]) -> list[str]:
    from storage import environments as _envs
    from storage import store as _store
    wrong: list[str] = []
    retyped: dict = {}
    for v in workflow.get("variables") or []:
        if v.get("secret") or not _envs.value_is_set(v.get("value")):
            continue
        t = setting_type_for(workflow, plan, str(v.get("name") or ""))
        if not t:
            continue
        val, understood = _envs.coerce_to_type(v.get("value"), t)
        if not understood:
            wrong.append(str(v.get("name")))
        elif val != v.get("value") or v.get("type") != t:
            v["value"], v["type"] = val, t
            retyped[str(v.get("name"))] = (val, t)

    if retyped:
        fresh = _store.load(workflow.get("id") or "")
        if fresh is not None:
            for v in fresh.get("variables") or []:
                hit = retyped.get(str(v.get("name")))
                if hit and not v.get("secret"):
                    v["value"], v["type"] = hit
            _store.save(fresh)
    return wrong

FILE_KINDS = {"pdf", "image", "spreadsheet", "document", "text", "other"}
_FILE_KIND_SYNONYMS = {
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "webp": "image",
    "picture": "image", "photo": "image", "screenshot": "image", "images": "image",
    "xlsx": "spreadsheet", "xls": "spreadsheet", "csv": "spreadsheet", "tsv": "spreadsheet",
    "sheet": "spreadsheet", "excel": "spreadsheet",
    "docx": "document", "doc": "document", "pptx": "document", "ppt": "document",
    "word": "document", "powerpoint": "document", "slides": "document",
    "txt": "text", "md": "text", "markdown": "text", "json": "text", "xml": "text",
    "html": "text", "plain": "text",
}

# Maps a file kind the model wrote (png, xlsx, Word) onto the app's own words
def canon_file_kind(value) -> str:
    v = str(value or "").strip().lower().lstrip(".")
    if not v:
        return ""
    if v in FILE_KINDS:
        return v
    return _FILE_KIND_SYNONYMS.get(v, "other")

# The kinds of file a port takes: a list, usually of one, filled from the sample the step was tried on
def port_file_kinds(port: dict) -> list:
    raw = port.get("file_kinds")
    if not raw and port.get("file_kind"):
        raw = [port.get("file_kind")]
    out = []
    for k in (raw if isinstance(raw, list) else [raw]) if raw else []:
        c = canon_file_kind(k)
        if c and c not in out:
            out.append(c)
    return out

# A file port that has not been proven on a file yet learns its kind from the first one it is tried on
def seed_file_kinds(workflow: dict, step_name: str, inputs: dict) -> bool:
    from runtime.capability import _file_kind
    from storage import blobstore
    kinds_by_name: dict[str, str] = {}
    for k, v in (inputs or {}).items():
        if isinstance(v, str) and v.startswith("blob:") and blobstore.exists(v):
            st = blobstore.stat(v) or {}
            kinds_by_name[k] = _file_kind(st.get("mime") or "", st.get("name") or k)
    if not kinds_by_name:
        return False
    changed = False
    holders = []
    node = find_node(workflow, step_name)
    if node:
        holders.append(node)
    for pn in (workflow.get("plan") or {}).get("nodes") or []:
        if pn.get("name") == step_name:
            holders.append(pn)
    for h in holders:
        for port in h.get("inputs") or []:
            if isinstance(port, dict) and port.get("type") == "file" \
                    and port.get("name") in kinds_by_name and not port_file_kinds(port):
                port["file_kinds"] = [kinds_by_name[port["name"]]]
                port.pop("file_kind", None)
                changed = True
    if node and changed:
        refresh_sends_file(node)
    return changed

# An AI step that hands a file to its model says so on its config, read off its own inputs
def refresh_sends_file(node: dict) -> None:
    cfg = node.setdefault("config", {})
    if node_type_of(node) != "ai":
        cfg.pop("sends_file", None)
        return
    kinds = sorted({k for p in node.get("inputs") or []
                    if isinstance(p, dict) and p.get("type") == "file"
                    for k in (port_file_kinds(p) or ["other"])})
    if kinds:
        cfg["sends_file"] = {"kinds": kinds}
    else:
        cfg.pop("sends_file", None)

_TYPE_SYNONYMS = {
    "array": "list", "sequence": "list", "tuple": "list", "set": "list",
    "string": "text", "str": "text", "char": "text",
    "int": "number", "integer": "number", "float": "number", "double": "number",
    "decimal": "number", "num": "number",
    "bool": "boolean",
    "dict": "record", "object": "record", "json": "record", "map": "record",
    "obj": "record",
    "datetime": "date", "timestamp": "date", "time": "date",
    "email": "text", "url": "text", "uuid": "text",
}

# Maps the type names models reach for (string, int, dict) onto the app's own vocabulary before validation
def canon_port_type(t) -> str:
    if not isinstance(t, str):
        return ""
    key = t.strip().lower()
    if key in PORT_TYPES:
        return key
    base = key.split("[", 1)[0].split("<", 1)[0].split("(", 1)[0].strip()
    if base in ("list", "array", "sequence", "tuple", "set"):
        return "list"
    return _TYPE_SYNONYMS.get(key) or _TYPE_SYNONYMS.get(base) or key

# Normalises every port type in place, item_fields included
def canon_ports(ports) -> list:
    for p in ports or []:
        if isinstance(p, dict) and "type" in p:
            p["type"] = canon_port_type(p.get("type"))
        if isinstance(p, dict):
            for f in p.get("item_fields") or []:
                if isinstance(f, dict) and "type" in f:
                    f["type"] = canon_port_type(f.get("type"))
    return ports or []

# Whether a port describes a list: by its type, or by an item shape on a port with no type (the one-time move at boot reads this)
def is_list_port(p: dict) -> bool:
    if str(p.get("type") or "") == "list":
        return True
    if p.get("item_fields") and str(p.get("type") or "") != "record":
        return True
    return (p.get("schema") or {}).get("type") == "array"

# The type a recorded value shows: the port vocabulary word for a plain value
def type_of_value(v) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "text"
    if isinstance(v, dict):
        return "record"
    if isinstance(v, list):
        return "list"
    return ""

# One resolver for a step reference: the id first, then the name it currently carries, plan steps then built nodes
def step_by_ref(workflow: dict, plan: Optional[dict], ref) -> Optional[dict]:
    ref = str(ref or "").strip()
    if not ref:
        return None
    steps = list((plan or workflow.get("plan") or {}).get("nodes") or []) \
        + list(workflow.get("nodes") or [])
    hit = next((n for n in steps if str(n.get("id") or "") == ref), None)
    if hit is not None:
        return hit
    want = humanise_name(ref)
    return next((n for n in steps
                 if humanise_name(str(n.get("name") or "")) == want), None)

# The id a step reference stands for, or "" when nothing has it yet
def step_id_for(workflow: dict, ref, plan: Optional[dict] = None) -> str:
    hit = step_by_ref(workflow, plan, ref)
    return str((hit or {}).get("id") or "")

# Did the user say no to trying this step live? Read by its id first, its name second
def is_declined(workflow: dict, step) -> bool:
    d = declined_steps(workflow)
    if isinstance(step, dict):
        return bool((step.get("id") and step["id"] in d)
                    or (step.get("name") and humanise_name(str(step["name"])) in d))
    ref = str(step or "")
    return bool(ref and (ref in d or humanise_name(ref) in d
                         or step_id_for(workflow, ref) in d))

# Change lists at rest are ids; the card and the findings read them as names
def changes_to_ids(workflow: dict, plan: dict) -> dict:
    ch = plan.get("changes") if isinstance(plan.get("changes"), dict) else {}
    out = {}
    for k, v in ch.items():
        if isinstance(v, list):
            out[k] = [step_id_for(workflow, x, plan) or x if isinstance(x, str) else x
                      for x in v]
        else:
            out[k] = v
    return out

def changes_to_names(workflow: dict, plan: dict) -> dict:
    ch = plan.get("changes") if isinstance(plan.get("changes"), dict) else {}
    out = {}
    for k, v in ch.items():
        if isinstance(v, list):
            out[k] = [str((step_by_ref(workflow, plan, x) or {}).get("name") or x)
                      if isinstance(x, str) else x for x in v]
        else:
            out[k] = v
    return out

# Resolves by id or exact name - the model naturally uses names
def find_node(workflow: dict, node_id: str) -> Optional[dict]:
    n = next((n for n in workflow["nodes"] if n["id"] == node_id), None)
    if n is None and node_id:
        n = next((n for n in workflow["nodes"] if n.get("name") == node_id), None)
    return n

# Node type as a plain string, enum or stored string alike
def node_type_of(node: dict) -> str:
    t = node.get("type")
    return t.value if hasattr(t, "value") else str(t)

# Every write tool echoes the updated node, so a re-read after an edit adds nothing
def _state(node: dict) -> dict:
    cfg = node.get("config", {})
    return {"id": node["id"], "name": node.get("name"), "type": node_type_of(node),
            "inputs": [p["name"] + ":" + (p.get("type") or "untyped")
                       for p in node.get("inputs", [])],
            "outputs": [p["name"] + ":" + (p.get("type") or "untyped")
                        for p in node.get("outputs", [])],
            "has_code": bool(cfg.get("code")), "has_prompt": bool(cfg.get("prompt")),
            "model": (cfg.get("model") or {}).get("model", ""),
            "tests": len(node.get("tests", []))}

# The same closed grammar the runtime interprets, so nothing downstream refuses what was accepted here
def expr_ok(expr: str) -> bool:
    from runtime import safe_eval
    return safe_eval.check(expr, set(verifier.FUNCTIONS)) is None

# One validator for test asserts, shared by save and set so the two cannot disagree
def assert_problems(asserts) -> list:
    from runtime import safe_eval
    out = []
    for a in asserts or []:
        if re.search(r"\boutput\b", str(a)):
            out.append(f"assert {a!r}: asserts see the step's output FIELDS as "
                       "bare names - write len(headlines) == 3, never output.headlines")
            continue
        problem = safe_eval.check(a, set(verifier.FUNCTIONS))
        if problem:
            out.append(f"assert {a!r} is not allowed: {problem}")
    return out

def save(workflow: dict) -> None:
    store.save_turn(workflow)

def set_prompt_model(workflow: dict, node_id: str, prompt: str, model: str,
                         temperature: float = 0.0, provider_id: str = "") -> dict:
    node = find_node(workflow, node_id)
    if not node:
        return {"error": f"no node {node_id!r}"}
    if node_type_of(node) != "ai":
        return {"error": f"set_prompt_model only applies to ai nodes, not {node_type_of(node)} "
                         "(all intelligence goes through ai_call; declare an ai node)"}

    model = str(model or "").strip()
    provider_id = str(provider_id or "").strip()
    if model and not providers.find_model(model, for_node=True,
                                          provider_id=provider_id)[1]:
        avail = [m["name"] + (f' ({m["provider"]})' if m["ambiguous"] else "")
                 for m in providers.node_models()]
        return {"error": f"model {model!r} is not set up in Admin"
                         + (f" under provider {provider_id!r}" if provider_id else "")
                         + f". Use one of the configured models "
                         f"{avail or '(none configured)'}, or leave "
                         "the model blank and tell the user you'll set it once they add one in Admin."}
    node["config"]["prompt"] = prompt
    # The agent's version of the prompt is kept beside it, so a user edit in the drawer can always be reset to it
    node["config"]["prompt_agent"] = prompt
    node["config"]["model"] = asdict(ModelRef(model=model, temperature=temperature,
                                              provider_id=provider_id))
    refresh_sends_file(node)
    save(workflow)
    return {"ok": True, "state": _state(node)}

def clean_criteria(criteria: list[dict], label: str) -> tuple[list, list]:
    from runtime import safe_eval
    errors, clean = [], []
    for i, c in enumerate(criteria or []):
        problem = safe_eval.check(c.get("expr", ""), set(verifier.FUNCTIONS))
        if problem:
            errors.append(f"{label} {i}: {problem}")
        if c.get("hardness", "hard") not in _HARDNESS:
            errors.append(f"{label} {i}: hardness must be hard|soft")
        clean.append({"expr": c.get("expr", ""), "field": c.get("field"),
                      "hardness": c.get("hardness", "hard"),
                      "label": str(c.get("label") or "").strip(),
                      "justification": c.get("justification", "")})
    return clean, errors

def _would_cycle(workflow: dict, src: str, dst: str) -> bool:
    succ: dict[str, list[str]] = {}
    for e in workflow.get("edges", []):
        succ.setdefault(e["src"], []).append(e["dst"])
    stack, seen = [dst], set()
    while stack:
        n = stack.pop()
        if n == src:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(succ.get(n, []))
    return False

# One field, one name: a key gets the same human label everywhere it appears
def unify_field_labels(workflow: dict) -> int:
    from collections import Counter
    votes: dict = {}
    holders: dict = {}
    def note(d):
        key = str(d.get("name") or "").strip()
        if not key:
            return
        holders.setdefault(key, []).append(d)
        lab = str(d.get("label") or "").strip()
        if lab:
            votes.setdefault(key, Counter())[lab] += 1
    for n in workflow.get("nodes", []):
        for p in (n.get("inputs") or []) + (n.get("outputs") or []):
            note(p)
    for v in workflow.get("variables", []):
        note(v)
    changed = 0
    for key, ds in holders.items():
        cnt = votes.get(key)
        if not cnt:
            continue
        canonical = sorted(cnt.items(),
                           key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[0][0]
        for d in ds:
            if str(d.get("label") or "").strip() != canonical:
                d["label"] = canonical
                changed += 1
    return changed

def _connect_one(workflow: dict, src: str, dst: str, when: str = "") -> dict:
    if src == dst:
        return {"error": "a node cannot connect to itself"}
    a, b = find_node(workflow, src), find_node(workflow, dst)
    if not a:
        return {"error": f"no node {src!r}"}
    if not b:
        return {"error": f"no node {dst!r}"}
    if when and not expr_ok(when):
        return {"error": f"when {when!r} is not a valid python expression (a predicate "
                         "over the source node's outputs, e.g. \"doc_type == 'A'\")"}

    src_id, dst_id = a["id"], b["id"]
    edges = workflow.setdefault("edges", [])
    existing = next((e for e in edges
                     if e["src"] == src_id and e["dst"] == dst_id), None)
    if existing:
        if str(existing.get("when") or "") == str(when or ""):
            return {"ok": True, "note": f"already wired: {src} -> {dst} - "
                                        "nothing to change"}
        return {"error": f"edge {src} -> {dst} already exists with a different "
                         f"condition ({existing.get('when') or 'none'}); "
                         "disconnect first if you mean to change it"}
    if _would_cycle(workflow, src_id, dst_id):
        return {"error": f"edge {src} -> {dst} would create a cycle; the workflow must be a DAG"}

    edges.append({"src": src_id, "dst": dst_id, "when": when})
    save(workflow)
    return {"ok": True, "order": f"{a.get('name') or src} -> "
                                 f"{b.get('name') or dst}"
                                 + (f" when {when}" if when else "")}

# Re-reads samples from the store, since the user can upload mid-turn
def fresh_samples(workflow: dict) -> list:
    fresh = store.load(workflow["id"])
    if fresh is not None:
        workflow["samples"] = fresh.get("samples", [])
    return workflow.get("samples", [])

def _same_json(a, b) -> bool:
    import json as _j
    try:
        return _j.dumps(a, sort_keys=True, default=str) == \
            _j.dumps(b, sort_keys=True, default=str)
    except Exception:
        return False

# Drops plan code and tests that duplicate a recording exactly - the harness carries those; a real edit is never reverted
def drop_recorded_duplicates(workflow_id: str, nodes: list) -> list:
    from agent import cells as _cells
    carried = []
    for n in nodes or []:
        kind = "ai" if n.get("type") == "ai" else "code"
        if n.get("type") not in (*CODE_TYPES, "ai"):
            continue
        rec = _cells.latest_ok_for(workflow_id, n, kind=kind)
        if not rec:
            continue
        hit = False
        if kind == "code" and str(n.get("code") or "").strip() \
                and code_key(n.get("code")) == code_key(rec.get("code")):
            n.pop("code", None)
            hit = True
        tests = n.get("tests")
        if isinstance(tests, list) and len(tests) == 1 \
                and _same_json((tests[0] or {}).get("inputs") or {},
                               rec.get("inputs") or {}):
            n.pop("tests", None)
            hit = True
        if hit:
            carried.append(n.get("name"))
    return carried

# A step with no code is filled from its recorded run - code, packages and the recording as its first test - carried by the harness, not promised by the model
def fill_from_cells(workflow_id: str, nodes: list) -> list:
    from agent import cells as _cells
    filled = []
    for n in nodes or []:
        if n.get("type") in CODE_TYPES:
            rec = _cells.latest_ok_for(workflow_id, n, kind="code")
            if not rec:
                continue

            changed = False
            if not str(n.get("code") or "").strip():
                n["code"] = rec["code"]
                changed = True
            elif code_key(n["code"]) != code_key(rec.get("code")):
                older = _cells.ok_codes(workflow_id, str(n.get("name") or ""), kind="code", step_id=str(n.get("id") or "") or None)[1:]
                if any(code_key(n["code"]) == code_key(c) for c in older):
                    n["code"] = rec["code"]
                    ts = n.get("tests") or []
                    if len(ts) == 1 and ts[0].get("name") == "recorded run":
                        ts[0]["inputs"] = rec.get("inputs") or {}
                    changed = True
            if rec.get("packages") and not n.get("packages"):
                n["packages"] = rec["packages"]
                changed = True
            if not n.get("tests"):
                n["tests"] = [{"name": "recorded run", "expect": "ok",
                               "inputs": rec.get("inputs") or {}}]
                changed = True

            recorded = rec.get("output") if isinstance(rec.get("output"), dict) else {}
            for port in n.get("outputs") or []:
                v = recorded.get(port.get("name"))
                shapes = plan_logic._TYPE_SHAPES.get(port.get("type"))
                if v is None or shapes is None:
                    continue
                bad_bool = isinstance(v, bool) and port.get("type") == "number"
                if bad_bool or not isinstance(v, shapes):
                    port["type"] = type_of_value(v) or port.get("type")
                    changed = True

            for port in n.get("outputs") or []:
                v = recorded.get(port.get("name"))
                if v is not None and derive_port_schema(port, v):
                    changed = True
            if changed:
                filled.append(n.get("name"))
        elif n.get("type") == "ai":
            rec = _cells.latest_ok_for(workflow_id, n, kind="ai")
            if rec:
                changed = False
                if not n.get("tests"):
                    n["tests"] = [{"name": "recorded example", "expect": "ok",
                                   "inputs": rec.get("inputs") or {}}]
                    changed = True

                import providers as _prov
                planned = str(n.get("model") or "").strip()
                if rec.get("model") and (not planned
                        or not _prov.is_ready({"model": planned})):
                    if planned != rec["model"]:
                        n["model"] = rec["model"]
                        changed = True

                recorded = rec.get("output") if isinstance(rec.get("output"), dict) else {}
                for port in n.get("outputs") or []:
                    v = recorded.get(port.get("name"))
                    if v is not None and derive_port_schema(port, v):
                        changed = True
                if changed:
                    filled.append(n.get("name"))
    return filled

RECORDED_PREFIX = "$recorded"

ISSUE_PREFIX = "$issue"

# The failing run's own recorded inputs, by name, so a fix proves the step on real data instead of an invented row
def issue_case_inputs(workflow: dict) -> dict:
    tid = fix_in_progress(workflow)
    if not tid:
        return {}
    t = next((t for t in workflow.get("tickets") or [] if t.get("id") == tid), None)
    if not t:
        return {}
    pool: dict = {}
    saw = t.get("saw") if isinstance(t.get("saw"), dict) else {}
    page = saw.get("page") if isinstance(saw.get("page"), dict) else {}
    for name, ref in (("page", page.get("page")),
                      ("screenshot", page.get("screenshot")),
                      ("har", saw.get("har"))):
        if isinstance(ref, str) and ref:
            pool[name] = ref
    for name, ref in (saw.get("files") or {}).items():
        if isinstance(ref, str) and ref:
            pool.setdefault(str(name), ref)
    case = None
    if t.get("case_id"):
        try:
            case = next((c for c in corpus.cases(workflow["id"], t.get("node_id") or "",
                                                 "failure")
                         if c.get("case_id") == t["case_id"]), None)
        except Exception:
            case = None

    pool.update(dict((case or {}).get("inputs") or {}))
    return pool

# The one rule for how a question card is answered, used by the ask's refusal and its tool description
ASK_NEEDS_AN_ANSWER = (
    "A question card always gives the user a way to answer on the card: options for a choice, a field for each value (even a single link, name, number or date), secret=true for a password or key, upload=true for a file, folder=true for a folder. Anything open-ended is not a card: say it as an ordinary message and end your turn, and the user answers in the chat box.")

# One resolver for chaining recorded outputs and a failing run's inputs into a cell
def resolve_recorded(k: str, v: str, recorded: dict, issue: Optional[dict] = None):
    if v == ISSUE_PREFIX or v.startswith(ISSUE_PREFIX + ":"):
        want = k if v == ISSUE_PREFIX else v[len(ISSUE_PREFIX) + 1:].strip()
        pool = issue or {}
        if want in pool:
            return pool[want], None
        have = ", ".join(f"{n!r}" for n in sorted(pool)) or "nothing"
        return None, (f"input {k!r}: the failing run kept no value named {want!r} "
                      f"(it kept: {have}). \"$issue\" works only while fixing an "
                      "issue, and offers what that run left behind: the values the step was given, plus \"page\", \"screenshot\", \"har\" and any file it stored."
                      + ("" if pool else
                         " This run kept NOTHING - say so and ask the user rather than guessing what the page held."))
    if v == RECORDED_PREFIX:
        want = k
    elif v.startswith(RECORDED_PREFIX + ":"):
        want = v[len(RECORDED_PREFIX) + 1:].strip()
    else:
        return v, None
    if want in recorded:
        return recorded[want], None
    have = ", ".join(f"{n!r}" for n in sorted(recorded)) or "nothing yet"
    return None, (f"input {k!r}: no earlier cell has recorded an output "
                  f"named {want!r} yet. Recorded output names are: {have} - "
                  f"chain one of those (\"$recorded:<name>\"), or name this "
                  "input the same as the earlier output and pass \"$recorded\" (no re-run needed either way)")

# A recorded value derives the port's nested schema; the model's declarations overlay it, never replace it
def derive_port_schema(port: dict, value) -> str | None:
    from runtime import shape as _shape
    structured = isinstance(value, dict) or (
        isinstance(value, list) and value and all(isinstance(v, dict) for v in value))
    if not structured:
        return None
    inferred = _shape.infer(value)
    if not _shape.is_structured(inferred):
        return None
    overlay = port.get("item_fields") or []
    stored = port.get("schema") if isinstance(port.get("schema"), dict) else None
    if not stored:
        base, verdict = inferred, "derived"
    elif not _shape.validate(stored, value):
        base, verdict = stored, None
    else:
        base, verdict = _shape.merge(stored, inferred), "widened"

    final = _shape.apply_item_fields(base, overlay) if overlay else base

    if isinstance(value, list) and value \
            and (final or {}).get("type") == "array":
        if port.get("may_be_empty"):
            final.pop("x-nonempty", None)
        else:
            final = {**final, "x-nonempty": True}
    if final != stored:
        port["schema"] = final
        return verdict or "annotated"
    return None

# A recorded value types both ends of a seam - the producer's output and every same-named consumer input
def derive_seam_types(workflow_id: str, plan: dict) -> list:
    from agent import cells as _cells
    by_name = {n.get("name"): n for n in plan.get("nodes") or []}
    ancestors = plan_logic.ancestors_by_name(plan)
    changed = []
    for dst in plan.get("nodes") or []:
        dname = dst.get("name")
        for p in dst.get("inputs") or []:
            nm = p.get("name")
            if not nm:
                continue

            shapes = plan_logic._TYPE_SHAPES.get(p.get("type"))
            srcs = [a for a in ancestors.get(dname, ())
                    if nm in {q.get("name")
                              for q in (by_name.get(a) or {}).get("outputs") or []}]
            if len(srcs) != 1:
                continue
            rec = _cells.latest_ok(workflow_id, srcs[0])
            recorded = (rec.get("output") if rec
                        and isinstance(rec.get("output"), dict) else {})
            v = recorded.get(nm)
            if v is None:
                continue
            bad_bool = isinstance(v, bool) and p.get("type") == "number"
            if shapes is not None and (bad_bool or not isinstance(v, shapes)):
                p["type"] = type_of_value(v) or p.get("type")
                if dname not in changed:
                    changed.append(dname)

            if derive_port_schema(p, v) and dname not in changed:
                changed.append(dname)
    return changed

# Structural equality of a plan step and its built node; any difference counts as modified
def _step_matches_node(pn: dict, node: dict) -> bool:
    if pn.get("type") != node_type_of(node):
        return False
    def ports(seq):
        return sorted((str(p.get("name")), str(p.get("type") or "")) for p in seq or [])
    if ports(pn.get("inputs")) != ports(node.get("inputs")) \
            or ports(pn.get("outputs")) != ports(node.get("outputs")):
        return False
    cfg = node.get("config", {})
    if code_key(pn.get("code")) != code_key(cfg.get("code")):
        return False
    if str(pn.get("prompt") or "").strip() != str(cfg.get("prompt") or "").strip():
        return False
    if pn.get("type") in OUTSIDE_TYPES and (
            bool(pn.get("read_only")) != bool(node.get("read_only"))
            or str(pn.get("external_impact") or "") != str(node.get("external_impact") or "")):
        return False
    def tests(seq):
        return [(t.get("inputs"), t.get("expect", "ok"), t.get("asserts") or [])
                for t in seq or []]
    return tests(pn.get("tests")) == tests(node.get("tests"))

# Normalises the changes list to one shape, then derives modified and added against the built workflow
def normalise_changes(workflow: dict, plan: dict) -> None:
    ch = plan.get("changes")
    if isinstance(ch, list):
        ch = {"modified": [str(x) for x in ch]}
    elif not isinstance(ch, dict):
        ch = {}
    else:
        ch = {k: v for k, v in ch.items() if isinstance(v, list)}

    id_name = {n.get("id"): n.get("name") for n in workflow.get("nodes") or []}
    id_name.update({pn.get("id"): pn.get("name") for pn in plan.get("nodes") or []
                    if pn.get("id")})
    ch = {k: [humanise_name(id_name.get(x, x)) if isinstance(x, str) else x
              for x in v] for k, v in ch.items()}

    def _dedupe(v):
        seen, out = set(), []
        for x in v:
            if isinstance(x, str):
                if x in seen:
                    continue
                seen.add(x)
            out.append(x)
        return out
    ch = {k: _dedupe(v) for k, v in ch.items()}
    if workflow.get("nodes"):
        by_name = {}
        for n in workflow["nodes"]:
            by_name[n.get("name")] = n
            by_name[humanise_name(n.get("name"))] = n
        by_id = {n.get("id"): n for n in workflow["nodes"]}
        mod, add = [], []
        for pn in plan.get("nodes") or []:
            b = by_id.get(pn.get("id")) or by_name.get(pn.get("name"))
            if b is None:
                add.append(pn.get("name"))
            elif not _step_matches_node(pn, b) \
                    or humanise_name(b.get("name")) != pn.get("name"):
                mod.append(pn.get("name"))
        ch.setdefault("modified", mod)
        ch.setdefault("added", add)
    if any(ch.get(k) for k in ("modified", "added", "removed")):
        plan["changes"] = ch
    else:
        plan.pop("changes", None)

# One cleaner for submitted card answers; a value too long to replay inline becomes a sample, never clipped away
def clean_ask_answers(workflow: dict, fields, raw) -> dict:
    if not (fields and isinstance(raw, dict)):
        return {}
    names = {f.get("name") for f in fields}
    out = {}

    for k, v in list(raw.items())[:8]:
        k, v = str(k)[:64], str(v or "")
        if k not in names or not v.strip():
            continue
        out[k] = v
    return out

# A new user message stands as the answer to whatever was open; the card itself is never edited
def settle_open_asks(workflow: dict, cid: str = "") -> int:
    n = 0
    for req in _transcript.open_requests(workflow):
        _transcript.append_answer(workflow, req.get("iid"),
                                  "(answered in the message above)",
                                  shown={"via_message": True}, cid=cid)
        n += 1
    return n

# A new user message answers an open opening card - nothing more
def supersede_open_plan(workflow: dict, cid: str = "") -> bool:
    card = _transcript.last_request(workflow, "blueprint")
    if not card or card.get("iid") in _transcript.answered(workflow):
        return False
    if (card.get("payload") or {}).get("head") != "plan":
        return False

    _transcript.append_answer(workflow, card["iid"],
                              "(answered in the message above)",
                              shown={"via_message": True}, cid=cid)
    return True

# After a turn-ending tool, further tool calls are refused with an explanation instead of executed
def turn_over_error(workflow: dict, tool_name: str = "") -> str:
    if turnstate.of(workflow).ask_open:
        return ("the question card is on screen - this turn is OVER. Do nothing further; the answer arrives as the user's next message.")
    if turnstate.of(workflow).built:
        return ("the workflow is built - this turn is OVER. Do nothing further; tell the user in one line what it does.")
    if turnstate.of(workflow).pending_fix:
        return ("the repair is already triggered - this turn is OVER. Do nothing further; it runs next.")
    return ""

# Counts which of the failing run's rejected rows the new schemas would now accept, so widening is approved knowingly
def contract_widened(workflow: dict, plan: dict) -> list:
    from runtime import shape as _shape
    tid = plan.get("ticket_id")
    if not tid:
        return []
    t = next((t for t in workflow.get("tickets") or [] if t.get("id") == tid), None)
    off = (t or {}).get("offending")
    if not isinstance(off, list) or not off:
        return []
    by_id = {n.get("id"): n for n in workflow.get("nodes") or []}
    step = by_id.get((t or {}).get("node_id")) or {}
    pn = next((n for n in plan.get("nodes") or []
               if n.get("id") == step.get("id")
               or (step.get("name") and n.get("name") == step.get("name"))), None)
    if not pn:
        return []
    out = []
    for o in off:
        port = next((q for q in (pn.get("outputs") or []) + (pn.get("inputs") or [])
                     if q.get("name") == o.get("port")), None)
        if not port:
            continue
        sch = _shape.from_port(port)
        item_sch = sch.get("items") if sch.get("type") == "array" else sch
        rows = o.get("rows") or []
        accepted = sum(1 for r in rows if not _shape.validate(item_sch, r.get("value")))
        if accepted:
            out.append({"step": pn.get("name"), "port": o.get("port"),
                        "accepted": accepted, "of": len(rows)})
    return out

# Which result fields are must-have and which may be missing - stated only when there is a distinction to state
def must_have_summary(workflow: dict) -> list:
    from runtime import shape as _shape
    by_id = {n.get("id"): n for n in workflow.get("nodes") or []}
    out = []
    for d in workflow.get("deliverables") or []:
        node = by_id.get(d.get("node"))
        if not node:
            continue
        port = next((p for p in node.get("outputs") or [] if p.get("name") == d.get("port")), None)
        if not port:
            continue
        sch = _shape.from_port(port)
        obj = sch.get("items") if sch.get("type") == "array" else sch
        if not isinstance(obj, dict) or obj.get("type") != "object":
            continue
        props = list((obj.get("properties") or {}).keys())
        must = [k for k in props if k in set(obj.get("required") or [])]
        rest = [k for k in props if k not in must]
        if must and rest:
            out.append({"label": d.get("label") or port.get("label") or d.get("port"),
                        "must_have": must, "may_be_missing": rest})
    return out

# One payload for both plan-shaped cards, the opening approval and the built record; a change card carries only what is new
def opening_card_payload(workflow: dict, plan: dict, head: str) -> dict:
    packages = sorted({p for n in (plan.get("nodes") or [])
                       for p in (n.get("packages") or [])})
    domains = sorted({d for n in (plan.get("nodes") or [])
                      for d in (n.get("domains") or [])})
    paths = sorted({str(x) for n in (plan.get("nodes") or [])
                    for x in (n.get("paths") or []) if str(x).strip()})
    ch = plan.get("changes") if isinstance(plan.get("changes"), dict) else {}
    if ch.get("modified") or ch.get("added") or ch.get("removed"):
        built_pkgs = {d for n in (workflow.get("nodes") or [])
                      for d in ((n.get("config") or {}).get("dependencies")
                                or [])}
        packages = sorted(set(packages) - built_pkgs)
        domains = sorted(set(domains)
                         - set(workflow.get("egress_allowlist") or []))
        built_paths = {str(x) for n in (workflow.get("nodes") or [])
                       for x in ((n.get("config") or {}).get("paths") or [])}
        paths = sorted(set(paths) - built_paths
                       - set(workflow.get("path_allowlist") or []))
    return {"head": head,
            "summary": plan.get("summary", ""),

            "steps": [{"name": n.get("name"), "type": n.get("type"),
                       "description": n.get("description", ""),
                       **({"tested": _step_test_label(workflow, n)}
                          if head == "built"
                          and _step_test_label(workflow, n) else {})}
                      for n in (plan.get("nodes") or [])],
            "changes": (changes_to_names(workflow, plan)
                        if workflow.get("nodes") else {}),
            **({"fix_note": plan["fix_note"]} if isinstance(plan.get("fix_note"), dict) else {}),
            **({"change_note": plan["change_note"]}
               if isinstance(plan.get("change_note"), dict) and workflow.get("nodes") else {}),
            "warnings": list(dict.fromkeys((plan.get("warnings") or [])
                                           + (plan.get("_done_warnings") or []))),
            "packages": packages,
            "domains": domains,
            "paths": paths,
            "must_have": plan.get("_must_have") or [],

            **({"done": DONE_LINES} if head == "built" else {}),
            **({"continue": plan["_continue"]}
               if head == "built" and isinstance(plan.get("_continue"), dict) else {}),

            **(_plan_diff_keys(workflow, plan) if head == "plan" else {})}

# The card's two lists when the steps differ from the approved ones, each only when it has something in it
def _plan_diff_keys(workflow: dict, plan: dict) -> dict:
    if not plan_steps_need_approval(workflow, plan):
        return {}
    diff = plan_step_changes(workflow.get("approved_steps") or [], plan.get("nodes") or [])
    return {"plan_changed": True,
            **({"plan_removed": diff["removed"]} if diff["removed"] else {}),
            **({"plan_added": diff["added"]} if diff["added"] else {})}

DONE_LINES = ["Done - built and checked.",
              "If something goes wrong, I'll pause the run and help you fix it."]

# One step's test status in the user's words: Tested, or the honest reason it was not
def _step_test_label(workflow: dict, n: dict) -> str:
    from agent import cells as _cells
    t = n.get("type")
    if t == "user-input":
        return ""
    nm = str(n.get("name") or "")
    rec = _cells.latest_ok_for(workflow["id"], n,
                           kind="ai" if t == "ai" else "code")
    if t == "ai":
        return "Tested" if rec else "Not tested - first try on the next run"
    code = str(n.get("code") or "").strip()
    if rec and (not code or code_key(code) == code_key(rec.get("code"))):
        return "Tested"
    if is_declined(workflow, n):
        return "Not tested - you chose to skip the test"
    if str(n.get("minor_revision") or "").strip() and rec:
        return "Not tested - a small change since its test"
    return "Not tested - first try on the next run"

# Lands a opening card exactly as rendered, never touched again
def append_plan_entry(workflow: dict, plan: dict, force_new: bool = False,
                       head: Optional[str] = None):
    head = head or "plan"
    payload = opening_card_payload(workflow, plan, head)
    last = _transcript.last_request(workflow, "blueprint")
    if not force_new and last is not None:
        same = _summary_key((last.get("payload") or {}).get("summary")) \
            == _summary_key(plan.get("summary"))
        same_head = (last.get("payload") or {}).get("head") == head
        if same and same_head:
            return None
    return _transcript.append_request(workflow, "blueprint", payload)

# Whitespace- and case-folded summary, so a cosmetic rewording is not a change
def _summary_key(text) -> str:
    return " ".join(str(text or "").split()).lower()

# The one plan-level approval, stamped by the opening card's click and never re-asked as the plan evolves
def plan_approved(workflow: dict) -> bool:
    return bool(workflow.get("plan_approved_ts"))

FIX_LIVE_STATUSES = ("open", "in-progress")

# Whether this plan changes a built workflow in a way the user has not seen yet
def change_needs_approval(workflow: dict, plan: Optional[dict] = None) -> bool:
    plan = workflow.get("plan") if plan is None else plan
    if not workflow.get("nodes") or fix_in_progress(workflow):
        return False
    if (plan or {}).get("change_approved_ts"):
        return bool(change_widened(workflow, plan or {}))
    ch = (plan or {}).get("changes")
    if not isinstance(ch, dict):
        return False
    return any(ch.get(k) for k in ("added", "modified", "removed", "renamed"))

# The step names a plan's change list declares, humanised, whatever list they sit under; a stored id reads as its step's name
def declared_change_names(workflow: dict, plan: dict) -> set:
    ch = (plan or {}).get("changes")
    if not isinstance(ch, dict):
        return set()
    id_name = {n.get("id"): n.get("name") for n in (workflow or {}).get("nodes") or []}
    id_name.update({pn.get("id"): pn.get("name") for pn in (plan or {}).get("nodes") or []
                    if pn.get("id") and pn.get("name")})
    out = set()
    for k in ("added", "modified", "removed", "renamed"):
        for x in ch.get(k) or []:
            if isinstance(x, dict):
                x = x.get("to") or x.get("from")
            if isinstance(x, str) and x.strip():
                out.add(humanise_name(id_name.get(x, x)))
    return out

# The declared steps the approved change did not name; empty when the declaration still fits
def change_widened(workflow: dict, plan: dict) -> set:
    approved = plan.get("approved_changes")
    if not isinstance(approved, list):
        return set()
    return declared_change_names(workflow, plan) - {humanise_name(str(x)) for x in approved}

DECLARE_FIRST = (
    "this would change {what}, and the user has not seen what you are changing or why. Declare it first: save_plan with change_note {{found, will_change}} (fix_note {{went_wrong, will_change}} in a fix) and changes {{modified: [step names], added: [...], removed: [...]}} naming this step, and no code yet - the card shows your declaration, the click approves that scope, and then you work the declared steps. Reading needs no permission meanwhile: read_node, read_cases, this step's own recordings, and what the failing run itself kept (\"$issue:page\", \"$issue:har\").")
CARD_WAITING = (
    "the card showing this change is waiting for the user's click - end your turn here; their answer starts the next one, and then you work the declared steps.")
OUTSIDE_SCOPE = (
    "the approved change names {scope}, and \"{name}\" is not among them - save_plan again adding it under changes (modified or added) and update the note; the card asks once more, and then you work it.")

# Why a try of a changed step is refused on a built workflow, or None when the change covers it
def change_scope_refusal(workflow: dict, name: str, what: str = "how the step works") -> Optional[str]:
    plan = workflow.get("plan") or {}
    fix = fix_in_progress(workflow)
    approved = plan.get("fix_approved_ts") if fix else plan.get("change_approved_ts")
    step = humanise_name(str(name or ""))
    if approved:
        scope = {humanise_name(str(x)) for x in plan.get("approved_changes") or [] if str(x).strip()}
        if scope and step not in scope:
            return OUTSIDE_SCOPE.format(scope=", ".join(f'"{x}"' for x in sorted(scope)), name=step)
        return None
    if declared_change_names(workflow, plan):
        return CARD_WAITING
    return DECLARE_FIRST.format(what=what)

# The steps the approved plan had and this plan lacks, and the steps this plan adds, matched by type in order; names play no part
def plan_step_changes(approved: list, current: list) -> dict:
    a = [str((x or {}).get("type") or "") for x in approved or []]
    b = [str((x or {}).get("type") or "") for x in current or []]
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) - 1, -1, -1):
        for j in range(len(b) - 1, -1, -1):
            table[i][j] = table[i + 1][j + 1] + 1 if a[i] == b[j] \
                else max(table[i + 1][j], table[i][j + 1])
    kept_a, kept_b, i, j = set(), set(), 0, 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            kept_a.add(i); kept_b.add(j); i += 1; j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1

    def _row(x):
        return {"name": str((x or {}).get("name") or ""), "type": str((x or {}).get("type") or "")}
    return {"removed": [_row(x) for k, x in enumerate(approved or []) if k not in kept_a],
            "added": [_row(x) for k, x in enumerate(current or []) if k not in kept_b]}

# The approved steps as they are stored at the opening card's click
def approved_steps_of(plan: dict) -> list:
    return [{"name": str(n.get("name") or ""), "type": str(n.get("type") or "")}
            for n in (plan or {}).get("nodes") or []]

# Whether a plan not yet built has changed its steps since the user approved them
def plan_steps_need_approval(workflow: dict, plan: Optional[dict] = None) -> bool:
    plan = workflow.get("plan") if plan is None else plan
    if workflow.get("nodes") or fix_in_progress(workflow) or not plan_approved(workflow):
        return False
    approved = workflow.get("approved_steps")
    if approved is None:
        return False
    diff = plan_step_changes(approved, (plan or {}).get("nodes") or [])
    return bool(diff["removed"] or diff["added"])

# Whether an issue fix is underway, read from the plan and the open issue, not from the turn that started it
def fix_in_progress(workflow: dict) -> str:
    if turnstate.of(workflow).fix_issue_id:
        return str(turnstate.of(workflow).fix_issue_id)
    tid = str((workflow.get("plan") or {}).get("ticket_id") or "")
    if not tid:
        return ""
    t = next((t for t in workflow.get("tickets") or [] if t.get("id") == tid), None)
    if t is not None and t.get("status") in FIX_LIVE_STATUSES:
        return tid
    return ""

# An amend save carries only changed steps; everything else carries forward, so nothing can silently vanish
def merge_amend(prev: dict, patch: dict) -> dict:
    merged = dict(prev)
    merged.pop("status", None)
    merged.pop("validation", None)
    merged.pop("superseded", None)

    prev_ch = merged.pop("changes", None)
    prev_removed = [x for x in ((prev_ch or {}).get("removed") or [])
                    if isinstance(prev_ch, dict) and isinstance(x, str)]
    patch_nodes = list(patch.get("nodes") or [])
    for n in patch_nodes:
        if n.get("name"):
            n["name"] = humanise_name(n["name"])
    out_nodes = []
    used: list = []
    for pn in prev.get("nodes") or []:
        hit = next((n for n in patch_nodes
                    if (n.get("id") and n.get("id") == pn.get("id"))
                    or (n.get("name") and n.get("name")
                        == humanise_name(pn.get("name")))), None)
        if hit is not None:
            used.append(hit)

            out_nodes.append({**dict(pn), **hit})
        else:
            out_nodes.append(dict(pn))
    for n in patch_nodes:
        if not any(n is u for u in used):
            out_nodes.append(n)
    ch = patch.get("changes") if isinstance(patch.get("changes"), dict) else {}

    def _ref(x):
        return str((x.get("id") or x.get("name")) if isinstance(x, dict) else x)

    listed = {str(n.get("id")) for n in patch_nodes if n.get("id")} \
        | {humanise_name(str(n.get("name") or "")) for n in patch_nodes if n.get("name")}
    removed_refs = {_ref(x) for x in (ch.get("removed") or [])}
    removed = {r for r in removed_refs | {humanise_name(r) for r in removed_refs}
               if r not in listed}
    if removed:
        out_nodes = [n for n in out_nodes
                     if humanise_name(str(n.get("name") or "")) not in removed
                     and str(n.get("id") or "") not in removed]
    merged["nodes"] = out_nodes

    if patch.get("edges"):
        merged["edges"] = patch["edges"]
    if str(patch.get("summary") or "").strip():
        merged["summary"] = patch["summary"]
    present = {humanise_name(str(n.get("name") or "")) for n in out_nodes} \
        | {str(n.get("id")) for n in out_nodes if n.get("id")}
    still_gone = [x for x in prev_removed
                  if humanise_name(x) not in present and x not in present]
    if still_gone:
        ch = {**ch, "removed": list(ch.get("removed") or []) + still_gone}
    if ch:
        merged["changes"] = ch
    for k, v in patch.items():
        if k not in ("nodes", "edges", "summary", "changes", "amend"):
            merged[k] = v
    return merged

_EDGE_ARROW = re.compile(r"^\s*(.+?)\s*(?:->|=>|>|→)\s*(.+?)\s*$")

# Coerces string-shaped plan entries into the expected dict shapes; what cannot be read comes back as a refusal
def normalise_plan_shapes(plan: dict) -> list[str]:
    errs: list[str] = []
    nodes = plan.get("nodes")
    if nodes is not None and not isinstance(nodes, list):
        return ["`nodes` must be a list of steps"]
    edges = plan.get("edges")
    if edges is not None and not isinstance(edges, list):
        return ["`edges` must be a list of {src, dst}"]
    step_names = {str(n.get("name") or "") for n in (nodes or []) if isinstance(n, dict)}

    def _end(v):
        v = str(v or "")
        if v not in step_names and "." in v:
            head = v.rsplit(".", 1)[0]
            if head in step_names:
                return head
        return v

    fixed_edges = []
    for e in edges or []:
        if isinstance(e, str):
            m = _EDGE_ARROW.match(e)
            if not m:
                errs.append(f"edge {e!r}: write it as {{\"src\": ..., \"dst\": ...}}")
                continue
            fixed_edges.append({"src": _end(m.group(1)), "dst": _end(m.group(2))})
        elif isinstance(e, dict):
            e = dict(e)
            for k in ("src", "dst"):
                if isinstance(e.get(k), str):
                    e[k] = _end(e[k])
            fixed_edges.append(e)
        else:
            errs.append(f"edge {e!r}: write it as {{\"src\": ..., \"dst\": ...}}")
    if edges is not None:
        plan["edges"] = fixed_edges
    for n in nodes or []:
        if not isinstance(n, dict):
            errs.append(f"step {n!r}: a step is an object with at least a name and type")
            continue
        nm = n.get("name") or "?"
        for side in ("inputs", "outputs"):
            ports = n.get(side)
            if ports is None:
                continue
            if not isinstance(ports, list):
                errs.append(f'step "{nm}": {side} must be a list')
                continue
            out = []
            for p in ports:
                if isinstance(p, str):
                    out.append({"name": p})
                elif isinstance(p, dict):
                    if isinstance(p.get("item_fields"), list):
                        p["item_fields"] = [
                            {"name": f} if isinstance(f, str) else f
                            for f in p["item_fields"]]
                    out.append(p)
                else:
                    errs.append(f'step "{nm}": {side} entry {p!r} is not a port '
                                "({name, type, ...})")
            n[side] = out
        crit = n.get("criteria")
        if isinstance(crit, list):
            kept = []
            for c in crit:
                c = {"expr": c} if isinstance(c, str) else c

                if isinstance(c, dict) and not str(c.get("expr") or "").strip():
                    continue
                kept.append(c)
            n["criteria"] = kept
        tests = n.get("tests")
        if isinstance(tests, list) and any(not isinstance(t, dict) for t in tests):
            errs.append(f'step "{nm}": a test is an object {{name, inputs, expect}} - '
                        "or leave `tests` out and the recorded run fills it")
        for key in ("packages", "domains"):
            if isinstance(n.get(key), str):
                n[key] = [x.strip() for x in re.split(r"[,\s]+", n[key]) if x.strip()]
    return errs

# A secret pasted into chat is scrubbed from the transcript and logs once stored; short values are left alone
def redact_everywhere(workflow: dict, name: str, value: str) -> None:
    from agent import transcript as _tr, chatlog as _cl
    from storage import secrets_store as _ss
    if not value or len(value) < _ss.SCRUB_MIN_LEN:
        return
    _tr.redact_value(workflow, value, f"(secret {name})")
    _cl.redact(workflow.get("id") or "", value, f"(secret {name})")
    from agent import trail as _trail
    _trail.redact(workflow.get("id") or "", value, f"(secret {name})")

# Each step's single progress state: planned, validated, coded or finished
def step_states(workflow: dict) -> dict:
    built = {n.get("name") for n in workflow.get("nodes") or []}
    plan = workflow.get("plan") or {}
    gapped = bool(plan.get("design_gaps"))
    out: dict = {}
    for n in plan.get("nodes") or []:
        nm, t = str(n.get("name") or ""), n.get("type")
        if not nm:
            continue
        if nm in built:
            out[nm] = "finished"
        elif t == "user-input" or str(n.get("code") or "").strip() \
                or _has_recorded_code(workflow["id"], nm, t):
            out[nm] = "coded"
        else:
            out[nm] = "planned" if gapped else "validated"
    return out

def _has_recorded_code(workflow_id: str, name: str, ntype: str) -> bool:
    from agent import cells as _cells
    return bool(_cells.latest_ok(workflow_id, name,
                                 kind="ai" if ntype == "ai" else "code"))

CHANGED_MARK = " (its code changed since it last ran)"

RENAME_MATCH_MIN = 120

# New or changed code steps with neither working code nor a recorded run; a step matching its built node is settled and skipped
def untested_steps(workflow: dict, nodes: list) -> list:
    from agent import cells as _cells
    by_name = {n.get("name"): n for n in workflow.get("nodes") or []}
    declined = declined_steps(workflow)

    failing = ""
    fix_tid = fix_in_progress(workflow)
    if fix_tid:
        _t = next((t for t in workflow.get("tickets") or []
                   if t.get("id") == fix_tid), None)
        _n0 = next((n for n in workflow.get("nodes") or []
                    if n.get("id") == (_t or {}).get("node_id")), None)
        failing = humanise_name(str((_n0 or {}).get("name") or ""))
    out = []
    for n in nodes or []:
        t = n.get("type")
        if t not in CODE_TYPES:
            continue
        built = by_name.get(n.get("name"))
        if built is not None and _step_matches_node(n, built):
            continue
        if is_declined(workflow, n):
            continue
        code = str(n.get("code") or "").strip()
        rec = _cells.latest_ok_for(workflow["id"], n,
                               kind="code")

        if not rec and (t in OUTSIDE_TYPES or not code):
            out.append(n.get("name"))
        elif code and rec and code_key(code) != code_key(rec.get("code")):
            if str(n.get("minor_revision") or "").strip() \
                    and str(n.get("name") or "") != failing:
                continue
            out.append(f"{n.get('name')}{CHANGED_MARK}")
    return out

# Steps the user declined to test for real, read from the recorded answers - never a flag the model sets
def declined_steps(workflow: dict) -> set:
    answers = {i.get("to"): i for i in _transcript.items(workflow)
               if i.get("kind") == "answer"}
    latest: dict = {}
    for item in _transcript.items(workflow):
        if item.get("kind") != "request" or item.get("request") != "approval":
            continue
        pl = item.get("payload") or {}
        ans = answers.get(item.get("iid"))
        if ans is None or ans.get("shown") not in ("deny", "allow", "always"):
            continue
        for ref in (str(pl.get("step") or ""), str(pl.get("step_id") or "")):
            if ref:
                latest[ref] = ans.get("shown")
    return {ref for ref, decision in latest.items() if decision == "deny"}

# Wires every declared plan edge whose ends exist - selection by the harness, never judgement
def auto_wire_plan_edges(workflow: dict, plan: dict) -> list:
    canonicalise_refs(workflow)
    wired = []
    for e in plan.get("edges") or []:
        a = find_node(workflow, str(e.get("src") or ""))
        b = find_node(workflow, str(e.get("dst") or ""))
        if not a or not b:
            continue
        if any(x.get("src") == a["id"] and x.get("dst") == b["id"]
               for x in workflow.get("edges", [])):
            continue
        r = _connect_one(workflow, a["id"], b["id"], str(e.get("when") or ""))
        if r.get("ok"):
            wired.append(f'{a.get("name")} -> {b.get("name")}')
    return wired

# Options become plain label strings whatever shape was sent, so a card never shows [object Object]
def option_labels(options: Optional[list]) -> list[str]:
    out = []
    for o in options or []:
        if isinstance(o, dict):
            out.append(str(o.get("label") or o.get("name") or o.get("title")
                           or o.get("value") or json.dumps(o, default=str)))
        else:
            out.append(str(o))
    return out

# Validates a setup card's fields; secrets, uploads and folder picks keep their own dedicated cards
def clean_ask_fields(fields, secret=False, upload=False,
                     folder=False) -> tuple[list, str]:
    if not fields:
        return [], ""
    if secret or upload or folder:
        return [], ("fields never combine with secret/upload/folder - those keep their own card; ask for them separately, right after the setup card")
    if not isinstance(fields, list) or not all(isinstance(f, dict)
                                              for f in fields):
        return [], "fields must be a list of {name, label?, type?, options?}"
    clean, seen = [], set()
    for f in fields:
        raw = str(f.get("name") or f.get("label") or "").strip()
        name = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_",
                                         raw.lower())).strip("_")
        if not name:
            return [], "every field needs a name"
        if name in seen:
            return [], f"two fields share the name {name!r}"
        seen.add(name)
        ftype = str(f.get("type") or "").strip().lower()
        if ftype not in ("text", "choice", "number", "date", "boolean"):
            ftype = "choice" if f.get("options") else "text"
        opts = option_labels(f.get("options"))
        if ftype == "choice" and not opts:
            return [], f"field {name!r}: a choice field needs options"
        clean.append({"name": name,
                      "label": str(f.get("label") or "").strip()
                      or humanise_name(raw),
                      "type": ftype,
                      **({"options": opts} if opts else {}),
                      **({"optional": True} if f.get("optional") else {})})
    return clean, ""

# The blocked host from an egress refusal, so approval can be offered the moment a cell hits the wall
def egress_block_domain(error_text: str) -> Optional[str]:
    m = re.search(r"egress blocked: \(?'([^']+)'", str(error_text or ""))
    return m.group(1) if m else None

# The refused path from a disk-guard message, or None
def path_block_path(error_text: str) -> Optional[str]:
    m = re.search(r"path blocked: '([^']+)'", str(error_text or ""))
    return m.group(1) if m else None

# The folder a blocked path belongs to: the path itself when it is a folder, else its parent
def folder_of(path: str) -> str:
    import os as _os
    p = _os.path.abspath(_os.path.expanduser(str(path)))
    return p if _os.path.isdir(p) else _os.path.dirname(p)

# Step-work is refused until the user can see at least a skeleton plan on the canvas
def skeleton_first(workflow: dict) -> Optional[str]:
    fresh = store.load(workflow["id"]) or workflow
    if fresh.get("nodes"):
        return None
    if not (fresh.get("plan") or {}).get("nodes"):
        return ("nothing is on the canvas yet - the user must see the planned workflow before you work its steps. Call save_intent (one line) and save_plan with a SKELETON (step names + types only) FIRST, then run this again; refine the plan as you learn.")

    if not plan_approved(fresh) and not plan_approved(workflow):
        return ("the user hasn't approved the plan yet - their reply at the opening card is feedback, not a go-ahead. Fold it in and save_plan again (amend:true): the updated plan goes back to them with the button, and step-work starts once they click it.")
    return None

_CELL_PROGRESS: dict = {}

def set_cell_progress(workflow_id: str, cb) -> None:
    if cb is None:
        _CELL_PROGRESS.pop(workflow_id, None)
    else:
        _CELL_PROGRESS[workflow_id] = cb

def cell_progress(workflow_id: str):
    return _CELL_PROGRESS.get(workflow_id)

# Polled while a cell runs, so Stop frees the slot in about a second
def turns_should_stop(workflow: dict) -> bool:
    import turns
    return turns.should_stop(workflow.get("id") or "")

_SHAPE_ASK_UNDER = 5

# Asks the one judgement a recording cannot make: which fields the record is meaningless without
def shape_question(output) -> Optional[dict]:
    from runtime import shape as _shape
    if not isinstance(output, dict):
        return None
    ask = {}
    for k, v in output.items():
        items = v if isinstance(v, list) else None
        if items is not None and (not items or not all(isinstance(i, dict) for i in items)):
            continue
        if items is None and not isinstance(v, dict):
            continue
        sch = _shape.infer(v)
        if not _shape.is_structured(sch):
            continue
        obj = sch["items"] if sch.get("type") == "array" else sch
        seen = list(obj.get("x-expected") or []) + [
            f for f in (obj.get("properties") or {}) if f not in (obj.get("x-expected") or [])]
        n = len(items) if items is not None else 1
        if seen and n < _SHAPE_ASK_UNDER:
            ask[k] = {"fields_seen": seen, "seen_items": n}
    if not ask:
        return None
    return {"derived": True,
            "note": ("the nested shape of these outputs is DERIVED from this run and enforced on every later run - you never write it, and it ACCEPTS ABSENCE by default: a row missing a field goes through thinner (only a field gone from every row is flagged). ONE call is yours: which fields is a record MEANINGLESS without (a name, an id, the thing the row is about)? Mark exactly those `required: true` in that output's `item_fields` in save_plan (for a list: per item; for a record: its fields), naming ONLY the fields you are marking - a row missing one of them is then a shape problem the run stops on (or sets aside, per the step's on_invalid_items). Leave everything else unmarked. Enums are never inferred: declare `options` where a field is a fixed choice. Say the outcome to the user once, right before you build, ONLY when some fields are must-have and others are not."),
            "outputs": ask}
