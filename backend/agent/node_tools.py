# The only way the builder changes a workflow - every mutation is one of these structured, validated tools
import copy
import json
import re
import time
from dataclasses import asdict
from typing import Any, Optional
from storage import blobstore
import config
from runtime import corpus
from runtime import port_checks as _ports
from runtime import sandbox
from storage import deliverables
from storage import deps
from storage import environments
from runtime import executor
from agent import gate
from agent import plan_logic
import providers
from storage import secrets_store
from agent import skills
from agent import transcript as _transcript
from agent import turnstate
from storage import store
from models import Node, NodeType
import step_types
from step_types import CODE_TYPES, OUTSIDE_TYPES
from agent import steps
from models import canonicalise_refs, humanise_name
from models import code_key
from agent.steps import (
    CHANGED_MARK,
    DOMAIN,
    EXPECT,
    FIX_LIVE_STATUSES,
    ISSUE_PREFIX,
    PORT_TYPES,
    UNTYPED_PORTS,
    untyped_ports,
    PORT_TYPE_NAMES,
    RECORDED_PREFIX,
    RENAME_MATCH_MIN,
    _EDGE_ARROW,
    _HARDNESS,
    _SHAPE_ASK_UNDER,
    _TYPE_SYNONYMS,
    _connect_one,
    _has_recorded_code,
    _same_json,
    _state,
    _step_matches_node,
    _step_test_label,
    _summary_key,
    _would_cycle,
    append_plan_entry,
    assert_problems,
    auto_wire_plan_edges,
    canon_port_type,
    canon_file_kind,
    port_file_kinds,
    seed_file_kinds,
    canon_ports,
    cell_progress,
    change_needs_approval,
    changes_to_ids,
    changes_to_names,
    clean_ask_answers,
    clean_ask_fields,
    clean_criteria,
    contract_widened,
    declined_steps,
    derive_port_schema,
    derive_seam_types,
    drop_recorded_duplicates,
    egress_block_domain,
    expr_ok,
    fill_from_cells,
    find_node,
    fix_in_progress,
    folder_of,
    fresh_samples,
    is_declined,
    issue_case_inputs,
    merge_amend,
    must_have_summary,
    node_type_of,
    normalise_changes,
    normalise_plan_shapes,
    opening_card_payload,
    option_labels,
    path_block_path,
    plan_approved,
    redact_everywhere,
    resolve_recorded,
    save,
    set_cell_progress,
    set_prompt_model,
    settle_open_asks,
    shape_question,
    skeleton_first,
    step_by_ref,
    step_id_for,
    step_states,
    supersede_open_plan,
    turn_over_error,
    turns_should_stop,
    unify_field_labels,
    untested_steps, ASK_NEEDS_AN_ANSWER)

PORT_FIELDS = {"name", "type", "label", "options", "description", "optional",
               "item_fields", "value_field", "schema", "may_be_empty", "file_kind", "file_kinds"}

# Names the nodes still short of the structural bar, cheap enough to ride every validate result
def _not_ready(workflow: dict) -> list:
    out = []
    for n in workflow.get("nodes", []):
        nt, cfg = node_type_of(n), n.get("config", {})
        ready = step_types.function(nt, "readiness")
        if ready:
            ok = all(ready(n).values())
        elif nt == "ai":
            ok = (bool(n.get("outputs")) and bool(cfg.get("prompt"))
                  and bool((cfg.get("model") or {}).get("model"))
                  and bool(n.get("tests")))
        else:
            ok = (bool(n.get("outputs")) and bool(cfg.get("code"))
                  and bool(n.get("tests")))
        if not ok:
            out.append(n.get("name") or n["id"])
    return out

def tool_list_nodes(workflow: dict) -> dict:
    return {"nodes": [
        {"id": n["id"], "name": n.get("name"), "type": node_type_of(n),
         "intention": n.get("intention", ""),
         "inputs": [p["name"] for p in n.get("inputs", [])],
         "outputs": [p["name"] for p in n.get("outputs", [])],
         "tests": len(n.get("tests", [])), "criteria": len(n.get("config", {}).get("criteria", []))}
        for n in workflow["nodes"]],
        "edges": [{"src": e["src"], "dst": e["dst"], **({"when": e["when"]} if e.get("when") else {})}
                  for e in workflow.get("edges", [])]}

_READ_NODE_DROP = ("lineage", "stats",
                   "status", "provenance", "preconditions", "deopt_to",
                   "accuracy", "anomaly_fields", "capabilities",
                   "clean_run_streak", "clean_runs_to_offer", "thresholds")

# Tests come back as shape - name, expectation, input names - with values marked omitted rather than silently dropped
def _summarise_tests(tests) -> list:
    out = []
    for t in tests or []:
        if not isinstance(t, dict):
            continue
        keys = sorted((t.get("inputs") or {}).keys())
        out.append({"name": t.get("name"), "expect": t.get("expect"),
                    "input_names": keys,
                    "note": "recorded values omitted here - the step's own recording holds them"})
    return out

# An earlier tool result exactly as it came back, so a later turn opens it instead of doing the work again
def tool_read_earlier_result(workflow: dict, id: str = "", ids: Optional[list] = None,
                             path: str = "", find: str = "", offset: int = 0,
                             limit: int = 0) -> dict:
    from agent import trail as _trail
    wanted = [str(i) for i in (ids or []) if str(i or "").strip()] or ([str(id)] if str(id or "").strip() else [])
    if not wanted:
        return {"error": "pass the id from the working memory's result line (id=\"56\"), or ids=[...]"}
    found, missing = [], []
    for i in wanted:
        r = _trail.result_of(workflow.get("id") or "", i.lstrip("#"))
        if not r:
            missing.append(i)
            continue
        if path or find or offset or limit:
            r = _result_part(r, path, find, offset, limit)
        found.append(r)
    if not found:
        return {"error": f"no earlier result with id {', '.join(missing)}"}
    return {"ok": True, "results": found, **({"not_found": missing} if missing else {})}

def _walk_path(value, path: str):
    steps = [t for t in re.split(r"\.|(?=\[)", str(path or "").strip()) if t]
    here, walked = value, []
    for tok in steps:
        m = re.fullmatch(r"\[(-?\d+)\]", tok)
        if m:
            if not isinstance(here, list):
                return None, f"{'.'.join(walked) or 'the result'} is not a list, so [{m.group(1)}] does not apply"
            n = int(m.group(1))
            if not -len(here) <= n < len(here):
                return None, f"{'.'.join(walked) or 'the result'} has {len(here)} items, so [{n}] is out of range"
            here = here[n]
        else:
            if not isinstance(here, dict):
                return None, f"{'.'.join(walked) or 'the result'} is not an object, so .{tok} does not apply"
            if tok not in here:
                return None, (f"no key {tok!r} at {'.'.join(walked) or 'the result'}; "
                              f"it has: {', '.join(str(k) for k in here)}")
            here = here[tok]
        walked.append(tok)
    return here, None

def _result_part(r: dict, path: str, find: str, offset: int, limit: int) -> dict:
    value, err = _walk_path(r.get("result"), path)
    if err:
        return {"id": r.get("id"), "tool": r.get("tool"), "error": err}
    out = {"id": r.get("id"), "tool": r.get("tool"), "path": path or ""}
    needle = str(find or "").lower()
    if needle:
        if isinstance(value, list):
            kept = [x for x in value if needle in json.dumps(x, default=str).lower()]
            out["matched"] = f"{len(kept)} of {len(value)} entries contain {find!r}"
            value = kept
        elif isinstance(value, dict):
            kept = {k: v for k, v in value.items()
                    if needle in json.dumps(v, default=str).lower() or needle in str(k).lower()}
            out["matched"] = f"{len(kept)} of {len(value)} keys contain {find!r}"
            value = kept
        else:
            lines = str(value).splitlines()
            kept = [f"line {n}: {ln}" for n, ln in enumerate(lines, 1) if needle in ln.lower()]
            out["matched"] = f"{len(kept)} of {len(lines)} lines contain {find!r}"
            value = kept
    off, lim = max(int(offset or 0), 0), max(int(limit or 0), 0)
    if (off or lim) and isinstance(value, (list, str)):
        total = len(value)
        end = off + lim if lim else total
        value = value[off:end]
        out["slice"] = f"{'items' if isinstance(value, list) else 'characters'} {off}-{min(end, total)} of {total}"
    out["value"] = value
    return out

# Reads a built step back, whole - its code is never summarised
def tool_read_node(workflow: dict, node_id: str = "",
                   node_ids: Optional[list] = None) -> dict:
    ids = [str(i) for i in (node_ids or []) if str(i or "").strip()]
    if ids:
        nodes = [tool_read_node(workflow, i) for i in ids]
        return {"ok": True, "nodes": nodes}
    if not str(node_id or "").strip():
        return {"error": "name the step to read: node_id=\"<name or id>\", or node_ids=[...] for several at once"}
    node = find_node(workflow, node_id)
    if not node:
        planned = steps.step_by_ref(workflow, None, node_id)
        if planned is None:
            return {"error": f"no node {node_id!r}; " + steps.steps_that_exist(workflow)}
        return {"ok": True, "planned": True,
                "node": {k: v for k, v in planned.items() if k not in _READ_NODE_DROP},
                "note": "this step is planned and not built yet; its plan entry is shown"}
    out = {k: v for k, v in node.items() if k not in _READ_NODE_DROP}
    for side in ("inputs", "outputs"):
        if node.get(side):
            out[side] = [{k: v for k, v in p.items() if k in PORT_FIELDS}
                         for p in node[side]]
    if node.get("tests"):
        out["tests"] = _summarise_tests(node["tests"])
    return out

def _create_node(workflow: dict, name: str, type: str, description: str = "",
                    external_impact: str = "", intention: str = "",
                    read_only: bool = False, node_id: str = "") -> dict:
    if not str(name or "").strip():
        return {"error": "a node needs a NAME - the plain label the user sees on the canvas (a nameless node renders as 'a step')"}
    name = humanise_name(name)

    if find_node(workflow, name):
        return {"error": f"a step named {name!r} already exists - update or "
                         "delete that step instead of creating a second one (steps are identified by name)"}
    if type not in PORT_TYPE_NAMES:
        return {"error": f"type must be one of {sorted(PORT_TYPE_NAMES)}, not {type!r} "
                         "(a node is exactly one type, never mixed)"}
    if type in OUTSIDE_TYPES and not read_only and not external_impact.strip():
        return {"error": "a connector or browser step that writes must declare external_impact - a plain-language description of what it changes outside the workflow, for the user to approve"}
    node = Node(name=name, type=NodeType(type), description=description,
                intention=intention.strip())
    d = asdict(node)
    d["type"] = node.type.value
    if node_id and not any(n.get("id") == node_id for n in workflow["nodes"]):
        d["id"] = node_id
    if type in OUTSIDE_TYPES:
        d["read_only"] = bool(read_only)
        d["external_impact"] = external_impact
    workflow["nodes"].append(d)
    save(workflow)
    return {"ok": True, "node_id": d["id"], "state": _state(d)}

