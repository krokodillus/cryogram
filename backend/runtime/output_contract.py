# Normalizes outputs consistently without mistaking an explicit output map for a raw record
from __future__ import annotations

from typing import Any

def normalize(ports: list, raw: Any, *, named: bool = False) -> Any:
    if named:
        return raw
    if len(ports) == 1:
        name = ports[0]["name"]
        if isinstance(raw, dict) and name in raw:
            return raw
        return {name: raw}
    if isinstance(raw, dict):
        return raw
    return {p["name"]: raw for p in ports}
