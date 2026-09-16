# One JSON-schema subset per port is the data contract; problems report as human-readable paths
from __future__ import annotations

import json
from typing import Any

from runtime import verifier

PORT_TYPE_SCHEMA: dict[str, dict] = {
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "date": {"type": "string", "format": "date"},
    "record": {"type": "object"},
    "list": {"type": "array"},
    "text": {"type": "string"},
    "longtext": {"type": "string"},
    "folder": {"type": "string"},
    "enum": {"type": "string"},
    "file": {"type": "string", "x-file": True},
    "secret": {"type": "string", "x-secret": True},
}

INFER_ITEMS_CAP = 200

PROBLEMS_CAP = 50

VALUE_PREVIEW_CHARS = 40

# Reads a recorded value and writes the schema it fits; enums are never guessed
def infer(value: Any) -> dict:
    if value is None:
        return {"x-nullable": True}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string", "format": "date"} if verifier.is_date(value) \
            else {"type": "string"}
    if isinstance(value, dict):
        props = {str(k): infer(value[k]) for k in sorted(value, key=str)}
        return {"type": "object", "properties": props,
                "required": [], "x-expected": sorted(props.keys())}
    if isinstance(value, (list, tuple)):
        items: dict | None = None
        for v in list(value)[:INFER_ITEMS_CAP]:
            items = infer(v) if items is None else merge(items, infer(v))
        out: dict = {"type": "array"}
        if items is not None:
            out["items"] = items
        return out
    return {}

# Combines two schemas; where they disagree on a type the result allows anything rather than guessing
def merge(a: dict, b: dict) -> dict:
    if "type" not in a or "type" not in b:
        if "type" in b:
            return _nullable(b) if a.get("x-nullable") else {}
        if "type" in a:
            return _nullable(a) if b.get("x-nullable") else {}

        return {"x-nullable": True} \
            if (a.get("x-nullable") and b.get("x-nullable")) else {}
    ta, tb = a["type"], b["type"]
    if ta != tb:
        return {}
    if ta == "object":
        pa, pb = a.get("properties") or {}, b.get("properties") or {}
        props: dict = {}
        for k in sorted(set(pa) | set(pb)):
            if k in pa and k in pb:
                props[k] = merge(pa[k], pb[k])
            else:
                props[k] = dict(pa.get(k) or pb.get(k) or {})
        req = sorted(set(a.get("required") or []) & set(b.get("required") or []))
        exp = sorted(set(a.get("x-expected") or []) & set(b.get("x-expected") or []))
        out: dict = {"type": "object", "properties": props, "required": req}
        if exp:
            out["x-expected"] = exp
        return _keep_nullable(out, a, b)
    if ta == "array":
        out = {"type": "array"}
        if "items" in a and "items" in b:
            out["items"] = merge(a["items"], b["items"])
        elif "items" in a or "items" in b:
            out["items"] = dict(a.get("items") or b.get("items") or {})
        return _keep_nullable(out, a, b)
    if ta == "string":
        out = {"type": "string"}
        if a.get("format") == "date" and b.get("format") == "date":
            out["format"] = "date"
        for m in ("x-file", "x-secret"):
            if a.get(m) and b.get(m):
                out[m] = True
        return _keep_nullable(out, a, b)
    return _keep_nullable({"type": ta}, a, b)

def _nullable(s: dict) -> dict:
    out = dict(s)
    out["x-nullable"] = True
    return out

def _keep_nullable(out: dict, a: dict, b: dict) -> dict:
    if a.get("x-nullable") or b.get("x-nullable"):
        out["x-nullable"] = True
    return out

# A short, safe description of what actually arrived
def _describe(v: Any) -> str:
    if v is None:
        return "nothing (null)"
    if isinstance(v, bool):
        return f"a boolean ({v})"
    if isinstance(v, (int, float)):
        return f"a number ({v})"
    if isinstance(v, str):
        if v == "":
            return "empty text"
        s = v if len(v) <= VALUE_PREVIEW_CHARS else v[:VALUE_PREVIEW_CHARS - 3] + "..."
        return f"text ({s!r})"
    if isinstance(v, bytes):
        return f"binary data ({len(v)} bytes)"
    if isinstance(v, dict):
        return f"a record with fields {sorted(str(k) for k in v)[:6]}"
    if isinstance(v, list):
        return f"a list of {len(v)} items"
    return f"a {type(v).__name__}"

_TYPE_WORD = {"string": "text", "number": "a number", "boolean": "yes or no",
              "object": "a record", "array": "a list", "null": "nothing"}