# Removes the node, its edges and any deliverable that referenced it; other steps are untouched
def delete_node(workflow: dict, node_id: str) -> dict:
    node = find_node(workflow, node_id)
    if not node:
        return {"error": f"no node {node_id!r}"}
    name = node.get("name")
    workflow["nodes"] = [n for n in workflow.get("nodes", []) if n["id"] != node_id]
    workflow["edges"] = [e for e in workflow.get("edges", [])
                        if e.get("src") != node_id and e.get("dst") != node_id]
    workflow["deliverables"] = [d for d in workflow.get("deliverables", [])
                               if d.get("node") not in (node_id, name)]
    save(workflow)
    return {"ok": True, "removed": node_id}

def _cellmod_for_ports():
    from agent import cells as _c
    return _c

def _set_declared_io(workflow: dict, node_id: str,
                        inputs: list[dict], outputs: list[dict]) -> dict:
    node = find_node(workflow, node_id)
    if not node:
        return {"error": f"no node {node_id!r}"}
    errors = []
    for kind, ports in (("input", inputs), ("output", outputs)):
        canon_ports(ports)
        for i, p in enumerate(ports or []):
            if "name" not in p:
                errors.append(f"{kind} {i}: needs a 'name' "
                              "(Port shape: name/type/label/options)")
                continue
            t = p.get("type") or ""
            if not t:
                errors.append(f"{kind} {p['name']!r} has no type yet: " + UNTYPED_PORTS)
            elif t not in PORT_TYPES:
                errors.append(f"{kind} {p['name']!r}: type {t!r} is not in the vocabulary "
                              f"{sorted(PORT_TYPES)} - the type drives the run form widget "
                              "and the enforced AI output schema, so it must be concrete")
            if t == "enum" and not p.get("options"):
                errors.append(f"{kind} {p['name']!r}: an enum port must list its options")
            for j, f in enumerate(p.get("item_fields") or []):
                if not isinstance(f, dict) or not f.get("name"):
                    errors.append(f"{kind} {p.get('name')!r} item_fields[{j}]: "
                                  "each needs a name (and type + description)")
                elif (f.get("type") or "") not in PORT_TYPES:
                    errors.append(f"{kind} {p.get('name')!r} item field "
                                  f"{f['name']!r}: type {f.get('type')!r} not in "
                                  f"{sorted(PORT_TYPES)}")
    if errors:
        return {"ok": False, "errors": errors}
    def clean(p):
        out = {"name": p["name"], "type": p.get("type") or "",
               "label": p.get("label", ""),
               "options": [str(o) for o in (p.get("options") or [])]}

        if p.get("optional"):
            out["optional"] = True
        if p.get("value_field"):
            out["value_field"] = str(p["value_field"])
        if out["type"] == "file":
            kinds = port_file_kinds(p)
            if kinds:
                out["file_kinds"] = kinds

        if str(p.get("description") or "").strip():
            out["description"] = str(p["description"]).strip()
        if p.get("item_fields"):
            out["item_fields"] = [
                {"name": f["name"], "type": f.get("type") or "",
                 **({"description": str(f["description"]).strip()}
                    if str(f.get("description") or "").strip() else {}),
                 **({"options": [str(o) for o in f["options"]]}
                    if f.get("options") else {}),
                 **({"optional": True} if f.get("optional") else {})}
                for f in p["item_fields"]
                if isinstance(f, dict) and f.get("name")]

        if isinstance(p.get("schema"), dict) and p["schema"]:
            out["schema"] = p["schema"]
        return out
    node["inputs"] = [clean(p) for p in (inputs or [])]
    node["outputs"] = [clean(p) for p in (outputs or [])]

    rec = _cellmod_for_ports().latest_ok_for(workflow["id"], node)
    if rec and steps.seed_file_kinds(workflow, node.get("name") or "", rec.get("inputs") or {}):
        pass
    steps.refresh_sends_file(node)
    save(workflow)
    warnings: list = []
    port_check = step_types.function(node_type_of(node), "port_warnings")
    if port_check:
        warnings.extend(port_check(node))
    return {"ok": True, "state": _state(node),
            **({"warnings": warnings} if warnings else {})}

def _set_code(workflow: dict, node_id: str, code: str) -> dict:
    node = find_node(workflow, node_id)
    if not node:
        return {"error": f"no node {node_id!r}"}
    if node_type_of(node) not in CODE_TYPES:
        return {"error": f"set_code only applies to code/connector nodes, not {node_type_of(node)}"}
    declared = [p["name"] for p in node.get("inputs", [])]
    secret_ports = [p["name"] for p in node.get("inputs", [])
                    if p.get("type") == "secret"]

    from agent import cells as _cells
    rec = _cells.latest_ok_for(workflow["id"], node,
                           kind="code")
    proven = bool(rec) and code_key(rec.get("code")) == code_key(code)
    violations = (([] if proven else
                   gate.check(code, declared) + gate.check_fake(code, declared)
                   + gate.check_undefined_names(code))
                  + gate.check_secret_ports(code, secret_ports)
                  + gate.check_secret_value_use(code, secret_ports)
                  + gate.check_detached_browser(code))
    if violations:
        return {"ok": False, "violations": violations}
    node["config"]["code"] = code

    save(workflow)
    return {"ok": True, "advisories": gate.advisories(code), "state": _state(node)}

# Installs a node's packages into the bundle venv and records exact pins; model-client packages are refused - intelligence goes through ai_call
def _declare_dependencies(workflow: dict, node_id: str, packages: list,
                              installer=None) -> dict:
    node = find_node(workflow, node_id)
    if not node:
        return {"error": f"no node {node_id!r}"}
    if node_type_of(node) not in CODE_TYPES:
        return {"error": "dependencies apply to code/connector nodes only"}
    packages = [str(p).strip() for p in (packages or []) if str(p).strip()]
    if not packages:
        return {"error": "declare at least one package, e.g. [\"pypdf\"]"}
    errors = deps.validate(packages)
    if errors:
        return {"ok": False, "errors": errors}
    res = (installer or deps.install)(workflow["id"], packages)
    if not res.get("ok"):
        return {"error": res.get("error", "install failed")}
    node["config"]["dependencies"] = sorted(set(packages))
    node["config"]["dependency_lock"] = "\n".join(res.get("pins", []))
    save(workflow)
    return {"ok": True, "pins": res.get("pins", [])}

# The step's always-on output checks, evaluated on every run
def _set_criteria(workflow: dict, node_id: str, criteria: list[dict]) -> dict:
    node = find_node(workflow, node_id)
    if not node:
        return {"error": f"no node {node_id!r}"}
    clean, errors = clean_criteria(criteria, "criterion")
    if errors:
        return {"ok": False, "errors": errors}
    node["config"]["criteria"] = clean
    save(workflow)
    return {"ok": True}

def _set_tests(workflow: dict, node_id: str, tests: list[dict]) -> dict:
    node = find_node(workflow, node_id)
    if not node:
        return {"error": f"no node {node_id!r}"}
    errors = []
    clean = []
    for i, t in enumerate(tests or []):
        if not t.get("name"):
            t = {**t, "name": f"test {i + 1}"}
        if t.get("expect", "ok") not in EXPECT:
            errors.append(f"test {i}: expect must be ok|reject")
        errors += [f"test {i}: {p}" for p in assert_problems(t.get("asserts"))]

        for k, v in (t.get("inputs") or {}).items():
            if isinstance(v, str) and v.startswith("blob:") and not blobstore.exists(v):
                errors.append(f"test {i}: input {k!r} references a file that does "
                              "not exist - use a REAL uploaded sample's ref from list_samples, never an invented one")
        clean.append({"name": t.get("name", f"test_{i}"), "inputs": t.get("inputs", {}),
                      "expect": t.get("expect", "ok"), "asserts": t.get("asserts", []) or []})
    if errors:
        return {"ok": False, "errors": errors}
    node["tests"] = clean

    save(workflow)
    return {"ok": True, "state": _state(node)}

# Wires two steps, optionally with a condition; connecting an identical edge twice is not an error
def connect_nodes(workflow: dict, src: str = "", dst: str = "", when: str = "",
                 edges: Optional[list] = None) -> dict:
    canonicalise_refs(workflow)
    if edges:
        results, ok = {}, True
        for e in edges:
            r = _connect_one(workflow, str(e.get("src") or ""), str(e.get("dst") or ""),
                             str(e.get("when") or ""))
            ok = ok and not r.get("error")
            results[f"{e.get('src')} -> {e.get('dst')}"] = r
        return {"ok": ok, "edges": results}
    return _connect_one(workflow, src, dst, when)

# Runs one node under a temp id so test runs never pollute its real run history
def _run_slice(workflow: dict, node: dict, sample_input: dict) -> dict:
    tmp = copy.deepcopy(node)
    tmp_id = f"tmp_{node['id']}"
    tmp["id"] = tmp_id
    sl = {"id": f"slice_{tmp_id}", "corpus_workflow_id": workflow["id"],
          "nodes": [tmp], "edges": []}
    try:
        res = executor.run_workflow(sl, dict(sample_input or {}))
    finally:
        _cleanup_tmp(workflow['id'], tmp_id)
    return res

def _cleanup_tmp(workflow_id: str, tmp_id: str) -> None:
    corpus.purge_node(workflow_id, tmp_id)

