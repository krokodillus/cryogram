# Pre-run setup checks: a missing key or model stops the run before it starts, with a plain fix, never mid-run
from __future__ import annotations

import socket
import ssl
from urllib.parse import urlparse

from storage import blobstore
import providers

FILE_WORDS = {"pdf": "a PDF", "image": "an image", "spreadsheet": "a spreadsheet",
              "document": "a Word or PowerPoint file", "text": "a text file",
              "other": "a file"}
FILE_PLURAL = {"pdf": "PDFs", "image": "images", "spreadsheet": "spreadsheets",
               "document": "Word or PowerPoint files", "text": "text files",
               "other": "files of that kind"}

# The one sentence for a file an AI step's model cannot take: what the step gives, who does not read it, the two ways out
def file_unsupported_sentence(kind: str, facts: dict, step: str = "") -> str:
    who = f'The step "{step}" gives' if step else "This step gives"
    model = facts.get("model") or "its model"
    provider = facts.get("provider") or "its provider"
    if kind in ("pdf", "image"):
        return (f"{who} {FILE_WORDS[kind]} to {model}, and {provider} does not read "
                f"{FILE_PLURAL[kind]}. Choose a model that does on the step's panel, or "
                "ask in the chat to add a step that reads the file first.")
    return (f"{who} {FILE_WORDS.get(kind, 'a file')} to {model}, and no AI model reads "
            f"{FILE_PLURAL.get(kind, 'files of that kind')} directly. Ask in the chat to "
            "add a step that reads the file first.")

# Everything a run needs from the machine, checked before any step executes
def check_run(workflow: dict, entry_inputs: dict) -> list[str]:
    problems: list[str] = []
    for name, val in (entry_inputs or {}).items():
        if isinstance(val, str) and val.startswith("blob:") and not blobstore.exists(val):
            problems.append(f"entry input {name!r} references an uploaded file that "
                            "no longer exists in the blob store - upload it again")
    return problems

# Names the actual problem - no key, switched off, not set up - instead of a vague not-set-up
def _model_problem(label: str, model: str, provider_id: str = "",
                   kinds: list | None = None) -> str | None:
    if not model:
        return None if providers.pick_model(kinds or []) else \
            providers.no_pick_sentence(kinds or [], step=label)
    state, prov = providers.node_model_state(model, provider_id)
    pname = prov.get("name") or "its provider"
    if state == "ready":
        return None
    if state == "no-key":
        return (f'The step "{label}" runs on {pname}, which has no API key '
                "yet. Add the key in Admin.")
    if state == "disabled":
        return (f'The step "{label}" runs on {pname}, which is switched off. '
                "Switch it back on in Admin.")
    return (f'The step "{label}" uses an AI model that isn\'t set up under '
            "any workflow provider. Open Admin to add it.")

# Every set value is checked against the port that reads it, with the same machinery the run uses, before the run starts
def check_variables(workflow: dict) -> list[str]:
    from storage import environments
    resolution = environments.resolve(workflow)
    labels = {v.get("name"): v.get("label")
              for v in workflow.get("variables", []) or []}
    problems: list[str] = []
    for name in sorted(environments.workflow_variable_names(workflow) or []):
        r = resolution.get(name)
        if not r or r.get("secret") or r.get("ambiguous"):
            continue
        value = r.get("value")
        if not environments.value_is_set(value):
            continue
        label = labels.get(name) or name.replace("_", " ")
        port = environments.port_for(workflow, name) or {}
        want = environments.port_type_for(workflow, name)
        coerced, understood = environments.coerce_to_type(value, want)
        if not understood:
            problems.append(f'The value for "{label}" isn\'t a {want} - '
                            "fix it on the Inputs tab.")
            continue
        fits, need = environments.value_fits_port(coerced, port)
        if not fits:
            problems.append(f'The value for "{label}" has to be {need} - '
                            "fix it on the Inputs tab.")
    return problems

# Pre-run readiness: only the AI-model config must be ready up front; everything else is collected when a step needs it
def check_all_nodes(workflow: dict) -> list[str]:
    problems: list[str] = []
    for node in workflow.get("nodes", []):
        if node.get("type") != "ai":
            continue
        label = node.get("name") or node.get("id")
        ref = node.get("config", {}).get("model") or {}
        kinds = ((node.get("config") or {}).get("sends_file") or {}).get("kinds") or []
        problem = _model_problem(label, ref.get("model", ""), ref.get("provider_id", ""),
                                 kinds=kinds)
        if problem:
            problems.append(problem)
            continue

        if kinds and ref.get("model"):
            f = providers.model_facts(ref)
            for kind in kinds:
                if kind in ("pdf", "image") and not f.get(f"reads_{kind}"):
                    problems.append(file_unsupported_sentence(kind, f, step=label))
    problems += check_folders(workflow)
    return problems

# A folder a step was allowed to read that no longer exists is a setup problem named before the run starts
def check_folders(workflow: dict) -> list[str]:
    import os
    problems: list[str] = []
    seen: set = set()
    for node in workflow.get("nodes", []):
        for x in (node.get("config") or {}).get("paths") or []:
            p = os.path.expanduser(str(x))
            if p in seen:
                continue
            seen.add(p)
            if not os.path.exists(p):
                label = node.get("name") or node.get("id")
                problems.append(f'The folder "{x}" that the step "{label}" reads '
                                "isn't there any more - put it back, or ask in chat to change the step.")
    return problems

# One step's setup problems, in plain words naming where the fix lives
def check_node(node: dict) -> list[str]:
    if node.get("type") != "ai":
        return []
    label = node.get("name") or node.get("id")
    ref = node.get("config", {}).get("model") or {}
    problem = _model_problem(label, ref.get("model", ""), ref.get("provider_id", ""))
    if problem:
        return [problem]
    ref = providers.resolve({"model": ref.get("model", ""),
                             "provider_id": ref.get("provider_id", "")})
    host = urlparse(ref.get("endpoint") or "https://api.anthropic.com").hostname
    if host and host not in ("localhost", "127.0.0.1") and not _tls_ok(host):
        return [f'The step "{label}" can\'t make a secure connection to the AI '
                "service on this computer. On macOS run 'Install Certificates.command' from your Python folder (or install the certifi package), then retry."]
    return []

_tls_good: set = set()

# One cached TLS probe per host, so a missing-trust-roots problem surfaces before a run dies mid-step
def _tls_ok(host: str) -> bool:
    if host in _tls_good:
        return True
    ctx = providers._SSL_CTX or ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=4) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                _tls_good.add(host)
                return True
    except ssl.SSLCertVerificationError:
        return False
    except Exception:
        _tls_good.add(host)
        return True
