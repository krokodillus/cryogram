# The data model: a workflow is a Workflow holding typed Nodes, Edges and Variables. Data only, no logic
from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

# The five step types; a step is exactly one of these, never mixed
class NodeType(str, Enum):
    CODE = "code"
    CONNECTOR = "connector"
    BROWSER = "browser"
    AI = "ai"
    INPUT = "user-input"

# A step's type as a plain string, whether stored as the enum or the string
def step_type(node) -> str:
    t = (node or {}).get("type") if isinstance(node, dict) else getattr(node, "type", "")
    return t.value if hasattr(t, "value") else str(t or "")

# The staleness key: trailing whitespace and blank lines stripped, so a cosmetic re-type never invalidates a recorded run
def code_key(code) -> str:
    lines = [ln.rstrip() for ln in str(code or "").splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)

class Hardness(str, Enum):
    HARD = "hard"
    SOFT = "soft"

def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

# A step's id, minted once at its first save and kept for its whole life - the built node carries the same id
def new_step_id() -> str:
    return _id("node")

def _now() -> float:
    return time.time()

# A check written at build time that runs on a step's output on every run
@dataclass
class Criterion:
    expr: str
    field: Optional[str] = None
    hardness: Hardness = Hardness.SOFT
    justification: str = ""

# A recorded example for a step: inputs and what is expected back
@dataclass
class Test:
    name: str
    inputs: dict = field(default_factory=dict)
    expect: str = "ok"
    asserts: list[str] = field(default_factory=list)

# An AI step always runs one named model at a set temperature
@dataclass
class ModelRef:
    model: str = ""
    temperature: float = 0.0

    provider_id: str = ""

# Everything proven as one unit; changing any part means the step must be proven again
@dataclass
class ConfigBundle:
    prompt: str = ""
    model: ModelRef = field(default_factory=ModelRef)
    code: str = ""
    dependency_lock: str = ""
    criteria: list[Criterion] = field(default_factory=list)

# A port's declared type drives the run form's widget, the AI step's enforced output schema and validation
@dataclass
class Port:
    name: str
    type: str = ""
    label: str = ""
    options: list[str] = field(default_factory=list)

# One step of a workflow
@dataclass
class Node:
    id: str = field(default_factory=lambda: _id("node"))
    name: str = ""
    description: str = ""

    intention: str = ""
    type: NodeType = NodeType.AI
    config: ConfigBundle = field(default_factory=ConfigBundle)
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    lineage: Optional[dict] = None
    # A connector that is not explicitly read-only always asks the user before it runs - write vs read is a fact, not a guess
    read_only: bool = False

    external_impact: str = ""

    approval_suppressed: bool = False
    stats: dict = field(default_factory=dict)

    tests: list[Test] = field(default_factory=list)

# A secret variable carries only its name here - the value lives encrypted in the secret store
@dataclass
class Variable:
    name: str
    label: str = ""
    value: Optional[str] = None
    secret: bool = False

    persistent: bool = True

    kind: str = "config"

# The conversation record is append-only: what the user was shown is never rewritten after the fact
@dataclass
class TranscriptItem:
    seq: int
    kind: str
    at: float = field(default_factory=_now)

# An edge orders two steps and may carry a condition; a step with no live incoming edge is skipped
@dataclass
class Edge:
    src: str
    dst: str
    count: int = 0

    when: str = ""

# A named set of variables shared across workflows, such as one per external tool
@dataclass
class Environment:
    id: str = field(default_factory=lambda: _id("env"))
    name: str = ""
    description: str = ""
    group: str = ""
    variables: list[Variable] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

# A workflow and everything recorded about it; "workflow" is the internal name for a workflow
@dataclass
class Workflow:
    id: str = field(default_factory=lambda: _id("wfl"))
    name: str = ""
    description: str = ""
    group: str = ""

    environment_ids: list = field(default_factory=list)

    env_bindings: dict = field(default_factory=dict)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)

    transcript: list[TranscriptItem] = field(default_factory=list)
    transcript_seq: int = 0

    samples: list = field(default_factory=list)

    plan: Optional[dict] = None

    tickets: list = field(default_factory=list)

    intent: Optional[dict] = None

    deliverables: list = field(default_factory=list)

    egress_allowlist: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

_IDENT_NAME = re.compile(r"^[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)+$")

# Turns an identifier-style step name into the plain label the user sees; human names pass through untouched
def humanise_name(name) -> str:
    s = str(name or "").strip()
    if re.fullmatch(r"[a-z0-9]+(?:[A-Z][a-z0-9]*)+", s):
        words = re.findall(r"[a-z0-9]+|[A-Z]+(?![a-z])|[A-Z][a-z0-9]*", s)
        out = " ".join(w.lower() for w in words)
        return out[:1].upper() + out[1:]
    if not _IDENT_NAME.match(s):
        return s
    out = " ".join(re.split(r"[_-]+", s))
    return out[:1].upper() + out[1:]

# Edges store node ids; names are only a lookup key, and any stored name self-heals here
def canonicalise_refs(workflow: dict) -> int:
    by_name = {n.get("name"): n["id"] for n in workflow.get("nodes", [])}
    ids = {n["id"] for n in workflow.get("nodes", [])}
    fixed = 0
    for e in workflow.get("edges", []):
        for k in ("src", "dst"):
            v = e.get(k)
            if v not in ids and v in by_name:
                e[k] = by_name[v]
                fixed += 1
    return fixed