# Checks a step against the completeness bar for its type; passing is what marks it done on the canvas
def validate_node(workflow: dict, node_id: str = "",
                       node_ids: Optional[list] = None) -> dict:
    if node_ids:
        results = {nid: validate_node(workflow, nid) for nid in node_ids}
        return {"ok": all(r.get("ok") for r in results.values()), "nodes": results,
                "not_ready": _not_ready(workflow)}
    node = find_node(workflow, node_id)
    if not node:
        return {"error": f"no node {node_id!r}"}
    nt = node_type_of(node)
    cfg = node.get("config", {})
    if nt == "ai":
        impl = bool(cfg.get("prompt"))
    else:
        impl = bool(cfg.get("code"))

    ready = step_types.function(nt, "readiness")
    if ready:
        gating = ready(node)
        return {"ok": all(gating.values()), "checklist": gating,
                "missing": [k for k, v in gating.items() if not v],
                "advisory": [], "not_ready": _not_ready(workflow)}
    if nt == "ai":
        gating = {"declared_outputs": bool(node.get("outputs")),
                  "implementation": impl,
                  "tests_authored": bool(node.get("tests"))}
        advisory = ([] if cfg.get("criteria") else ["criteria"]) \
            + ([] if cfg.get("model", {}).get("model") else ["model"])
        return {"ok": all(gating.values()),
                "checklist": {**gating, "criteria": bool(cfg.get("criteria")),
                              "model": bool(cfg.get("model", {}).get("model"))},
                "missing": [k for k, v in gating.items() if not v],
                "advisory": advisory,
                "not_ready": _not_ready(workflow)}

    real = True
    if nt in CODE_TYPES and cfg.get("code"):
        from agent import cells as _cells
        rec = _cells.latest_ok_for(workflow["id"], node,
                               kind="code")
        if not (rec and code_key(rec.get("code")) == code_key(cfg["code"])):
            real = not gate.check_fake(
                cfg["code"], [p["name"] for p in node.get("inputs", [])])

    if nt in OUTSIDE_TYPES:
        gating = {
            "declared_outputs": bool(node.get("outputs")),
            "implementation": impl,
            "real_implementation": real,
        }
        return {"ok": all(gating.values()),
                "checklist": {**gating,
                              "tests_authored": bool(node.get("tests")),
                              "declared_inputs": bool(node.get("inputs")),
                              "criteria": bool(cfg.get("criteria"))},
                "missing": [k for k, v in gating.items() if not v],
                "advisory": [] if cfg.get("criteria") else ["criteria"],
                "not_ready": _not_ready(workflow)}

    tests = {"all_pass": True, "results": []}
    gating = {
        "declared_outputs": bool(node.get("outputs")),
        "implementation": impl,
        "real_implementation": real,
    }
    checklist = {**gating,
                 "tests_authored": bool(node.get("tests")),
                 "declared_inputs": bool(node.get("inputs")) or nt == "user-input",
                 "criteria": bool(cfg.get("criteria"))}

    missing = []
    for k, v in gating.items():
        if v:
            continue
        if k == "tests_pass" and tests.get("results"):
            bad = next((x for x in tests["results"] if not x.get("passed")), None)
            if bad:
                missing.append(f"tests_pass (test {str(bad.get('name'))!r} "
                               f"failed: {str(bad.get('detail'))[:220]})")
                continue
        missing.append(k)
    return {"ok": all(gating.values()), "checklist": checklist,
            "missing": missing,
            "advisory": [] if cfg.get("criteria") else ["criteria"],
            "not_ready": _not_ready(workflow)}

# Each sample carries a link so a mention in chat can reopen it right there
def tool_list_samples(workflow: dict) -> dict:
    return {"samples": [
        {**{k: s.get(k) for k in ("name", "ref", "mime", "size")},
         "shared_ts": s.get("ts"),
         "link": "/api/blobs/" + str(s.get("ref") or "").replace("blob:", "")}
        for s in fresh_samples(workflow)]}

_TEXT_MIMES = ("application/json", "application/xml", "application/csv",
               "application/x-ndjson", "application/javascript")

_SAMPLE_CAP = 60_000

def tool_read_sample(workflow: dict, name: str) -> dict:
    matches = [s for s in fresh_samples(workflow) if s.get("name") == name]
    if len({s.get("ref") for s in matches}) > 1:
        return {"error": f"sample name {name!r} is ambiguous; use the refs from list_samples"}
    result = _read_sample_content(workflow, name)
    if matches and not result.get("error"):
        result.update({"ref": matches[0]["ref"],
                       "input_hint": "Pass this ref as the value of the declared file input to run_cell or run_ai_step; the name is only its label."})
    return result

def _read_sample_content(workflow: dict, name: str) -> dict:
    s = next((s for s in fresh_samples(workflow) if s.get("name") == name), None)
    if not s:
        have = [x.get("name") for x in fresh_samples(workflow)]
        return {"error": f"no sample {name!r}. "
                         + (f"These exist: {', '.join(repr(h) for h in have)}"
                            if have else "This workflow has no samples yet.")}
    try:
        data = blobstore.get(s["ref"])
    except FileNotFoundError:
        return {"error": f"sample {name!r} bytes are missing from the blobstore"}
    mime = s.get("mime") or ""
    if mime.startswith("text/") or mime in _TEXT_MIMES:
        text = data.decode("utf-8", errors="replace")
        if len(text) > _SAMPLE_CAP:
            return {"name": name, "mime": mime, "truncated": True,
                    "text": text[:_SAMPLE_CAP],
                    "note": f"first {_SAMPLE_CAP} of {len(text)} characters - "
                            "for the rest, parse the sample in a cell (declare an input with the sample's name; read_input gives the full bytes)"}
        return {"name": name, "mime": mime, "text": text}

    if mime == "application/pdf":
        text, pages = _pdf_text(data)
        if text is None:
            return {"name": name, "mime": mime, "size": s.get("size"),
                    "note": "this PDF could not be read as text here - a workflow step can still read it on a model that reads PDFs"}
        if not text.split():
            return {"name": name, "mime": mime, "size": s.get("size"), "pages": pages,
                    "note": "This PDF has no text that can be extracted; it is a scan. The Builder Agent reads only PDFs whose text can be extracted without OCR. Tell the user so in one line; a workflow step can still read this file on a model that reads PDFs."}
        if len(text) > _SAMPLE_CAP:
            return {"name": name, "mime": mime, "pages": pages, "truncated": True,
                    "text": text[:_SAMPLE_CAP],
                    "note": f"the text of {pages} pages, the first {_SAMPLE_CAP} of "
                            f"{len(text)} characters - for the rest, parse the "
                            "sample in a cell (declare an input with the sample's name; read_input gives the full bytes)"}
        return {"name": name, "mime": mime, "pages": pages, "text": text}

    if mime in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        import base64
        return {"name": name, "mime": mime, "size": s.get("size"),
                "image": {"media_type": mime,
                          "data": base64.b64encode(data).decode()}}
    return {"name": name, "mime": mime, "size": s.get("size"),
            "note": "binary sample - parse it in a cell: declare an input named after the sample (run_cell resolves it to the stored bytes; read_input returns them) and extract what you need there (for a PDF, the pypdf package). The parsed result becomes the recorded output."}

# The text of a PDF, page by page, with a page marker between pages; None when the file cannot be opened
def _pdf_text(data: bytes) -> tuple:
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        parts, words = [], 0
        for i, page in enumerate(reader.pages, 1):
            body = page.extract_text() or ""
            words += len(body.split())
            parts.append(f"[page {i}]\n" + body)

        return ("\n\n".join(parts) if words else ""), len(reader.pages)
    except Exception:
        return None, 0

# Queues a cross-workflow note; it is checked for identifying values now and published only when the build succeeds
def tool_save_learning(workflow: dict, title: str, content: str) -> dict:
    from storage import learnings as _learnings
    bad = _learnings.check(title, content, workflow)
    if bad:
        return bad
    entry = {"title": str(title).strip(), "content": str(content).strip(),
             "ts": time.time()}
    q = workflow.setdefault("pending_learnings", [])
    slug = _learnings._slug(entry["title"])
    q[:] = [e for e in q if _learnings._slug(str(e.get("title") or "")) != slug]
    q.append(entry)
    save(workflow)
    return {"ok": True, "queued": entry["title"],
            "note": "noted - it is saved for every workflow when this build goes green (proven means built); until then it stays with this build. Same title updates it."}

# Records which attached environment supplies a name two of them define; the pin is internal, never shown
def tool_bind_variable(workflow: dict, name: str, environment: str) -> dict:
    from storage import environments as _envs
    name = str(name or "").strip()
    if not name:
        return {"error": "bind_variable needs the variable's name"}
    envs = _envs.attached(workflow)
    if not envs:
        return {"error": "this workflow has no environments attached"}
    want = str(environment or "").strip().lower()

    if want == "workflow":
        workflow.setdefault("env_bindings", {})[name] = "workflow"
        save(workflow)
        return {"ok": True, "bound": name, "environment": "workflow",
                "note": "the workflow's own variable supplies this name"}
    env = next((e for e in envs
                if e["id"].lower() == want
                or (e.get("name") or "").strip().lower() == want), None)
    if not env:
        return {"error": f"no attached environment matches {environment!r} - "
                         "attached: "
                         + ", ".join(e.get("name") or e["id"] for e in envs)}
    if not any(v["name"] == name for v in env.get("variables", []) or []):
        return {"error": f'the environment "{env.get("name") or env["id"]}" has '
                         f'no variable named {name!r}'}
    workflow.setdefault("env_bindings", {})[name] = _envs.canonical(env["id"], name)
    store.save(workflow)
    return {"ok": True,
            "message": f'This workflow now uses "{name}" from the '
                       f'"{env.get("name") or "environment"}" environment.'}

# Updates the workflow's purpose record incrementally; facts merge and are only removed by name
def tool_save_intent(workflow: dict, summary: str, facts: Optional[list] = None,
                     instructions: str = "", sources: Optional[list] = None,
                     description: str = "",
                     remove_facts: Optional[list] = None) -> dict:
    if not str(summary or "").strip():
        return {"error": "intent needs a summary (2-4 plain sentences: what this workflow is for, for whom, and what counts as correct)"}
    prev_doc = workflow.get("intent") or {}

    prev = prev_doc.get("instructions") or ""

    def _fkey(s) -> str:
        return " ".join(str(s or "").split()).lower()
    drop = {_fkey(f) for f in (remove_facts or []) if _fkey(f)}
    merged, seen = [], set()
    for f in list(prev_doc.get("facts") or []) + list(facts or []):
        f = str(f).strip()
        k = _fkey(f)
        if not f or k in seen or k in drop:
            continue
        seen.add(k)
        merged.append(f)
    workflow["intent"] = {
        "summary": str(summary).strip(),
        "facts": merged,
        "instructions": str(instructions or "").strip() or prev,
        "updated": time.time(),
    }

    if not str(workflow.get("description") or "").strip():
        line = str(description or "").strip() \
            or str(summary).strip().split(". ")[0].strip().rstrip(".")
        workflow["description"] = line
    save(workflow)
    return {"ok": True, "note": "intent saved - the user sees it in the Information tab (Purpose + Instructions)"}

# Bounded read of a node's stored run cases, filtered by outcome
def tool_read_cases(workflow: dict, node_id: str, outcome: str = "all",
                    limit: int = 10) -> dict:
    node = find_node(workflow, node_id)
    if not node:
        name = humanise_name(str(node_id or ""))
        has_cell = False
        try:
            from agent import cells as _cm
            has_cell = any(c.get("name") == name
                           for c in _cm.inventory(workflow["id"]))
        except Exception:
            pass
        return {"total": 0, "returned": 0, "cases": [],
                "note": (f"nothing is built by the name {name!r}, so there "
                         "are no run cases yet"
                         + (" - its recorded try (your cell) is the whole record and is already in your context"
                            if has_cell else "")
                         + ". Do not retry this call for unbuilt steps.")}
    if outcome not in ("all", "success", "failure"):
        outcome = "all"
    rows = corpus.cases(workflow['id'], node_id, outcome)
    lim = max(1, min(int(limit or 10), 50))
    return {"total": len(rows), "returned": min(lim, len(rows)), "cases": rows[-lim:]}

