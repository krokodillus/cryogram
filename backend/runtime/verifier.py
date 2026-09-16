# Deterministic verification - code, not models: a step's criteria run on every output, every run
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass
class VerifyResult:
    ok: bool = True
    hard_failures: list[str] = field(default_factory=list)
    soft_flags: list[str] = field(default_factory=list)
    escalate: bool = False

def is_positive(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0

def is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)

def non_empty(v: Any) -> bool:
    return v is not None and (len(v) > 0 if hasattr(v, "__len__") else True)

def in_enum(v: Any, options) -> bool:
    return v in options

def in_range(v: Any, lo: float, hi: float) -> bool:
    return is_number(v) and lo <= v <= hi

def matches(v: Any, pattern: str) -> bool:
    return isinstance(v, str) and re.fullmatch(pattern, v) is not None

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

def is_date(v: Any) -> bool:
    return isinstance(v, str) and _DATE.fullmatch(v.strip()) is not None

# The quoted span must actually occur in the source; a hallucinated value has no real span
def source_exists(span: Any, source_text: Any) -> bool:
    return isinstance(span, str) and isinstance(source_text, str) and span.strip() in source_text

_LIBRARY: dict[str, Any] = {
    fn.__name__: fn for fn in
    (is_positive, is_number, non_empty, in_enum, in_range, matches, is_date, source_exists)
}

_SAFE_BUILTINS: dict[str, Any] = {
    name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    for name in ("abs", "len", "min", "max", "sum", "round", "sorted", "all", "any",
                 "set", "dict", "list", "tuple", "range", "bool", "int", "float", "str", "divmod")
}

# The one table of functions a check may call - the gate refuses any other name at save, the run evaluates with exactly these
FUNCTIONS: dict[str, Any] = {**_SAFE_BUILTINS, **_LIBRARY}

# Reads a check field from dict or dataclass alike
def _get(c, key: str, default=None):
    return c.get(key, default) if isinstance(c, dict) else getattr(c, key, default)

def _is_hard(c) -> bool:
    h = _get(c, "hardness", "soft")
    return str(getattr(h, "value", h)) == "hard"

# Runs through the closed-grammar interpreter, never eval; could-not-verify counts as failure
def _evaluate_expr(expr: str, scope: dict) -> tuple[bool, Optional[str]]:
    from runtime import safe_eval
    try:
        return bool(safe_eval.evaluate(expr, scope, FUNCTIONS)), None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

def _label(c) -> str:
    lab = str(_get(c, "label") or "").strip()
    if lab:
        return lab
    fld = _get(c, "field")
    return f"[{fld}] {_get(c, 'expr')}" if fld else str(_get(c, "expr"))

# Runs a step's checks against its output; a failed hard check stops the value from moving on
def evaluate(output: dict, criteria: list, inputs: Optional[dict] = None) -> VerifyResult:
    scope: dict[str, Any] = {**(inputs or {}), **(output or {})}
    result = VerifyResult()
    for c in criteria or []:
        passed, err = _evaluate_expr(_get(c, "expr"), scope)
        if err is not None:
            result.hard_failures.append(f"{_label(c)} -> {err}")
            result.escalate = True
        elif passed:
            continue
        elif _is_hard(c):
            result.hard_failures.append(_label(c))
            result.escalate = True
        else:
            result.soft_flags.append(_label(c))
    result.ok = not result.hard_failures
    return result