# Checks a value against a schema and reports problems with readable paths such as "item 7 > author > name"
def validate(schema: dict, value: Any, path: str = "",
             problems: list | None = None, allow_empty: bool = False) -> list[dict]:
    out = problems if problems is not None else []
    if len(out) >= PROBLEMS_CAP:
        return out
    if not schema or schema.get("x-secret") or "type" not in schema:
        return out
    t = schema.get("type")
    here = path or "the value"
    if value is None:
        if schema.get("x-nullable") or allow_empty:
            return out
        out.append({"path": path, "problem": f"{here} is missing (nothing arrived)"})
        return out
    if schema.get("enum") is not None:
        opts = [str(o) for o in schema["enum"]]
        if str(value) not in opts:
            out.append({"path": path, "problem": f"{here} is {_describe(value)} - "
                                                 f"the allowed values are {opts}"})
        return out
    if t == "object":
        if not isinstance(value, dict):
            out.append({"path": path, "problem": f"{here} should be a record, "
                                                 f"but {_describe(value)} arrived"})
            return out
        props = schema.get("properties") or {}
        req = set(schema.get("required") or [])
        for k in schema.get("required") or []:
            if k not in value:
                out.append({"path": _join(path, k),
                            "problem": f"{_join(path, k)} is missing"})
                if len(out) >= PROBLEMS_CAP:
                    return out
        for k, sub in props.items():
            if k in value:
                validate(sub, value[k], _join(path, k), out,
                         allow_empty=(k not in req))
                if len(out) >= PROBLEMS_CAP:
                    return out
        return out
    if t == "array":
        if not isinstance(value, list):
            out.append({"path": path, "problem": f"{here} should be a list, "
                                                 f"but {_describe(value)} arrived"})
            return out
        items = schema.get("items")
        if items:
            for i, it in enumerate(value):
                validate(items, it, _item_path(path, i), out)
                if len(out) >= PROBLEMS_CAP:
                    return out
        return out
    if t == "string":
        if not isinstance(value, str):
            out.append({"path": path, "problem": f"{here} should be text, but "
                                                 f"{_describe(value)} arrived"})
            return out
        if value == "" and not allow_empty:
            out.append({"path": path, "problem": f"{here} is empty text"})
            return out
        if schema.get("format") == "date" and value != "" and not verifier.is_date(value):
            out.append({"path": path, "problem": f"{here} should be a date "
                                                 f"(YYYY-MM-DD), but {_describe(value)} "
                                                 "arrived"})
        return out
    if t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            out.append({"path": path, "problem": f"{here} should be a number, but "
                                                 f"{_describe(value)} arrived"})
        return out
    if t == "boolean":
        if not isinstance(value, bool):
            out.append({"path": path, "problem": f"{here} should be yes or no, but "
                                                 f"{_describe(value)} arrived"})
        return out
    return out

# Paths read as a person says them: item 2 > author > name
def _join(path: str, key: str) -> str:
    return f"{path} > {key}" if path else str(key)

def _item_path(path: str, i: int) -> str:
    return f"{path} > item {i + 1}" if path else f"item {i + 1}"

VANISH_MIN_ITEMS = 5

# Flags a field that every earlier run had but no row carries now - the source-changed-shape signal
def vanished_fields(schema: dict, value: Any) -> list[str]:
    items = (schema or {}).get("items") or {}
    exp = items.get("x-expected") or []
    if not exp or not isinstance(value, list) or len(value) < VANISH_MIN_ITEMS:
        return []
    rows = [v for v in value if isinstance(v, dict)]
    if len(rows) < VANISH_MIN_ITEMS:
        return []
    return [k for k in exp if all(k not in r for r in rows)]

# Every failing item's index, uncapped - indices are cheap
def failing_items(schema: dict, value: list) -> list[int]:
    items = (schema or {}).get("items")
    if not items or not isinstance(value, list):
        return []
    return [i for i, it in enumerate(value) if validate(items, it)]

# The one way a port becomes a schema: a stored schema wins, else the declared type and fields
def from_port(port: dict) -> dict:
    if isinstance(port.get("schema"), dict) and port["schema"]:
        return port["schema"]
    if port.get("item_fields"):
        props, req, exp = {}, [], []
        for f in port["item_fields"]:
            if not isinstance(f, dict) or not f.get("name"):
                continue
            props[f["name"]] = from_port(f)
            if f.get("required"):
                req.append(f["name"])
            elif not f.get("optional"):
                exp.append(f["name"])
        obj = {"type": "object", "properties": props, "required": req}
        if exp:
            obj["x-expected"] = exp

        if str(port.get("type") or "") == "record":
            return obj
        return {"type": "array", "items": obj}
    t = str(port.get("type") or "")
    if t == "enum":
        frag: dict = {"type": "string"}
        if port.get("options"):
            frag["enum"] = [str(o) for o in port["options"]]
        return frag
    return dict(PORT_TYPE_SCHEMA.get(t, {}))

# The declared per-item fields refine the recorded schema, never replace it
def apply_item_fields(schema: dict, item_fields: list) -> dict:
    if not item_fields or not schema:
        return schema
    out = json.loads(json.dumps(schema))
    obj = out.get("items") if out.get("type") == "array" else out
    if not isinstance(obj, dict) or obj.get("type") != "object":
        return schema
    props = obj.setdefault("properties", {})
    req = set(obj.get("required") or [])
    exp = set(obj.get("x-expected") or [])
    for f in item_fields:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        name = f["name"]
        cur = props.get(name)
        if not isinstance(cur, dict):
            cur = from_port(f)
            props[name] = cur
            if not f.get("optional") and not f.get("required"):
                exp.add(name)
        else:
            if f.get("options"):
                cur["enum"] = [str(o) for o in f["options"]]
                cur["type"] = "string"
            elif "type" not in cur and f.get("type"):
                cur.update(from_port(f))

        if f.get("required"):
            req.add(name)
            exp.discard(name)
        elif f.get("optional"):
            req.discard(name)
            exp.discard(name)
    obj["required"] = sorted(req)
    if exp:
        obj["x-expected"] = sorted(exp)
    else:
        obj.pop("x-expected", None)
    return out

def port_type_of(schema: dict) -> str:
    if not schema:
        return ""
    if schema.get("enum") is not None:
        return "enum"
    t = schema.get("type")
    if t == "string":
        if schema.get("x-secret"):
            return "secret"
        if schema.get("x-file"):
            return "file"
        return "date" if schema.get("format") == "date" else "text"
    return {"number": "number", "boolean": "boolean", "object": "record",
            "array": "list"}.get(t, "")

# Whether the schema says anything about insides; flat scalars have their own gate
def is_structured(schema: dict) -> bool:
    t = (schema or {}).get("type")
    return (t == "object" and bool(schema.get("properties"))) \
        or (t == "array" and bool(schema.get("items")))