# The bounded whole-workflow overview - counts and failure classes, no case payloads
def tool_corpus_summary(workflow: dict) -> dict:
    ids = [n["id"] for n in workflow["nodes"]]
    stats = corpus.summary(workflow['id'], ids)
    return {"nodes": [
        {"node_id": nid, "name": n.get("name"), "type": node_type_of(n),
         **stats.get(nid, {})}
        for nid, n in ((n["id"], n) for n in workflow["nodes"]) if nid in stats]}

# One read-only, row-capped SELECT over the stored run cases
def tool_query_corpus(workflow: dict, sql: str) -> dict:
    return corpus.query(workflow['id'], sql)

# Attaches a diagnosis as its own event; the raw record is never mutated
def tool_diagnose_case(workflow: dict, node_id: str, case_id: str, cause: str,
                       cls: str = "", fix: str = "") -> dict:
    node = find_node(workflow, node_id)
    if not node:
        return {"error": f"no node {node_id!r}"}
    if not str(cause or "").strip():
        return {"error": "a diagnosis needs a cause - what property of the input made it fail, stated as a CLASS, not this instance"}
    if not any(c.get("case_id") == case_id for c in corpus.cases(workflow["id"], node_id, "failure")):
        return {"error": f"no failure case {case_id!r} on node {node_id!r}"}
    corpus.diagnose(workflow['id'], node_id, case_id, cause, cls or None, fix or None)
    return {"ok": True}

# Runs one code or AI node in isolation - never connectors, which fire real side effects
def tool_run_node(workflow: dict, node_id: str, sample_input: dict) -> dict:
    node = find_node(workflow, node_id)
    if not node:
        return {"error": f"no node {node_id!r}"}
    nt = node_type_of(node)
    if nt in OUTSIDE_TYPES:
        return {"error": "run_node never runs a connector or browser step - it would fire a real external side effect; design against the corpus (read_cases) and let the user's next Run be the test"}
    refuse = step_types.function(nt, "refuse_run_alone")
    if refuse:
        return {"error": refuse(node)}

    from agent import cells as _cellmod
    recorded = _cellmod.latest_outputs(workflow["id"])
    samples = {s.get("name"): s.get("ref") for s in workflow.get("samples", [])}
    fixed = {}
    for k, v in (sample_input or {}).items():
        if isinstance(v, str):
            if v.startswith((RECORDED_PREFIX, ISSUE_PREFIX)):
                v, err = resolve_recorded(k, v, recorded, issue_case_inputs(workflow))
                if err:
                    return {"error": err}
            elif v in samples:
                v = samples[v]
        fixed[k] = v
    sample_input = fixed
    res = _run_slice(workflow, node, sample_input)
    out = res.get("outputs", {}).get(f"tmp_{node_id}") if res.get("status") == "completed" else None
    return {"status": res.get("status"), "reason": res.get("reason"), "output": out}

# A built step is not rewritten before the user has seen what changes and why
def _proposal_first(workflow: dict, name: str, text: str) -> Optional[str]:
    if not workflow.get("nodes"):
        return None
    built = next((n for n in workflow.get("nodes") or []
                  if n.get("name") == name), None)
    if built is not None and \
            code_key((built.get("config") or {}).get("prompt")) == code_key(text):
        return None
    return steps.change_scope_refusal(workflow, name, what="what the step asks the model")

# Saves the plan and validates its design; a step can only leave the plan by being explicitly removed
def tool_save_plan(workflow: dict, plan: dict) -> dict:
    from agent import plan_save
    return plan_save.save(workflow, plan)

# Stores values the user gave: typed to match the step that reads them, secrets routed into the encrypted store by name
def tool_declare_variables(workflow: dict, entries: list) -> dict:
    if not isinstance(entries, list) \
            or not any(isinstance(e, dict) for e in entries or []):
        return {"error": "nothing declared - pass entries=[{name, value?, secret?, persistent?}], one entry per variable"}
    added, existing, valued, kept = [], [], [], []
    secret_stored: list = []
    over_writes: dict = {}
    for e in entries or []:
        if not isinstance(e, dict):
            return {"error": "every entry must be an object like {name, value?, secret?, persistent?}"}
        name = str(e.get("name") or "").strip()
        if not name:
            return {"error": "every variable entry needs a name"}

        holder = environments.attached_holder(workflow, name)
        if holder and (e.get("secret") or not e.get("overwrite")):
            return {"error": f'"{name}" already comes from the attached '
                             f'environment "{holder}" - steps read it by name '
                             "(read_input / get_secret), so there is nothing to declare"
                             + (". A secret is never copied out of an environment." if e.get("secret") else
                                ". If the user wants a workflow-specific value instead, confirm with them and re-send this entry with overwrite:true.")}
        if e.get("secret") and str(e.get("value") or "").strip():
            from storage import secrets_store as _ss
            value = str(e.get("value"))
            _ss.set_secret(name, value, _ss.workflow_owner(workflow["id"]))
            redact_everywhere(workflow, name, value)
            secret_stored.append(name)

        val = "" if e.get("secret") else e.get("value")
        if e.get("secret"):
            e = {**e, "value": ""}
        if not e.get("secret"):
            val, understood = environments.type_variable_value(workflow, name, val)
            if not understood:
                t = environments.port_type_for(workflow, name)
                return {"error": f"'{name}' is used as a {t}, but "
                                 f"{val!r} is not one - give a {t} value, or "
                                 "change the step's input to match"}
        cur = next((v for v in workflow.get("variables", [])
                    if v.get("name") == name), None)
        val_set = environments.value_is_set(val)
        if cur is not None:
            held = cur.get("value")
            held_set = environments.value_is_set(held)
            if val_set and not cur.get("secret") \
                    and (not held_set or e.get("overwrite")):
                cur["value"] = val
                valued.append(name)
                if held_set and e.get("overwrite"):
                    over_writes[name] = val
            elif val_set and not cur.get("secret") and held != val:
                kept.append(f"'{name}' already holds '{held}' - kept it; if "
                            "the user wants the new value, confirm with them and re-send this entry with overwrite:true")
                existing.append(name)
            else:
                existing.append(name)
            continue
        workflow.setdefault("variables", []).append(
            {"name": name, "value": None if e.get("secret") else val,
             "secret": bool(e.get("secret")),
             "persistent": e.get("persistent", True) is not False})
        added.append(name)
        if val_set:
            valued.append(name)
    if over_writes:
        fresh = store.load(workflow["id"])
        if fresh is not None:
            fmap = {v.get("name"): v for v in fresh.get("variables", [])}
            for nm, val in over_writes.items():
                fv = fmap.get(nm)
                if fv is not None and not fv.get("secret"):
                    fv["value"] = val
            store.save(fresh)
    save(workflow)
    return {"ok": True, "added": added, "already_present": existing,
            **({"values_stored": valued} if valued else {}),
            **({"secrets_stored": secret_stored,
                "secrets_note": "stored in the secret store under these names and removed from the chat record - refer to them by name only (get_secret at run time)"}
               if secret_stored else {}),
            **({"values_kept": kept} if kept else {}),
            "note": "unset values are the user's: set on the Inputs tab, or asked for when a run needs one"}

# Removes settings: blank ones freely, a valued or secret one only when the user asked for it by name
def tool_remove_variables(workflow: dict, names: list, confirmed: bool = False) -> dict:
    from storage import secrets_store as _secrets_store
    wanted = [str(x).strip() for x in (names or []) if str(x or "").strip()]
    if not wanted:
        return {"error": "names is required - the variable names to remove"}
    have = {v.get("name"): v for v in workflow.get("variables") or []}
    still_needed = environments.workflow_variable_names(workflow)
    plan_reads = {p.get("name") for n in (workflow.get("plan") or {}).get("nodes") or []
                  for p in (n.get("inputs") or [])}
    plan_makes = {p.get("name") for n in (workflow.get("plan") or {}).get("nodes") or []
                  for p in (n.get("outputs") or [])}
    still_needed |= (plan_reads - plan_makes)
    removed, refused, unknown, valued = [], [], [], []
    for nm in wanted:
        v = have.get(nm)
        if v is None:
            unknown.append(nm)
            continue
        if nm in still_needed:
            refused.append(nm)
            continue

        if not confirmed and (v.get("secret") or environments.value_is_set(v.get("value"))):
            valued.append(nm)
            continue
        if v.get("secret"):
            try:
                _secrets_store.delete_secret(
                    nm, _secrets_store.workflow_owner(workflow["id"]))
            except Exception:
                pass
        removed.append(nm)
    if removed:
        gone = set(removed)
        workflow["variables"] = [v for v in workflow.get("variables") or []
                                if v.get("name") not in gone]
        for nm in removed:
            (workflow.get("env_bindings") or {}).pop(nm, None)

        fresh = store.load(workflow["id"])
        if fresh is not None:
            fresh["variables"] = [v for v in fresh.get("variables", [])
                                  if v.get("name") not in gone]
            for nm in removed:
                (fresh.get("env_bindings") or {}).pop(nm, None)
            store.save(fresh)
        save(workflow)
    out: dict = {"ok": True, "removed": removed}
    if refused:
        out["refused"] = refused
        out["note"] = ("kept " + ", ".join(refused) + ": a step reads each of these and no earlier step produces it, so the variable is its only source - removing it would pause every run asking for the value. Change the step first if it should not read it.")
    if valued:
        out["kept_valued"] = valued
        out["value_note"] = ("kept " + ", ".join(valued) + ": these hold a value the user set. Say they are unread and remove them only if the user asks for that by name (re-send with confirmed=true then).")
    if unknown:
        out["unknown"] = unknown
    return out

# Declares which step outputs a finished run keeps as its results
def set_deliverables(workflow: dict, items: list) -> dict:
    norm, errors = deliverables.normalise(workflow, items)
    if errors:
        return {"ok": False, "errors": errors}
    workflow["deliverables"] = norm
    save(workflow)
    return {"ok": True, "deliverables": norm,
            "note": "each completed run now keeps these values as its results"}

STEP_STATES = ("planned", "validated", "coded", "finished")

