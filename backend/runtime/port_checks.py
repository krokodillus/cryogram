# Type-shape checks derived from declared ports at run time - no coercion; a mismatch is a plain problem sentence
from __future__ import annotations

from typing import Any

from storage import blobstore
from runtime import shape
from runtime import verifier

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
        s = v if len(v) <= 40 else v[:37] + "..."
        return f"text ({s!r})"
    if isinstance(v, bytes):
        return f"binary data ({len(v)} bytes)"
    if isinstance(v, dict):
        return f"a record with fields {sorted(v.keys())[:6]}"
    if isinstance(v, list):
        return f"a list of {len(v)} items"
    return f"a {type(v).__name__}"

_TYPE_WORD = {"text": "text", "longtext": "text", "number": "a number",
              "boolean": "yes or no", "date": "a date", "file": "a file",
              "folder": "a folder", "record": "a record",
              "enum": "one of its allowed choices"}

# The label the builder wrote, else the key made readable - users never see keys
def field_name(port: dict) -> str:
    label = str(port.get("label") or "").strip()
    if label:
        return label
    return str(port.get("name") or "").replace("_", " ").replace("-", " ").strip() \
        .capitalize() or "A value"

def _problem(port: dict, detail: str) -> dict:
    t = port.get("type") or ""
    return {"port": port["name"], "type": t,
            "problem": f"{field_name(port)} should be "
                       f"{_TYPE_WORD.get(t, t)}, but the run {detail}"}

NAMED_ITEMS = 3

# One reasoned sentence for a nested-shape miss, leading with the count
def _structured_problem(port: dict, sch: dict, value: Any, found: list) -> dict:
    fn = field_name(port)
    if isinstance(value, list) and (sch or {}).get("type") == "array":
        bad = shape.failing_items(sch, value)
        first = found[0]["problem"]
        named = ", ".join(str(i + 1) for i in bad[:NAMED_ITEMS])
        more = f" and {len(bad) - NAMED_ITEMS} more" if len(bad) > NAMED_ITEMS else ""
        sentence = (f"{fn}: {len(bad)} of {len(value)} items do not fit the "
                    f"expected shape (items {named}{more}) - first problem: "
                    f"{first}")
        return {"port": port["name"], "type": port.get("type") or "",
                "problem": sentence, "failing_items": bad,
                "detail": found[:NAMED_ITEMS * 2]}
    return {"port": port["name"], "type": port.get("type") or "",
            "problem": f"{fn}: {found[0]['problem']}"
                       + (f" (and {len(found) - 1} more)" if len(found) > 1 else ""),
            "detail": found[:NAMED_ITEMS * 2]}

# Checks values against the declared ports and returns problems as plain sentences with exact positions
def check_ports(ports: list, values: Any, require_all: bool = False) -> list[dict]:
    problems: list[dict] = []
    if not isinstance(values, dict):
        if ports:
            p = ports[0]
            problems.append(_problem(p, f"received {_describe(values)} instead of "
                                        "values keyed by port name"))
        return problems
    for p in ports or []:
        t, name = p.get("type") or "", p["name"]
        items = p.get("item_fields") or []
        sch = shape.from_port(p)

        if t in ("secret", "") and not items and not shape.is_structured(sch):
            continue
        if name not in values:
            if require_all and not p.get("optional"):
                problems.append({"port": name, "type": t,
                                 "problem": f"{field_name(p)} was not produced "
                                            "by this step"})
            continue
        v = values[name]
        if p.get("optional") and v in (None, ""):
            continue
        if items or shape.is_structured(sch):
            if require_all and sch.get("x-nonempty") \
                    and isinstance(v, list) and not v:
                problems.append({"port": name, "type": t,
                                 "empty_output": True,
                                 "problem": f"{field_name(p)} came back "
                                            "completely empty - every recorded run had items"})
                continue

            found = shape.validate(sch, v)
            if found:
                problems.append(_structured_problem(p, sch, v, found))

            gone = shape.vanished_fields(sch, v) if require_all else []
            if gone:
                names = ", ".join(repr(g) for g in gone)
                problems.append({"port": name, "type": t,
                                 "problem": f"{field_name(p)}: the field"
                                            f"{'s' if len(gone) > 1 else ''} {names} "
                                            f"{'are' if len(gone) > 1 else 'is'} missing "
                                            f"from every one of the {len(v)} items - "
                                            "the source seems to have changed shape",
                                 "detail": [{"path": g, "problem": f"{g} is missing "
                                             "from every item"} for g in gone]})
            continue
        if t in ("text", "longtext", "folder"):
            if not isinstance(v, str):
                problems.append(_problem(p, f"received {_describe(v)}"))
            elif v == "" and require_all:
                problems.append(_problem(p, "produced it empty - an optional value may be empty, a required one may not"))
        elif t == "number":
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                problems.append(_problem(p, f"received {_describe(v)}"))
        elif t == "boolean":
            if not isinstance(v, bool):
                problems.append(_problem(p, f"received {_describe(v)}"))
        elif t == "date":
            if not verifier.is_date(v):
                problems.append(_problem(p, f"received {_describe(v)} - a date "
                                            "looks like 2026-01-31"))
        elif t == "enum":
            opts = [str(o) for o in (p.get("options") or [])]
            if str(v) not in opts:
                problems.append(_problem(p, f"received {_describe(v)} - the "
                                            f"choices are {opts}"))
        elif t == "file":
            if isinstance(v, str) and v.startswith("blob:"):
                if not blobstore.exists(v):
                    problems.append(_problem(p, "received a file that is no longer stored"))
            elif not isinstance(v, str):
                problems.append(_problem(p, f"received {_describe(v)} - expected "
                                            "an uploaded file or its text"))
        elif t == "record":
            if not isinstance(v, dict):
                problems.append(_problem(p, f"received {_describe(v)}"))
        elif t == "list":
            if not isinstance(v, list):
                problems.append(_problem(p, f"received {_describe(v)}"))
    return problems

# The problems as the user-facing sentences the popups show
def problem_lines(problems: list[dict]) -> list[str]:
    return [p["problem"] for p in problems]
