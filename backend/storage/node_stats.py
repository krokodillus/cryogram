# Per-step run statistics, computed from the run record on read, never stored on the step
from __future__ import annotations

import providers
from step_types import CODE_TYPES
from runtime import corpus
from storage import settings, store

_CHECK_KEYS = frozenset((
    "precondition", "standard_input_check", "standard_output_check",
    "hard_failures"))

_CHECK_CAUSES = ("check", "precondition", "contract")

MIN_RUNS_FOR_RATE = 3

# checks or error; unknown shapes count as errors - we know it failed, not that a check caught it
def classify_failure(case: dict) -> str:
    cause = case.get("cause") or ""
    if cause:
        if cause.startswith("node threw"):
            return "error"
        if any(w in cause for w in _CHECK_CAUSES):
            return "checks"
    if _CHECK_KEYS.intersection(case.get("verdict_keys") or []):
        return "checks"
    return "error"

# A step's run counts, computed from the recorded runs of its current version
def for_node(workflow_id: str, node: dict) -> dict:
    counts = corpus.node_counts(workflow_id, node["id"],
                               cfg_hash=corpus.config_hash(node))
    successes = counts["successes"]
    failures = counts["failures"]
    runs = successes + len(failures)
    ntype = node.get("type")
    ntype = ntype.value if hasattr(ntype, "value") else str(ntype)

    stats: dict = {"runs": runs, "successes": successes,
                   "failure_count": len(failures)}
    if ntype in ("ai", *CODE_TYPES):
        stats["success_rate"] = (successes / runs) if runs >= MIN_RUNS_FOR_RATE else None
    if ntype in CODE_TYPES:
        kinds = [classify_failure(f) for f in failures]
        stats["failed_checks"] = kinds.count("checks")
        stats["failed_errors"] = kinds.count("error")
    return stats

# Stats are computed onto the nodes at read time, never persisted
def attach(workflow_id: str, workflow: dict) -> None:
    for node in workflow.get("nodes", []):
        node["stats"] = for_node(workflow_id, node)

# Counts which workflows and steps depend on each provider, for the admin cards
def provider_usage() -> dict[str, dict]:
    regs = settings.get().get("providers", [])
    owner_by_model: dict[str, str] = {}
    for p in regs:
        if p.get("use") == "builder" or providers.auth_type(p) in providers.BUILDER_ONLY_AUTHS:
            continue
        for m in p.get("models", []):
            name = m.get("name")
            if name and name not in owner_by_model:
                owner_by_model[name] = p.get("id", "")
    usage = {p["id"]: {"workflows": 0, "nodes": 0}
             for p in regs if p.get("id") and p.get("use") != "builder"}
    for meta in store.list_workflows():
        if meta.get("unreadable"):
            continue
        workflow = store.load_ro(meta.get("id", "")) or {}
        hit: set[str] = set()
        for node in workflow.get("nodes", []):
            if node.get("type") != "ai":
                continue
            model = ((node.get("config") or {}).get("model") or {}).get("model", "")
            owner = owner_by_model.get(model, "")
            if owner in usage:
                usage[owner]["nodes"] += 1
                hit.add(owner)
        for owner in hit:
            usage[owner]["workflows"] += 1
    return usage