# A build refuses while any step lacks a recorded successful try - untested work cannot slip into a finished workflow
def tool_build_workflow(workflow: dict,
                        include_proposals: Optional[list] = None) -> dict:
    plan = workflow.get("plan") or {}
    if plan.get("status") not in ("draft", "building"):
        return {"error": "No plan is ready to build yet. Write it with save_plan first (same turn), then call build_workflow."}

    untyped = [f'"{n.get("name")}": ' + ", ".join(untyped_ports(n))
               for n in plan.get("nodes") or [] if untyped_ports(n)]
    if untyped:
        return {"error": "these steps have ports with no type yet - "
                         + "; ".join(untyped) + ". " + UNTYPED_PORTS}

    from agent import plan_save as _plan_save
    gaps = _plan_save.gaps_now(workflow, plan)
    if gaps:
        plan["design_gaps"] = gaps
    else:
        plan.pop("design_gaps", None)
    if plan.get("design_gaps"):
        return {"error": "the plan still has open design gaps: "
                         + "; ".join(plan["design_gaps"])
                         + ". Fix them in the design and save_plan again, then call build_workflow."}

    if not plan_approved(workflow):
        return {"error": "the user hasn't agreed to build this workflow yet. Call save_plan - it shows them the plan and waits for their answer - then build."}

    if plan.get("ticket_id") and not plan.get("fix_approved_ts"):
        return {"error": "the user hasn't approved this fix yet. Call save_plan - it shows them the diagnosis and the planned change, and waits for their answer - then build. The save carries fix_note {went_wrong, will_change} - two plain sentences for the user, shown above the card."}

    if change_needs_approval(workflow, plan):
        return {"error": "the user hasn't seen what this change does to their workflow yet. Call save_plan with change_note {found, will_change} - it shows them what you found and what will change, and waits for their answer - then build."}
    from agent import steps as _steps
    if _steps.plan_steps_need_approval(workflow, plan):
        return {"error": "the plan's steps no longer match the steps the user approved (a step added or removed, or a step's type changed). Call save_plan - it shows them the steps removed and the steps added, and waits for their answer - then build."}

    untested = untested_steps(workflow, plan.get("nodes") or [])
    if untested:
        return {"error": "these steps have not been tested for real yet: "
                         + ", ".join(f'"{u}"' for u in untested)
                         + ". Run each one as run_cell NOW, in this same turn - the recorded runs become the steps' code and tests - then call build_workflow again. A live send or a browser window raises its own approval card from the cell; a no there settles that step (it counts as done). Do not pre-ask with ask_user."}
    from agent import build_readiness
    readiness = build_readiness.check(workflow, plan)
    if readiness:
        return {"error": "Build readiness: " + " | ".join(readiness),
                "readiness_findings": readiness}

    turnstate.of(workflow).build_now = {"include_proposals": list(include_proposals)
                             if include_proposals else None}
    return {"ok": True}

# Materialises one plan step as a built step from its recorded evidence; a failed build rolls the step back
def build_step(workflow: dict, name: str) -> dict:
    plan = workflow.get("plan") or {}
    name = humanise_name(name)
    pn = next((n for n in plan.get("nodes") or []
               if n.get("name") == name or n.get("id") == name), None)
    if not pn:
        return {"error": f"{name!r} is not a step in the plan"}
    ntype = pn.get("type")
    notes: list = []

    existing = (find_node(workflow, str(pn.get("id"))) if pn.get("id") else None) \
        or find_node(workflow, pn.get("name"))

    if existing and node_type_of(existing) != ntype:
        delete_node(workflow, existing["id"])
        notes.append(f"replaced the leftover {node_type_of(existing)} step with the "
                     f"planned {ntype} step (re-wire its edges)")
        existing = None
    created_here = False
    if existing:
        nid = existing["id"]

        if pn.get("name") and existing.get("name") != pn.get("name"):
            notes.append(f'renamed "{existing.get("name")}" to '
                         f'"{pn.get("name")}"')
            existing["name"] = pn.get("name")
        existing["description"] = pn.get("description", existing.get("description", ""))

        if pn.get("intention") or pn.get("description"):
            existing["intention"] = pn.get("intention") or pn.get("description", "")
        if ntype in OUTSIDE_TYPES:
            existing["read_only"] = bool(pn.get("read_only"))
            existing["external_impact"] = pn.get("external_impact", "")
    else:
        made = _create_node(
            workflow, pn.get("name"), ntype,
            description=pn.get("description", ""),
            external_impact=pn.get("external_impact", ""),
            intention=pn.get("intention") or pn.get("description", ""),
            read_only=bool(pn.get("read_only")),
            node_id=str(pn.get("id") or ""))
        if made.get("error"):
            return {"error": f"step {name!r}: {made['error']}"}
        nid = made["node_id"]
        created_here = True

    def _fail(errors: list) -> dict:
        if created_here:
            delete_node(workflow, nid)
        return {"ok": False, "step": name,
                "errors": [e for e in errors if e]}

    io = _set_declared_io(workflow, nid,
                              pn.get("inputs") or [], pn.get("outputs") or [])
    if not io.get("ok"):
        return _fail(io.get("errors") or [io.get("error")])

    how = str(pn.get("how") or "").strip()
    node_h = find_node(workflow, nid)
    if node_h is not None:
        if how:
            node_h["how"] = how[:600]
        else:
            node_h.pop("how", None)
    if ntype == "ai":
        if not str(pn.get("prompt") or "").strip():
            return _fail(["the plan step has no prompt - it was never tried with run_ai_step; report this plainly"])
        model = str(pn.get("model") or "").strip()

        provider_id = providers.provider_id_for(pn.get("provider") or "")
        if pn.get("provider") and not provider_id:
            notes.append(f"provider {pn.get('provider')!r} is not a workflow provider "
                         "set up in Admin - the model was kept without it")
        elif model:
            row, _why = providers.match_model(model, provider_id)
            if row is not None:
                model, provider_id = row["name"], row.get("provider_id", "")
        if model and not providers.find_model(model, for_node=True,
                                              provider_id=provider_id)[1]:
            notes.append(f"model {model!r} is not set up in Admin"
                         + (f" under {pn.get('provider')!r}" if provider_id else "")
                         + " - left blank; tell the user to add one there before running")
            model, provider_id = "", ""

        r = set_prompt_model(workflow, nid, pn.get("prompt") or "", model,
                                  (None if pn.get("temperature") in (None, "")
                                   else float(pn.get("temperature"))),
                                  provider_id=provider_id)
        if not r.get("ok"):
            return _fail([r.get("error")])

        import providers as _prov
        node = find_node(workflow, nid)
        if pn.get("max_tokens") is not None:
            mt = _prov.clamp_tokens(pn.get("max_tokens"))
            if mt != config.AI_MAX_TOKENS:
                node.setdefault("config", {})["max_tokens"] = mt
            else:
                (node.get("config") or {}).pop("max_tokens", None)
        else:
            (node.get("config") or {}).pop("max_tokens", None)
        save(workflow)
    elif ntype in CODE_TYPES:
        code = str(pn.get("code") or "").strip()
        if not code:
            return _fail(["no proven code for this step - it was never worked as a cell, so it cannot be frozen; report this plainly and end the turn"])

        pkgs = gate.required_packages(code, [str(p) for p in (pn.get("packages") or [])],
                                      is_browser=step_types.may(pn.get("type"), "browser"))
        if pkgs:
            inst = _declare_dependencies(workflow, nid, pkgs)
            if isinstance(inst, dict) and (inst.get("error") or inst.get("ok") is False):
                return _fail([str(inst)[:300]])
        r = _set_code(workflow, nid, code)
        if not r.get("ok"):
            return _fail(r.get("violations") or [r.get("error")])

        node = find_node(workflow, nid)
        t = sandbox.clamp_timeout(pn.get("timeout_seconds"))
        if t:
            node.setdefault("config", {})["timeout_seconds"] = t
        else:
            (node.get("config") or {}).pop("timeout_seconds", None)

        pi = pn.get("per_item") if isinstance(pn.get("per_item"), dict) else None
        if pi and pi.get("input") and pi.get("key"):
            node.setdefault("config", {})["per_item"] = {
                "input": str(pi["input"]).strip(), "key": str(pi["key"]).strip()}
        else:
            (node.get("config") or {}).pop("per_item", None)

        ms = str(pn.get("max_setting") or "").strip()
        if ms:
            node.setdefault("config", {})["max_setting"] = ms
        else:
            (node.get("config") or {}).pop("max_setting", None)

        paths = [str(x).strip() for x in (pn.get("paths") or []) if str(x).strip()]
        if paths:
            node.setdefault("config", {})["paths"] = paths
        else:
            (node.get("config") or {}).pop("paths", None)

        url = str(pn.get("url") or "").strip()
        if url and step_types.may(pn.get("type"), "browser"):
            node.setdefault("config", {})["url"] = url
        else:
            (node.get("config") or {}).pop("url", None)

        opts = pn.get("browser_options")
        if isinstance(opts, dict) and opts and step_types.may(pn.get("type"), "browser"):
            node.setdefault("config", {})["browser_options"] = dict(opts)
        else:
            (node.get("config") or {}).pop("browser_options", None)
        route = str(pn.get("route") or "").strip().lower()
        if route in ("api", "in-page", "ui"):
            node.setdefault("config", {})["route"] = route
        else:
            (node.get("config") or {}).pop("route", None)
        rpm = pn.get("requests_per_minute")
        try:
            rpm = float(rpm)
        except (TypeError, ValueError):
            rpm = 0.0
        if rpm > 0:
            node.setdefault("config", {})["requests_per_minute"] = rpm
        else:
            (node.get("config") or {}).pop("requests_per_minute", None)
        save(workflow)

    if ntype != "user-input":
        pol = str(pn.get("on_invalid_items") or "").strip().lower()
        if pol in ("proceed", "log"):
            node.setdefault("config", {})["on_invalid_items"] = pol
        else:
            (node.get("config") or {}).pop("on_invalid_items", None)
        save(workflow)
    tests = pn.get("tests") or []
    if tests and ntype != "user-input":
        tr = _set_tests(workflow, nid, tests)
        if not tr.get("ok"):
            return _fail(tr.get("errors") or [])

    if pn.get("criteria") and ntype != "user-input":
        cr = _set_criteria(workflow, nid, pn["criteria"])
        if not cr.get("ok"):
            return _fail(cr.get("errors") or [cr.get("error")])

    v = validate_node(workflow, nid)
    out = {"ok": bool(v.get("ok")), "node_id": nid,
           "state": _state(find_node(workflow, nid)),
           "missing": v.get("missing") or [],
           "not_ready": v.get("not_ready") or []}
    if notes:
        out["notes"] = notes
    return out

