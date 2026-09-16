# The five step types: where each one is defined, and the lookups every other part of the app uses
from __future__ import annotations

from types import ModuleType
from typing import Optional

from models import step_type
from step_types import ai, browser, code, connector, user_input

_MODULES: tuple[ModuleType, ...] = (user_input, code, connector, browser, ai)

TYPES: tuple[str, ...] = tuple(m.TYPE for m in _MODULES)

CAPABILITIES = ("code", "network", "app", "browser", "model", "process")

_BY_NAME: dict[str, ModuleType] = {m.TYPE: m for m in _MODULES}

# The module that defines a type, or None for a type that does not exist
def module(node_type) -> Optional[ModuleType]:
    return _BY_NAME.get(str(getattr(node_type, "value", node_type) or ""))

# What a type is allowed to reach; an unknown type is allowed nothing
def may(node_type: str, capability: str) -> bool:
    m = module(node_type)
    return bool(m and m.MAY.get(capability, False))

# One of a type's own rules by name, or None when the type has no such rule
def function(node_type: str, name: str):
    m = module(node_type)
    f = getattr(m, name, None) if m is not None else None
    return f if callable(f) else None

# A type's build contract: its re-proof, its evidence kind, its completeness bar and what it may reach
def contract(node_type: str) -> dict:
    m = module(node_type)
    if m is None:
        return {}
    return {"receipt": m.RECEIPT, "evidence": m.EVIDENCE, "bar": m.BAR,
            "may": dict(m.MAY)}

CODE_TYPES: tuple[str, ...] = tuple(t for t in TYPES if may(t, "code"))

OUTSIDE_TYPES: tuple[str, ...] = tuple(
    t for t in TYPES if may(t, "app") or may(t, "network") or may(t, "browser"))

# Does this step touch a system outside the workflow?
def reaches_outside(node) -> bool:
    return step_type(node) in OUTSIDE_TYPES

# Does finishing this step change something outside? A missing read_only counts as a write
def writes_outside(node) -> bool:
    if not reaches_outside(node):
        return False
    ro = (node or {}).get("read_only", False) if isinstance(node, dict) \
        else getattr(node, "read_only", False)
    return not ro

# Does this step run authored code?
def carries_code(node) -> bool:
    return step_type(node) in CODE_TYPES
