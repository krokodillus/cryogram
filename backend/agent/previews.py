# A tool result rides whole under the engine's own cut; past it the model gets the result's outline and opens the parts it needs
from __future__ import annotations

import json
import os
from typing import Any

INLINE_CHARS = {"claude": 60_000, "codex": 30_000}

_probe = os.environ.get("CRYOGRAM_INLINE_CHARS", "").strip()
if _probe.isdigit():
    INLINE_CHARS = {k: int(_probe) for k in INLINE_CHARS}

OUTLINE_STRING_CHARS = 200

OUTLINE_LIST_CHARS = 1_000

WHOLE_VALUE_NOTE = ("this result was larger than the turn can hold inline, so it comes back as its outline: every key, each list as its count and first item, long text cut with its total. The whole value is kept: open any part of it with read_earlier_result (this result's id, a path such as data.output.rows[3], find=\"text\" to keep only matching entries), or chain a step's recording into a cell with \"$recorded:<name>\"")

# The size of a value as the model would read it
def size(v: Any) -> int:
    try:
        return len(json.dumps(v, default=str))
    except (TypeError, ValueError):
        return len(str(v))

# The characters a result may carry inline on this engine
def inline_limit(engine: str = "") -> int:
    return INLINE_CHARS.get(str(engine or "").lower(), min(INLINE_CHARS.values()))

def _shape_of(v: Any) -> Any:
    from runtime import shape
    try:
        return shape.infer(v)
    except Exception:
        return {"type": type(v).__name__}

def _outline(v: Any) -> Any:
    if isinstance(v, str):
        if len(v) <= OUTLINE_STRING_CHARS:
            return v
        return (v[:OUTLINE_STRING_CHARS]
                + f" [... {len(v):,} characters in total]")
    if isinstance(v, list):
        if size(v) <= OUTLINE_LIST_CHARS:
            return [_outline(x) for x in v]
        return {"$list": len(v), "shape": _shape_of(v),
                "first": _outline(v[0]) if v else None,
                "note": f"{len(v):,} items in total"}
    if isinstance(v, dict):
        return {k: _outline(x) for k, x in v.items()}
    return v

# A result rides whole under the engine's limit; past it, its outline with a note saying how to open the whole
def guard(v: Any, engine: str = "") -> Any:
    if size(v) <= inline_limit(engine):
        return v
    out = _outline(v)
    if isinstance(out, dict):
        return {**out, "window_note": WHOLE_VALUE_NOTE}
    return {"value": out, "window_note": WHOLE_VALUE_NOTE}

# The compact stand-in stored beside an offloaded recording: its outline
def sketch(v: Any) -> Any:
    return _outline(v) if size(v) > OUTLINE_LIST_CHARS else v