# Starts the fix for an open issue
def tool_resolve_issue(workflow: dict, ticket_id: str = "") -> dict:
    live = fix_in_progress(workflow)
    if live and (not ticket_id or ticket_id == live):
        return {"error": f"issue {live} is already being fixed in this "
                         "conversation. Save the plan with fix_note {went_wrong, will_change} and changes naming the steps you will change, wait for the user's click on the card, then work those steps and build."}
    open_tickets = [t for t in (workflow.get("tickets") or [])
                    if t.get("status") == "open"]
    if not open_tickets:
        return {"error": "There are no open issues to fix right now."}
    tid = ticket_id or open_tickets[-1]["id"]
    if not any(t["id"] == tid for t in open_tickets):
        return {"error": "No open issue with that id. Open issues: "
                         + ", ".join(t["id"] for t in open_tickets)}
    turnstate.of(workflow).pending_fix = {"ticket_id": tid}
    return {"ok": True, "note": "Fix started - it runs now and streams to the user. Tell them you are looking into it."}

ISSUE_RESOLUTIONS = {
    "bad-data": "the record was malformed at its source - the step was right to reject it, and fixing that data is not this workflow's job",
    "fixed-elsewhere": "the cause was outside this workflow and has been dealt with there",
}

# Closes an issue without changing the workflow, with a one-line reason the Issues tab shows
def tool_close_issue(workflow: dict, ticket_id: str, resolution: str,
                     note: str = "") -> dict:
    tid = str(ticket_id or "").strip()
    t = next((t for t in workflow.get("tickets") or [] if t.get("id") == tid), None)
    if not t:
        return {"error": f"No issue {tid!r}. Open issues: "
                         + (", ".join(t["id"] for t in workflow.get("tickets") or []
                                      if t.get("status") in ("open", "investigating", "in-progress"))
                            or "none")}

    if t.get("status") not in ("open", "investigating", "in-progress"):
        return {"error": f"Issue {tid} is {t.get('status')} - only an open, "
                         "investigating or in-progress issue can be closed this way."}
    res = str(resolution or "").strip()
    if res not in ISSUE_RESOLUTIONS:
        return {"error": "resolution must be one of "
                         + ", ".join(sorted(ISSUE_RESOLUTIONS))}
    line = str(note or "").strip()
    if not line:
        return {"error": "note is required - one plain sentence the user reads on the issue row saying why nothing needs to change."}
    t["status"] = "closed"
    t["resolution"] = res
    t["resolution_note"] = line[:400]
    t["closed_ts"] = time.time()

    plan = workflow.get("plan") or {}
    if plan.get("ticket_id") == tid:
        plan.pop("ticket_id", None)
        plan.pop("fix_approved_ts", None)
    if turnstate.of(workflow).fix_issue_id == tid:
        turnstate.of(workflow).take("fix_issue_id")
    save(workflow)
    return {"ok": True, "closed": tid, "resolution": res,
            "note": "closed - tell the user in one line why nothing in the workflow changes. If such rows are expected to keep arriving, you MAY offer (ask_user, one option) that the step set them aside and carry on in future - never build that unasked."}

# Reports step by step what a run will do - pauses, input sources, gaps - with zero side effects
def tool_preview_run(workflow: dict) -> dict:
    from storage import environments as _envs
    from runtime import executor as _exec
    canonicalise_refs(workflow)
    nodes = {n["id"]: n for n in workflow.get("nodes") or []}
    if not nodes:
        return {"error": "no steps yet - nothing to walk"}
    resolution = _envs.resolve(workflow)
    varnames = {nm for nm, r in resolution.items() if not r.get("ambiguous")}
    edges_in: dict = {}
    for e in workflow.get("edges", []):
        edges_in.setdefault(e["dst"], []).append(e)

    ancestors = plan_logic.ancestors_by_name({}, workflow)
    by_name = {n.get("name"): n for n in workflow.get("nodes") or []}
    lines, problems = [], []
    for nid in _exec._execution_order(workflow):
        n = nodes.get(nid)
        if not n:
            continue
        name, t = n.get("name") or nid, node_type_of(n)
        notes = []
        summary = step_types.function(t, "run_summary")
        if summary:
            notes.append(summary(n, bool(edges_in.get(nid))))
        else:
            for p in n.get("inputs") or []:
                nm = p.get("name")
                if p.get("type") == "secret":
                    notes.append(f"secret '{nm}' injected from the store")
                    continue
                if any(nm in {q.get("name")
                              for q in (by_name.get(a) or {}).get("outputs") or []}
                       for a in ancestors.get(n.get("name"), ())):
                    continue
                if nm in varnames:
                    notes.append(f"'{nm}' comes from a stored variable")
                else:
                    notes.append(f"CANNOT RECEIVE '{nm}' - nothing supplies "
                                 "it (the run will halt here)")
                    problems.append(f"{name}: {nm}")
            if t in OUTSIDE_TYPES and not n.get("read_only", False):
                notes.append("approval is OFF - fires without asking"
                             if n.get("approval_suppressed")
                             else "PAUSES for the user's approval before it fires (an Approve card during the run)")
        lines.append(f"{name} [{t}]" + (f": {'; '.join(notes)}" if notes else ""))
    return {"ok": True, "walk": lines,
            **({"problems": problems} if problems else {})}

# A live AI try must name a step the plan or the build knows; probes keep their freedom
def _step_must_exist(workflow: dict, name: str) -> Optional[str]:
    fresh = store.load(workflow["id"]) or workflow
    steps = {str(n.get("name")) for n in fresh.get("nodes") or []} \
        | {str(n.get("name")) for n in (fresh.get("plan") or {}).get("nodes") or []}
    steps = {s for s in steps if s}
    if not steps or name in steps:
        return None
    return (f"there is no step called {name!r}. An AI try belongs to a step "
            "in the plan - save_plan it first (a name and type is enough), then try it. Steps you have: " + ", ".join(sorted(steps))
            + ". If this was meant to be one of those, use its name; if the workflow needs a new step, the plan is where that is agreed.")

# Runs step code for real through the same sandbox a run uses, and records the result as that step's evidence
def tool_run_cell(workflow: dict, name: str = "", code: str = "", inputs: Optional[dict] = None,
                  packages: Optional[list] = None, fresh: bool = False,
                  browser_ok: bool = False, reason: str = "",
                  send_ok: bool = False, missing: str = "",
                  ask_again: str = "", browser: bool = False, url: str = "",
                  cells: Optional[list] = None) -> dict:
    from agent import cell_gates
    if cells is not None:
        return cell_gates.run_many(workflow, cells)
    if not name and not code:
        return {"error": "name the cell and pass its code, or pass cells=[...] for several independent cells at once"}
    return cell_gates.run(workflow, name, code, inputs=inputs, packages=packages,
                          fresh=fresh, browser_ok=browser_ok, reason=reason,
                          send_ok=send_ok, missing=missing, ask_again=ask_again,
                          browser=browser, url=url)

# The model for a step, picked by code from the default provider, or asked of the user on a card the harness raises itself
def pick_or_ask(workflow: dict, step_name: str, inputs: dict) -> dict:
    from runtime.capability import input_file_kinds
    kinds = input_file_kinds(inputs)
    picked = providers.pick_model(kinds)
    if picked:
        return {"model": picked["model"], "provider_id": picked["provider_id"]}
    options = providers.model_options(kinds)
    if not options:
        return {"error": providers.no_pick_sentence(kinds, step=step_name)
                         + " Tell the user this in your own words and stop here."}
    from agent import actions as _actions
    question = (f'Which AI model should the step "{step_name}" use? '
                + providers.no_pick_sentence(kinds, step=step_name).split(". ")[0] + ".")
    labels = [o["label"] for o in options]
    iid, entry, answer = _actions._raise_card(
        workflow, "ask", {"question": question, "options": labels, "secret": False,
                          "secret_name": "", "upload": False, "folder": False},
        "ask", {"question": question, "options": labels}, "ask")
    if answer is None:
        from agent import turnstate as _ts
        _ts.of(workflow).ask_open = True
        return {"error": "no model chosen yet"}
    text = str(answer.get("text") if isinstance(answer, dict) else answer or "").strip()
    hit = next((o for o in options if o["label"] == text or o["name"] == text), None) \
        or next((o for o in options if o["name"].lower() in text.lower()), None)
    if not hit:
        return {"error": f"the user answered {text!r}, which is not one of the models "
                         "offered; take it as their words and pass the model they mean in `model`."}
    return {"model": hit["name"], "provider_id": hit["provider_id"], "chosen_by_user": True}

