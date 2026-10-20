# Checks recorded settings, dependencies and file artifacts before assembling a workflow
import csv
import io
import json
from pathlib import PurePath

from agent import cells
from runtime import env_checks
from storage import blobstore, environments

FORMAT_CHECK_BYTES = 8 * 1024 * 1024

def artifact_problem(ref, owner):
    if not isinstance(ref, str) or not ref.startswith("blob:"):
        return "publish the reference returned by write_file(filename, contents)"
    if not blobstore.exists(ref):
        return "the recorded file is no longer in the blob store; produce it again"
    meta = blobstore.stat(ref, owner=owner)
    suffix = PurePath(str(meta.get("name") or "")).suffix.lower()

    if suffix not in (".csv", ".json", ".pdf"):
        return ""
    if meta.get("size", 0) > FORMAT_CHECK_BYTES:
        return ""
    data = blobstore.get(ref)
    try:
        if suffix == ".json":
            json.loads(data)
        elif suffix == ".pdf":
            if not data.startswith(b"%PDF-"):
                return "the .pdf output has no PDF header"
        else:
            if data.startswith((b"\xff\xfe", b"\xfe\xff")):
                contents = data.decode("utf-16")
            else:
                try:
                    contents = data.decode("utf-8-sig")
                except UnicodeError:
                    contents = data.decode("cp1252")
            if "\x00" in contents:
                return "the .csv output contains binary NUL bytes"
            rows = csv.reader(io.StringIO(contents), strict=True)
            width = None
            for row in rows:
                if not row:
                    continue
                width = len(row) if width is None else width
                if len(row) != width:
                    return "the .csv output has inconsistent column counts"
    except (ValueError, UnicodeError, csv.Error):
        return f"the {suffix} output does not parse as its declared file format"
    return ""

def check(workflow, plan):
    nodes = plan.get("nodes") or []
    virtual = {**workflow, "nodes": nodes}
    problems = env_checks.check_variables(virtual)
    resolution = environments.resolve(virtual)
    produced = {p.get("name") for n in nodes for p in n.get("outputs") or []}
    persistent = {v.get("name") for v in workflow.get("variables") or []
                  if v.get("persistent", True) and not v.get("secret")}
    owner = blobstore.workflow_owner(workflow["id"])
    for node in nodes:
        if node.get("type") not in ("code", "connector", "browser", "ai"):
            continue
        rec = cells.latest_ok_for(workflow["id"], node,
                                  kind="ai" if node.get("type") == "ai" else "code")
        if not rec:
            continue
        label = node.get("name") or node.get("id")
        for port in node.get("inputs") or []:
            name = port.get("name")
            r = resolution.get(name) or {}
            if (name not in produced and name in persistent and not r.get("secret")
                    and not port.get("optional") and port.get("type") != "secret"
                    and environments.value_is_set((rec.get("inputs") or {}).get(name))
                    and not environments.value_is_set(r.get("value"))):
                if port.get("type") == "file":
                    problems.append(f'{label}: the file "{name}" exists only in the test '
                                    "inputs. If each run works on a different file, add a user-input step that asks for it on each run; if it is the same file on most runs (a template, a document of criteria), store it as the setting's value.")
                else:
                    problems.append(f'{label}: setting "{name}" exists only in the test inputs. '
                                    "Store the agreed production value (not a test override), or declare a genuine per-run user input.")

        declared = node.get("packages")
        if declared:
            missing = sorted(set(rec.get("packages") or []) - set(declared))
            if missing:
                problems.append(f"{label}: retain the tested dependencies {missing}, "
                                "or test the deliberate dependency change.")
        for port in node.get("outputs") or []:
            name = port.get("name")
            outputs = rec.get("output") or {}
            if port.get("type") != "file" or (name not in outputs and port.get("optional")):
                continue
            problem = artifact_problem(outputs.get(name), owner)
            if problem:
                problems.append(f'{label}: output "{name}": {problem}')
    return problems