# Tries an AI step for real with its enforced output schema; an identical retry answers from the record
def tool_run_ai_step(workflow: dict, name: str, prompt: str, model: str = "",
                     inputs: Optional[dict] = None,
                     outputs: Optional[list] = None,
                     fresh: bool = False, provider: str = "") -> dict:
    from runtime import capability
    import providers

    if not str(name or "").strip():
        return {"error": "name the ai step"}

    name = steps.step_name_for(workflow, name)
    if not str(prompt or "").strip():
        return {"error": "an ai step needs a prompt"}
    gate_note = skeleton_first(workflow)
    if gate_note:
        return {"error": gate_note}
    gate_note = _step_must_exist(workflow, name)
    if gate_note:
        return {"error": gate_note}
    gate_note = _proposal_first(workflow, name, prompt)
    if gate_note:
        return {"error": gate_note, **({"widen_change": name}
                                       if steps.outside_change_scope(workflow, name) else {})}
    import time as _time
    from agent import cells as _cellmod
    recorded = _cellmod.latest_outputs(workflow["id"])
    from agent import input_refs
    inputs, error = input_refs.resolve(workflow, inputs, recorded)
    if error:
        return {"error": error}

    if seed_file_kinds(workflow, name, inputs):
        save(workflow)

    provider_id = providers.provider_id_for(provider)
    if model:
        row, why = providers.match_model(model, provider_id)
        if row is None:
            return {"error": why}
        model, provider_id = row["name"], row.get("provider_id", "")
        if not providers.is_ready({"model": model, "provider_id": provider_id}):
            state, prov = providers.node_model_state(model, provider_id)
            return {"error": f"the model the user asked for, {model}, is listed under "
                             f"{prov.get('name') or 'its provider'} but "
                             + ("has no API key yet" if state == "no-key" else "is switched off")
                             + " - tell the user, and ask whether to fix that on the Admin page or to use another model."}
    else:
        picked = pick_or_ask(workflow, name, inputs)
        if picked.get("error"):
            return picked
        model, provider_id = picked["model"], picked["provider_id"]
        _pn = next((n for n in (workflow.get("plan") or {}).get("nodes") or []
                    if n.get("name") == name), None)
        if _pn is not None and picked.get("chosen_by_user"):
            _pn["model"], _pn["provider"] = model, provider_id
            save(workflow)

    ports = [{k: v for k, v in o.items() if k in PORT_FIELDS}
             if isinstance(o, dict) else {"name": str(o), "type": ""}
             for o in (outputs or [])]
    for q in ports:
        q.setdefault("name", "")
        q.setdefault("type", "")

    port_names = {p["name"] for p in ports}
    prior = _cellmod.latest_ok(workflow["id"], name, kind="ai")
    prior_out = (prior or {}).get("output")
    same_shape = not port_names or (isinstance(prior_out, dict)
                                    and set(prior_out.keys()) == port_names)
    if prior and same_shape and not fresh \
            and not (isinstance(prior_out, dict)
                     and "$missing_evidence" in prior_out) \
            and code_key(prior.get("code")) == code_key(prompt) \
            and prior.get("inputs") == dict(inputs or {}) \
            and prior.get("model") == model:
        return {"ok": True, "cell": name, "output": prior_out, "model": model,
                "duplicate": True,
                "note": "nothing changed since the last try (same prompt, same inputs, same model) - this is the RECORDED result, not a new run. To move forward, change something: the prompt, the inputs or the model - or show the user what you have."}
    t0 = _time.time()
    try:
        _pn_ai = next((n for n in (workflow.get("plan") or {}).get("nodes") or []
                       if n.get("name") == name), None)

        out = capability.ai_call(
            prompt,
            {"model": model, "provider_id": provider_id,
             "temperature": (_pn_ai or {}).get("temperature")},
            dict(inputs or {}), output_ports=ports or None,
            max_tokens=providers.clamp_tokens((_pn_ai or {}).get("max_tokens")))
        if isinstance(out, dict) and out.get("_unparsed"):
            raise RuntimeError(str(out.get("_text") or "the model call failed"))

        _try_usage = capability.take_ai_usage()
        if _try_usage:
            try:
                from storage import db as _db
                _owner = providers.find_model(model, for_node=True,
                                              provider_id=provider_id)[0]
                _db.ai_usage_add(provider_id=_owner.get("id", ""), model=model,
                                 workflow_id=workflow["id"], run_id="",
                                 node_id=str(name).strip(),
                                 tokens_in=_try_usage.get("in", 0),
                                 tokens_out=_try_usage.get("out", 0),
                                 source="build")
            except Exception:
                pass

        problems = _ports.check_ports(ports, out, require_all=True,
                                      empty_ok=True) if ports else []
        if problems:
            ok, result = False, {
                "ok": False,
                "error": "the answer did not match what this step declares: "
                         + "; ".join(_ports.problem_lines(problems)),
                "output": out}
        else:
            ok, result = True, {"ok": True, "output": out}
    except Exception as e:
        ok, result = False, {"ok": False, "error": f"{type(e).__name__}: {e}"}
    n = _cellmod.record(workflow["id"], str(name).strip(), str(prompt),
                        dict(inputs or {}), result.get("output"), ok,
                        _time.time() - t0, kind="ai", model=model)
    result.update({"cell": name, "runs": n, "model": model,
                   "seconds": round(_time.time() - t0, 1)})
    if ok:
        result["note"] = (f"recorded (model {model}) - a same-named ai plan "
                          "step gets this as its authored example and its model, so leave `tests` out for it. Later cells can chain from this output via \"$recorded\"")
    if n >= 4:
        result["note"] = ((result.get("note") or "") +
                          f" NOTE: this is try {n} of this step. If the "
                          "output still misses, the missing piece is information, not another run: re-read the source material (read_sample) or the tool's guide, or switch to another approach you know. Ask the user only about output preferences, never method.").strip()
    return result

# Puts a produced file into the chat as a download card, so results are shown rather than described
def tool_share_file(workflow: dict, path: str, label: str = "") -> dict:
    import pathlib

    ref = str(path or "").strip()
    if ref.startswith("blob:"):
        if not blobstore.exists(ref):
            return {"error": f"no stored file {ref!r} - use the ref a cell's "
                             "write_file returned"}
        data = blobstore.get(ref)
        name = str(label or "").strip() or "result"
        return _share_bytes(workflow, name, data)
    work = (config.workflow_dir(workflow["id"]) / "work").resolve()
    target = pathlib.Path(ref).expanduser()
    if not target.is_absolute():
        target = work / target
    target = target.resolve()
    if work not in target.parents and target != work:
        return {"error": "share_file shares files from your working directory, or a cell-produced file by its blob: reference (write_file returns it)"}
    if not target.is_file():
        return {"error": f"no file at {path!r}"}
    data = target.read_bytes()
    name = str(label or "").strip() or target.name
    return _share_bytes(workflow, name, data, orig_name=target.name)

# The shared tail of share_file - store the bytes, land the download card
def _share_bytes(workflow: dict, name: str, data: bytes,
                 orig_name: str = "") -> dict:
    import mimetypes
    fname = orig_name or name
    mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    ref = blobstore.put(data, mime=mime, meta={"name": fname},
                        owner=blobstore.workflow_owner(workflow["id"]))

    _transcript.append_message(workflow, "assistant", name,
                               files=[{"name": name, "ref": ref,
                                       "size": len(data)}])
    save(workflow)
    return {"ok": True, "shared": name, "ref": ref, "size": len(data),
            "note": "the user sees a download card in chat - ask for feedback on THIS, never on an output they cannot see"}

# Imports a captured recording as a scrubbed sample; the raw original is replaced, never kept beside it
def tool_import_recording(workflow: dict, name: str = "",
                          from_blob: str = "", from_sample: str = "") -> dict:
    from agent import capture
    ref = str(from_blob or "").strip()
    sname = str(from_sample or "").strip()
    if bool(ref) == bool(sname):
        return {"error": "pass exactly ONE source: from_blob (the ref a cell's write_file returned for the capture file) or from_sample (an uploaded sample's name)"}
    if ref:
        if not ref.startswith("blob:"):
            return {"error": "from_blob must be a blob: reference (write_file returns it) - raw file paths are refused"}
        if not blobstore.exists(ref):
            return {"error": f"no stored file {ref!r}"}
        data = blobstore.get(ref)
        entry_name = str(name or "").strip() or None
    else:
        s = next((s for s in workflow.get("samples") or []
                  if s.get("name") == sname), None)
        if not s:
            return {"error": f"no sample {sname!r} - list_samples shows "
                             "what exists"}
        data = blobstore.get(s["ref"])
        if data is None:
            return {"error": f"sample {sname!r} bytes are missing from the "
                             "blobstore"}

        entry_name = str(name or "").strip() or sname
    r = capture.import_recording(
        workflow, data.decode("utf-8", "replace"), name=entry_name)
    if r.get("ok"):
        if sname and r.get("name") != sname:
            workflow["samples"] = [s for s in workflow.get("samples") or []
                                  if s.get("name") != sname]
            save(workflow)
        r["note"] = ("scrubbed and stored as a recording sample - read it with read_sample and mine the calls (25-recordings); fold `domains` into the plan step's domains or the connector is egress-blocked")
    return r

# Asks the user one question as a card and waits; a secret answer goes into the store, never through the chat
def tool_ask_user(workflow: dict, question: str, options: Optional[list] = None,
                  secret: bool = False, secret_name: str = "",
                  upload: bool = False, folder: bool = False,
                  fields: Optional[list] = None,
                  allow_stored: bool = False) -> dict:
    clean_fields, ferr = clean_ask_fields(fields, secret=secret,
                                         upload=upload, folder=folder)
    if ferr:
        return {"error": ferr}
    out = {"ask_user": question, "options": option_labels(options)}
    if clean_fields:
        out["fields"] = clean_fields
    if secret:
        out.update({"secret": True, "secret_name": str(secret_name or "").strip()})
    if upload:
        out["upload"] = True
    if folder:
        out["folder"] = True

    turnstate.of(workflow).ask_open = True
    return out

_SAMPLE_SPECS = [
    ("list_samples", tool_list_samples, "List the samples the user uploaded, each with its name, stored file reference (ref), file type, size and download link. Use this list instead of asking again, and include the link when you mention an upload.", {}),
    ("read_sample", tool_read_sample, "Read a sample by its name. Returns a preview, cut short for a long file, with the sample's stored file reference (ref) and an input_hint. Pass the ref to a file input: the preview and a path are not the file reference. To read the parts of a long file the preview leaves out, read them in a try.", {"name": str}),
]

_AGENT_SPECS = [
    ("list_nodes", tool_list_nodes, "List the workflow's built steps, with a short summary of each.", {}),
    ("read_earlier_result", tool_read_earlier_result, "Open an earlier tool result in full, or one part of it. The working memory at the start of a turn lists every earlier result as one line with its id, and a result too big to send whole came back as an outline with its result_id. Pass that id (or ids=[...] for several) instead of doing the same work again. path goes into the result (for example data.output.rows[3]); find keeps only the entries, keys or lines that contain the text; offset and limit take a slice of a list or a text. A part that is still too big comes back as an outline again: go one level further in.",
     {"id": str, "ids": list, "path": str, "find": str, "offset": int, "limit": int}),
    ("read_node", tool_read_node, "Read a step's inputs and outputs, its code or prompt, and its settings, whether it is built or only planned. Pass node_id, or node_ids to read several steps at once. Test examples come back as their shapes, not their recorded values.",
     {"node_id": str, "node_ids": list}),
    *_SAMPLE_SPECS,

    ("share_file", tool_share_file, "Show a file a step made as a download card in the chat. path is the blob reference returned by write_file, or a file in the allowed working folder; label is the name the user sees. Show the user the real result before you ask what they think of it.",
     {"path": str, "label": str}),
    ("save_learning", tool_save_learning, "Save a finding you confirmed that holds for other workflows too; it is published when the build finishes successfully. title names the situation it applies to; content says under which conditions it holds and what showed it, never a theory you have not tried. Saving under the same title replaces the old learning. Never include workflow names, private links, ids, setting values or credentials; facts about this task go in save_intent.",
     {"title": str, "content": str}),

    ("import_recording", tool_import_recording, "Import a HAR file or capture JSON, unchanged, as a recording sample with logins and passwords masked. Use from_blob for a file reference a try saved, or from_sample for the name of a sample the user uploaded (the recording replaces that sample). Returns the hosts the recording contacted, to look into; declare only the hosts the task needs, not every analytics or page-file host. A recording that holds no calls or actions is refused.",
     {"name": str, "from_blob": str, "from_sample": str}),
    ("read_cases", tool_read_cases, "Read a few of a step's recorded cases: the real inputs, outputs and verdicts of earlier runs. Design changes and find the cause of failures from these, never by running the saved workflow. outcome filters by all, success or failure (not by the kind of step).",
     {"node_id": str, "outcome": str, "limit": int}),
    ("diagnose_case", tool_diagnose_case, "Record on a failed case the cause you found, and the supported class/conditions under which it happens; add the fix when you know it. Never claim more certainty than you have, and never turn one failure you could not explain into a rule for every case.",
     {"node_id": str, "case_id": str, "cause": str, "cls": str, "fix": str}),
    ("query_corpus", tool_query_corpus, "Ask the recorded cases one precise question: one read-only SELECT over cases(node_id, case_id, run_id, kind, ts, input_hash, cls, cause, inputs, output, verdict, observed, meta) / diagnoses(case_id, cause, cls, fix) / run_paths(workflow_id, run_id, path). The inputs, output, verdict, observed and meta columns hold JSON text (use json_extract). The number of rows returned is capped: ask for counts, groups and comparisons rather than printing rows.", {"sql": str}),
    ("corpus_summary", tool_corpus_summary, "Return the number of recorded cases for the whole workflow, their classes and the most common shapes. Use it when that overview helps you find a cause or the user asked for a review, never as a call you make at the end of every turn. What it shows does not allow you to change anything.", {}),
    ("run_node", tool_run_node, "Run one code or AI step of the built workflow on its own, on a sample or a recorded input, only to find a cause or to design a change. Never connectors, and never the whole workflow: a saved workflow runs only when the user presses Run.",
     {"node_id": str, "sample_input": dict}),
    ("preview_run", tool_preview_run, "Walk through the saved workflow step by step without running it: where a run pauses (approval popups, questions during the run), where each input comes from, and any step that cannot get a value. Use this to answer 'what happens when the user runs it', and never ask the user to run it and describe their screen.", {}),
    ("run_cell", tool_run_cell, "Run a code, connector or browser step for real in the same sandbox a run uses, and record the try. Save the step in the plan, with typed inputs and outputs, before you try it. Give the try the step's name or its id, so its code and result become the step's code and first test; a try that only looks at something gets its own name and a reason, and a try in a step's name that writes none of the step's outputs runs as such a try under its own name. Leave code out to run the step's own code again with new inputs. In the code, use only the functions listed at the top of these instructions: never call your own tools or ai_call there. Saved settings, and secrets named in the code, are filled in for you. inputs gives values for this one try: a sample, a smaller number in place of a setting, or the output of an earlier try: '$recorded' for the output with the same name, '$recorded:name' for another output, '$recorded:har#3' for window 3's HAR file. A value in inputs is not saved as a setting. A file input takes a sample's name or stored file reference, not a path into the app's storage. packages declares the libraries the code imports. A browser step of the plan gives its url in the plan; a browser try that is not a plan step passes browser=true and url. The app opens and closes the window, and asks the user on a card before a window opens, before a send, and before a new folder or host is used. reason is one sentence the user reads: it is required for a try that opens a window or sends again, and shown for a try that only looks at something. A try whose code has not changed since it worked returns its recorded result; pass fresh=true only when the user asked for fresh data. A recorded read is fetched again only when data you need is missing from it: say in missing what is missing. Do not try again something the user said no to; ask_again is only for a really new need, and says why. cells=[{name,code,inputs?,packages?,reason?,browser?,url?},...] runs tries that do not depend on each other together: no try may read the output of another try in the same call. Never put unrelated sends in one call. Changing a step of a built workflow needs the user's approval of its declaration first; a try of a step the approved change does not name adds it to the change, with the try's reason, and shows the user the card again, and the try runs on their click. A try is judged as a run: its inputs and outputs pass the run's own checks, and it gets only the step's declared inputs, so every name its code reads is one of them. A syntax error, an undefined name, a missing library, a wrong call to a function and an undeclared input are all reported in one answer, before the code runs. Returns ok and the output or recorded result, or the error and what is still needed; a card waiting for an answer is not success. A name passed to get_secret must be a stored secret. Never run a step's code again under a new name to get past a refusal: the recorded try belongs to the step's name.",
     {"name": str, "code": str, "inputs": dict, "packages": list,
      "fresh": bool, "reason": str, "missing": str, "ask_again": str,
      "browser": bool, "url": str, "cells": list}),
    ("run_ai_step", tool_run_ai_step, "Try an AI step of the plan for real and record it, with its answer held to the declared outputs by the same check a run uses. name takes the step's name or its id. The prompt of an accepted try becomes the step's prompt when the plan has none. prompt says what to judge; outputs gives the typed outputs, including the item_fields of record and list outputs, before the call. inputs takes file references, sample names and '$recorded' outputs of earlier tries. Use a model the user named; otherwise leave model blank for the configured default. A try whose prompt, inputs and model have not changed returns its recorded answer; pass fresh=true only when the user asked for a fresh answer. Values in a try's inputs are not saved as settings. Returns the accepted output, or the failure or the setup that is missing; an answer of the right shape is not necessarily a right answer.",
     {"name": str, "prompt": str, "model": str, "inputs": dict,
      "outputs": list, "fresh": bool}),

    ("declare_variables", tool_declare_variables, "Save settings the user agreed: entries=[{name,value?,secret?,persistent?,overwrite?}]. Include the real value the user gave, not only its name: a value passed in a try's inputs is not saved. A setting that already holds a different value keeps it, unless you pass overwrite:true after the user confirmed the change. The value is stored in the type of the input that reads it, and a value that cannot have that type is refused. Ask for a secret with ask_user and secret=true; if the user pastes one in the chat, save it with secret:true and never repeat it. A value from an attached environment is read by its name, never copied into the workflow; a plain value for this workflow only, in place of the environment's, needs the user to ask for it and overwrite:true.",
     {"entries": list}),
    ("remove_variables", tool_remove_variables, "Remove workflow settings. Without being asked, remove only blank settings that nothing reads any more (an input you renamed, a value the code now works out). Remove a setting that holds a value only when the user asked for it by name, and then pass confirmed=true. A setting that is the only source of a step's input is refused. names=[...].",
     {"names": list, "confirmed": bool}),
    ("bind_variable", tool_bind_variable, "Record which attached environment supplies a setting, when two attached environments hold the same name (a save reports this as a design gap). environment is the environment's name or id. Never guess: ask_user first, with the environments' names as the options.", {"name": str, "environment": str}),
    ("save_intent", tool_save_intent, "Save the purpose, facts and run instructions before save_plan, adding to what is already saved. summary is what the task is for; facts adds new or corrected facts, and remove_facts removes facts that are out of date. instructions says how to start a run, what the user is asked for during a run and where the results appear; leaving it out keeps the saved instructions. On the first save, description is the short label on the workflow's card on the home page; after that the user owns it. Do not repeat the chat, and never overwrite a lasting decision with a detail from an old sample.",
     {"summary": str, "facts": list, "remove_facts": list,
      "instructions": str, "description": str}),
    ("save_plan", tool_save_plan, "Save the workflow's plan, or a change to it. Call save_intent first. The first save holds the outline: every step, and the lines between the steps that depend on each other; a workflow of one step needs no line. Later saves hold only the steps you changed or added (send a step's id to keep it the same step); steps, lines and the summary you leave out stay as they are. A list of lines (edges) that is not empty replaces all the lines. Name removed steps in changes.removed; replace:true replaces the whole plan on purpose. A line is {src,dst,when?}; steps can be given by name or id, and when reads the outputs of the step the line comes from. Before you work on a built workflow, save only the declaration of the change or fix: changes={modified:[names],added:[names],removed:[names]} with change_note={found,will_change}, or fix_note={went_wrong,will_change} for the open issue. The card this raises asks the user to approve what will change; after their click, work on those steps and save them. A browser or connector step that reads a list names max_setting, its number input that holds the most records it collects, which the user is asked for once. Before the first build, a save that adds or removes steps, or changes a step's kind, after the user approved the plan also needs change_note: it heads the card that asks them again. On any other save change_note is optional and is shown in the chat. A step's recorded try or AI try gives it its code and first test: leave code and tests out unless you change the code on purpose or add a different test case. A code_sketch is not a try. minor_revision is a true one-sentence reason why a change was only mechanical and needs no new real try; never use it for a change in what a step does, or for the step the open issue is about. A user-input step needs per_run_reason; a value that stays the same between runs goes in declare_variables. Returns the saved plan, its design gaps and where approval and the build stand. Fix the gaps it reports before you build.",
     {"plan": steps.PLAN_INPUT_SCHEMA}),
    ("build_workflow", tool_build_workflow, "Build the workflow from the saved plan the user agreed and from the recorded tries; it does not run the workflow. It needs every input and output typed, every design gap and finding fixed, the real values saved as settings, and the user's click on the opening, change or fix card. Every step needs an accepted try, or a recorded reason it was left untried or the user said no, with complete code and tests. A refused build says what to fix; never repeat a send only because the build was refused. An accepted build ends your tool use and shows its progress; its finished card is what the user sees as the result. Never say the build worked only because you asked for it.",
     {"include_proposals": list}),
    ("resolve_issue", tool_resolve_issue, "Fix a problem from a failed run. Call it when the user asks you to fix an issue; pass the open issue's id, or leave it out for the latest. One fix at a time: while a fix is going on in this conversation, save its plan and build it before you start another. The fix starts right after this turn.", {"ticket_id": str}),
    ("close_issue", tool_close_issue, "Close an issue without changing the workflow, when you found that the workflow is right: resolution 'bad-data' (the rejected row was wrong where it came from, and fixing that data is not this workflow's job) or 'fixed-elsewhere' (the cause was outside the workflow and has been dealt with). note is one plain sentence the user reads on the issue row. Before you call it, or save a fix, tell the user which of the three ways out you chose (bad data, change the workflow, fixed elsewhere) and why.",
     {"ticket_id": str, "resolution": str, "note": str}),
    ("ask_user", tool_ask_user, "Ask the user one question about the task that you cannot answer yourself, or collect several setup values on one card. " + ASK_NEEDS_AN_ANSWER + " Options are plain strings, with the one you recommend first; a typed answer that says something counts, but it is never the click that approves the plan. secret=true with secret_name saves a credential straight into the secret store and returns only its name. upload=true asks for a sample file; folder=true opens a folder picker on the user's computer. For up to 8 values that are not secret, use fields=[{name,label,type:'text'|'choice'|'number'|'date'|'boolean',options?,optional?}]; fields cannot be on the same card as secret, upload or folder. Ask only for what is missing, not for every option a guide lists. A value already stored is returned instead of being asked again; allow_stored=true needs a stated reason. Use values from attached environments, or ask the user to attach an environment when that fits. The tool waits for the answer; if it returns a waiting status, the card is left waiting for the user: end the turn without a word and do not ask again. An answer to a setup question still goes where it belongs, a saved setting or a value for each run; a value used for a try never replaces a saved setting.",
     {"question": str, "options": list, "secret": bool, "secret_name": str,
      "upload": bool, "folder": bool, "fields": list, "allow_stored": bool}),
]

AGENT_TOOL_NAMES = [f"mcp__cryogram__{name}" for name, *_ in _AGENT_SPECS]

NODE_WRITE_TOOL_NAMES = [f"mcp__cryogram__{n}" for n in
                         ("build_step", "delete_node", "connect",
                          "disconnect", "validate_node")]
