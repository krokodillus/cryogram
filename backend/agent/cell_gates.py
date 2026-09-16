# The run_cell gate table: every rule a cell passes before it executes, in the one order they apply
from __future__ import annotations

import re
from typing import Callable, Optional

import config
import step_types
from runtime import sandbox
from agent import steps
from agent import gate as _gate

class CellCall:
    def __init__(self, workflow: dict, name: str, code: str, inputs, packages,
                 fresh: bool, browser_ok: bool, reason: str, send_ok: bool,
                 missing: str, ask_again: str = "", browser: bool = False,
                 url: str = ""):
        from agent import cells
        self.workflow = workflow
        self.raw_name = name
        self.name = steps.humanise_name(name) if str(name or "").strip() else ""
        self.code = code or ""
        self.inputs = dict(inputs or {})
        from agent import gate as _g

        self._declared_packages = [str(p) for p in (packages or [])]
        self.fresh = bool(fresh)
        self.browser_ok = bool(browser_ok)
        self.reason = str(reason or "").strip()
        self.send_ok = bool(send_ok)
        self.missing = str(missing or "").strip()
        self.ask_again = str(ask_again or "").strip()
        plan_nodes = (workflow.get("plan") or {}).get("nodes") or []
        self.plan_step = next((n for n in plan_nodes if n.get("name") == self.name), None)

        self.step = self.plan_step or next(
            (n for n in workflow.get("nodes") or [] if n.get("name") == self.name), None)
        self.step_id = str((self.step or {}).get("id") or "") or None

        self.url = str(url or "").strip() or str((self.plan_step or {}).get("url") or "").strip()
        self.prior = cells.latest_ok(workflow["id"], self.name, kind="code",
                                     step_id=self.step_id) if self.name else None
        self.prior_out = (self.prior or {}).get("output")
        self.retrievable = not (isinstance(self.prior_out, dict)
                                and "$missing_evidence" in self.prior_out)
        self.repeat = bool(self.prior and self.retrievable)

        self.is_browser = (step_types.may((self.plan_step or {}).get("type"), "browser")
                           if self.plan_step else bool(browser))
        self.packages = _g.required_packages(code or "", self._declared_packages,
                                             is_browser=self.is_browser)
        ps = self.plan_step or {}

        reaches_app = step_types.may(ps.get("type"), "app")
        self.is_write_conn = reaches_app and not ps.get("read_only")
        self.is_read_conn = (reaches_app and bool(ps.get("read_only"))
                             and not self.is_browser)

    def code_key(self, code=None) -> str:
        return steps.code_key(self.code if code is None else code)

def gate_door(c: CellCall) -> Optional[dict]:
    if not c.name:
        return {"error": "name the cell like the step it will become"}
    if not c.code.strip():
        return {"error": "a cell needs code"}
    note = steps.skeleton_first(c.workflow)
    if note:
        return {"error": note}
    if "ai_call" in c.code:
        return {"error": "an AI step is never run as a cell. Declare it in the plan (prompt + model + declared outputs - the output shape is enforced at run time), or try the prompt for real with run_ai_step."}
    if not c.is_browser and re.search("browser_page\\s*\\(|playwright|launch_persistent_context", c.code):
        if c.plan_step:
            return {"error": f"\"{c.name}\" is a {c.plan_step.get('type')} step, "
                             "and a browser window belongs to a BROWSER step the way a model belongs to an AI step. Declare it as type \"browser\" in the plan, with the address it opens, and the window becomes the harness's to open, record and close. A code step works on its declared inputs and local files; a connector reaches another app."}
        return {"error": "a probe cell that opens a window says so: re-send with browser=true. The type is always declared, never read out of the code, because that is what the permission is checked against."}
    called = next((t for t in ("declare_variables", "save_plan", "save_intent",
                               "ask_user", "build_workflow", "bind_variable",
                               "set_deliverables", "run_cell", "run_ai_step")
                   if re.search(rf"\b{t}\s*\(", c.code)), None)
    if called:
        return {"error": f"{called} is a workflow TOOL, not cell code - call "
                         "it directly as a tool from your reply. Cell code speaks only read_input / write_output / get_secret / write_file / heartbeat / checkpoint plus plain Python."}
    if re.search("^\\s*(import|from)\\s+(capability|cryogram\\w*|agent\\b|runtime\\b)", c.code, re.M):
        return {"error": "read_input / write_output / get_secret / write_file / heartbeat / checkpoint / browser_profile are provided DIRECTLY in the cell - call them as plain functions, never import them (there is no module to import)."}
    return None

def gate_typed_ports(c: CellCall) -> Optional[dict]:
    if not c.plan_step:
        return None
    untyped = steps.untyped_ports(c.plan_step)
    if untyped:
        return {"error": f'"{c.name}" has ports with no type yet '
                         f"({', '.join(untyped)}): {steps.UNTYPED_PORTS}"}
    return None

def gate_fixtures(c: CellCall) -> Optional[dict]:
    from agent import cells
    samples = {s.get("name"): s.get("ref") for s in steps.fresh_samples(c.workflow)}
    recorded = cells.latest_outputs(c.workflow["id"])
    fixed: dict = {}
    for k, v in c.inputs.items():
        if isinstance(v, str):
            if v.startswith((steps.RECORDED_PREFIX, steps.ISSUE_PREFIX)):
                v, err = steps.resolve_recorded(k, v, recorded, steps.issue_case_inputs(c.workflow))
                if err:
                    return {"error": err}
            elif v in samples:
                v = samples[v]
            elif "/blobs/" in v or str(config.DATA_DIR) in v:
                return {"error": f"input {k!r} is a raw file path - pass the "
                                 "sample's name or blob ref from list_samples (or \"$recorded\" to chain from an earlier cell); a cell must run exactly the way a real run will"}
        fixed[k] = v
    c.inputs = fixed

    if steps.seed_file_kinds(c.workflow, c.name, fixed):
        steps.save(c.workflow)
    return None

def gate_rename_signature(c: CellCall) -> Optional[dict]:
    from agent import cells
    if c.step is not None or len(c.code_key()) <= steps.RENAME_MATCH_MIN:
        return None
    candidates = list((c.workflow.get("plan") or {}).get("nodes") or []) \
        + list(c.workflow.get("nodes") or [])
    for st in candidates:
        sn = str(st.get("name") or "")
        rec = cells.latest_ok_for(c.workflow["id"], st, kind="code") if sn else None
        if rec and c.code_key(rec.get("code")) == c.code_key():
            return {"error": "this code already ran as the step "
                             f'"{sn}" and its run is recorded there - '
                             "never re-run a step's code under a new name (a renamed cell orphans the recorded "
                             f'evidence). Work under "{sn}" itself, or '
                             "read its recording by chaining \"$recorded:<output>\"."}
    return None

def gate_replay(c: CellCall) -> Optional[dict]:
    if not (c.prior and not c.fresh and c.retrievable
            and c.code_key(c.prior.get("code")) == c.code_key()):
        return None
    same_inputs = c.prior.get("inputs") == c.inputs
    return {"ok": True, "cell": c.name, "output": c.prior_out,
            "duplicate": True,
            "note": "this cell's code already ran OK and is recorded - NOT re-run; this is the recorded result"
                    + ("" if same_inputs else
                       " (your new inputs were NOT executed)")
                    + ". It carries into the build as the step's code and first test; later steps chain it with \"$recorded\". Re-run only for a real reason: change the code for a changed step, or pass fresh=true ONLY when the user asked for fresh live data."}

def gate_probe_shadow(c: CellCall) -> Optional[dict]:
    if not (c.step is not None and c.prior and c.retrievable):
        return None
    known = {str(k) for k in (c.prior_out or {}) if not str(k).startswith("$")}
    known |= {str(p.get("name")) for p in (c.step.get("outputs") or [])
              if isinstance(p, dict) and p.get("name")}
    written, dynamic = _gate.write_output_names(c.code)
    if known and written and not dynamic and not (written & known):
        return {"error": f'this cell carries the step name "{c.name}" but '
                         "writes none of that step's outputs ("
                         + ", ".join(sorted(known))
                         + ") - recorded under the step's name it would REPLACE the step's real evidence. A diagnostic or probe takes its OWN plain name (\"check ...\"); the step's cell writes the step's outputs."}
    return None

def gate_decline(c: CellCall) -> Optional[dict]:
    if steps.is_declined(c.workflow, c.step or c.name) and not c.ask_again:
        return {"error": f'the user already declined trying "{c.name}" live - '
                         "the step counts as done and the build accepts it as it stands. Do not re-run, re-plan or mention it again. If it has become genuinely necessary, re-send with ask_again=\"<one sentence: why it matters now>\" and they will be asked once more; a no settles it for good."}
    return None

def gate_proposal_first(c: CellCall) -> Optional[dict]:
    if not c.workflow.get("nodes") or not c.name:
        return None
    built = next((n for n in c.workflow["nodes"] if n.get("name") == c.name), None)
    if built is None and c.plan_step is None:
        return None
    if built is not None and \
            c.code_key((built.get("config") or {}).get("code")) == c.code_key():
        return None

    why = steps.change_scope_refusal(c.workflow, c.name)
    return {"error": why} if why else None

_LAUNCH = re.compile(r"launch_persistent_context\(|chromium\.launch\(")

def gate_capture_everything(c: CellCall) -> Optional[dict]:
    if not (c.is_browser and _LAUNCH.search(c.code)):
        return None
    from agent import gate as _g
    return {"error": _g.check_recording(c.code)[0] + " Then re-send."}

def gate_browser_card(c: CellCall) -> Optional[dict]:
    if not (c.is_browser and not c.browser_ok):
        return None

    step = c.plan_step or c.step
    if step is not None:
        gaps = (step_types.function("browser", "declaration_gaps") or (lambda *a: []))(
            c.name, {**step, "url": c.url})
        if gaps:
            return {"error": "; ".join(gaps) + " - add it to the plan step with save_plan, then run the cell again."}
    elif not c.url.lower().startswith(("http://", "https://")):
        return {"error": "a probe browser cell passes the address its window opens: re-send with url=\"https://...\"."}
    if not c.reason:
        return {"error": "opening the real browser needs the user's go-ahead, and the card must say WHY: re-send with reason=\"<one sentence: what this window is for>\". If you are only fixing parsing or shaping, no browser is needed - use a plain code cell chaining \"$recorded\"; every earlier window's recording is readable by its number (\"$recorded:har#<n>\")."}
    site = _site_of(c.url)
    return {"error": "opening the real browser needs the user's go-ahead. Never rename the cell to get past this: renames orphan the step's recorded evidence.",
            "needs_browser_ok": c.name,
            **({"domain": site} if site else {}),
            **({"sends": True} if c.step and step_types.writes_outside(c.step) else {})}

# The site a window opens, as the card names it
def _site_of(url: str) -> str:
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "") if url else ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host

def gate_send_card(c: CellCall) -> Optional[dict]:
    if not (c.is_write_conn and not c.is_browser and not c.send_ok):
        return None
    return {"error": "sending this for real needs the user's go-ahead - the approval card asks; a no there settles the step (it counts as done and the build goes ahead). Never pre-ask this with ask_user, never rename the cell.",
            "needs_send_ok": c.name,
            "send_repeat": c.repeat,

            "code_key": c.code_key(),
            "impact": str((c.plan_step or {}).get("external_impact") or "")}

def gate_read_connector(c: CellCall) -> Optional[dict]:
    if not (c.is_read_conn and c.repeat and not c.fresh and not c.missing):
        return None
    from agent import cells
    mine = next((e for e in cells.inventory(c.workflow["id"]) if e.get("name") == c.name), None)
    outs = ", ".join((mine or {}).get("outputs") or []) or "?"
    return {"error": "no live call: this step already has a recorded "
                     f"run (outputs: {outs}) - iterate on the recording "
                     "in a plain code cell chaining \"$recorded:<name>\" instead of re-fetching. Run live only for data the recordings lack or a genuinely changed fetch: re-send with missing=\"<one sentence: what the recordings do not contain>\", or fresh=true when the user asked for fresh data. Never rename the cell - renames orphan the step's recorded evidence."}

def gate_packages(c: CellCall) -> Optional[dict]:
    from storage import deps
    if not c.packages:
        return None
    errs = deps.validate(c.packages)
    if errs:
        return {"ok": False, "errors": errs}
    inst = deps.install(c.workflow["id"], c.packages)
    if inst.get("error"):
        return {"ok": False, "error": inst["error"]}
    return None

def gate_code_check(c: CellCall) -> Optional[dict]:
    from storage import deps
    problems = _gate.check_syntax(c.code)
    if problems:
        return {"error": "the code cannot run as written: " + "; ".join(problems)
                         + " - fix it and run the cell again"}
    problems = list(_gate.check_undefined_names(c.code))
    if problems:
        problems.append("the cell surface is read_input / write_output / get_secret / write_file / heartbeat / checkpoint / checkpoints / mark_sent / unexpected_input, in a browser cell `page` with browser_goto / confirm_with_user, plus plain Python; a recorded value is read with read_input after chaining it as \"$recorded:<name>\" in inputs")
    roots = _gate.import_roots(c.code)
    if roots:
        for m in deps.missing_modules(c.workflow["id"], roots,
                                      app_packages=c.is_browser):
            hint = _gate.PACKAGE_FOR_IMPORT.get(m, m)
            problems.append(f"`{m}` is imported but neither declared in `packages` "
                            "nor installed in this workflow's environment - add "
                            f"{hint!r} to packages")
    if problems:
        return {"error": "the code cannot run as written: " + " | ".join(problems)
                         + " - every problem is listed; fix them all and run the cell again"}
    return None

CELL_GATES: list[Callable[[CellCall], Optional[dict]]] = [
    gate_door,
    gate_typed_ports,
    gate_fixtures,
    gate_rename_signature,
    gate_replay,
    gate_probe_shadow,
    gate_decline,
    gate_proposal_first,
    gate_capture_everything,

    gate_packages,

    gate_code_check,
    gate_browser_card,
    gate_send_card,
    gate_read_connector,
]

# While a browser cell is running, what it needs the person to do reaches them as a card
def _window_request(workflow: dict):
    from agent import actions
    def ask(message: str) -> str:
        decision = actions._approve(
            workflow, lambda ev: None, "Something for you to do",
            str(message or "The step is waiting for you in the browser window."),
            "The window stays open while you do it - it carries on the moment you confirm.")
        if decision in ("allow", "always"):
            return "answered"
        return "unanswered" if decision is None else "stopped"
    return ask

# Runs the cell in the production sandbox and records the run under the step's name
def execute(c: CellCall) -> dict:
    import time as _time
    from agent import cells
    from storage import deps
    from storage import environments as _envs
    from runtime import executor as _exec
    from agent import gate as _gate
    from agent import turnstate
    workflow, name, code = c.workflow, c.name, c.code

    resolution = _envs.resolve(workflow)
    secret_map = _envs.secret_owners(workflow)
    secret_vals, unset = {}, []
    for n in sorted(_exec._secret_literals(code)):
        v = _exec._secret_val(n, None, secret_map)
        if v is None:
            unset.append(n)
        else:
            secret_vals[n] = v
    if unset:
        return {"ok": False, "cell": name,
                "error": "no stored value called "
                         + ", ".join(f'"{n}"' for n in unset)
                         + " in this workflow or its attached environments - declare it (declare_variables, secret:true) and collect it with the masked ask, or tell the user to attach the environment that holds it."}

    inputs = dict(c.inputs)
    declared_names = ({str(p.get("name")) for p in c.plan_step.get("inputs") or []}
                      if c.plan_step else None)
    for n in sorted(_gate.read_input_names(code)):
        r = resolution.get(n)
        if n in inputs or not r or r.get("secret") or r.get("ambiguous"):
            continue
        if declared_names is not None and n not in declared_names:
            continue
        if _envs.value_is_set(r.get("value")):
            inputs[n] = r["value"]

    from runtime import port_checks as _ports
    if c.plan_step:
        problems = _ports.check_ports(c.plan_step.get("inputs") or [], inputs)
        if problems:
            return {"ok": False, "cell": name,
                    "error": "the inputs do not match what this step declares: "
                             + "; ".join(_ports.problem_lines(problems)),
                    "note": "fix the value on the Inputs tab (or with declare_variables) or the declared type on the plan step - never convert it inside the step's code; a run receives values in their declared type"}

    plan_domains = {str(d) for n in (workflow.get("plan") or {}).get("nodes") or []
                    for d in (n.get("domains") or [])}
    allow = sorted(set(workflow.get("egress_allowlist") or []) | plan_domains)
    t0 = _time.time()
    try:
        declared = sandbox.clamp_timeout((c.plan_step or {}).get("timeout_seconds"))
        out = sandbox.run(code, None, inputs, secret_vals,
                          python_exe=deps.python_for(workflow["id"]),
                          profile_dir=str(config.browser_profile_dir(workflow["id"])),
                          extra_allowlist=allow,

                          network=(step_types.may(c.plan_step["type"], "network")
                                   if c.plan_step else not c.is_browser),

                          path_roots=sandbox.workflow_roots(
                              workflow, (c.plan_step or {}).get("paths") or []),
                          timeout=declared,
                          checkpoint_file=str(config.checkpoint_file(
                              f"cell_{workflow['id']}_{name}")),
                          should_stop=lambda: steps.turns_should_stop(workflow),
                          on_progress=steps.cell_progress(workflow["id"]),

                          on_user_request=(_window_request(workflow)
                                           if c.is_browser else None),

                          keep_captures=c.is_browser,

                          browser=c.is_browser,
                          browser_url=c.url,
                          browser_options=dict((c.plan_step or {}).get("browser_options") or {}),
                          output_ports=(c.plan_step or {}).get("outputs") or [])
        ok, result = True, {"ok": True, "output": out}

        if c.plan_step:
            problems = _ports.check_ports(c.plan_step.get("outputs") or [], out,
                                          require_all=True)
            if problems:
                ok, result = False, {
                    "ok": False, "output": out,
                    "error": "the result did not match what this step declares: "
                             + "; ".join(_ports.problem_lines(problems)),
                    "note": "the try is judged as a run: make the step produce the value, or if it may genuinely be empty some runs mark that output optional on the plan step"}

        if c.is_browser:
            from agent import cells as _cells
            kept = sorted(_cells.capture_refs(out))
            if kept:
                result["captured"] = kept
    except sandbox.NodeStopped:
        return {"ok": False, "error": "stopped by the user", "cell": name,
                "seconds": round(_time.time() - t0, 1)}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        ok, result = False, {"ok": False, "error": msg}
        detail = getattr(e, "detail", None)
        detail = detail if isinstance(detail, dict) else {}

        if c.is_browser:
            result["window_opened"] = bool(detail.get("window_opened"))
        saw = detail.get("saw")
        if saw:
            result["saw"] = saw
        low = msg.lower()

        if isinstance(e, sandbox.NodeError) and ("NameError" in msg or "is not defined" in msg):
            result["note"] = ("that name does not exist in a cell. The cell surface is read_input / write_output / get_secret / write_file / heartbeat / checkpoint / checkpoints / mark_sent / unexpected_input, in a browser cell `page` with browser_goto / confirm_with_user, plus plain Python; a recorded value is read with read_input after chaining it as \"$recorded:<name>\" in inputs.")

        if "modulenotfounderror" in low or "no module named" in low \
                or "is not installed" in low or "please install" in low:
            result["note"] = ("a library this code imports is not in the cell's environment - pass it in `packages` (e.g. packages=[\"requests\"]) and run the cell again; the built step keeps the same list.")

        if isinstance(e, sandbox.NodeError) and "<the contents of input" in msg \
                and ("File name too long" in msg or "No such file" in msg):
            result["note"] = (
                "read_input(name) returns the input's CONTENTS - a recording comes back already parsed (a dict for JSON, text otherwise), so work on it directly; a library that wants a file path gets one with read_input(name, path=True).")
        if "path blocked" in msg:
            result["note"] = (
                "this workflow may only read the folders it declares or the user allowed. If the step genuinely needs that folder, add it to the step's plan `paths` and save_plan, then run the cell again - the user is asked once and it stays allowed for this workflow. A file the workflow works on is a `file`/`filepath` setting read with read_input, never a path in code.")
        if "egress blocked" in msg:
            host = (re.search(r"\('([^']+)'", msg) or [None, None])[1]
            result["note"] = (
                f"this workflow may only reach declared hosts. Add {host!r} to "
                "this step's plan `domains` and save_plan, then run the cell again - the user sees and approves the domains when they build."
                if host else
                "declare the host this step reaches in its plan `domains`, save_plan, then run the cell again.")
    n = cells.record(workflow["id"], name, code, dict(c.inputs),
                     result.get("output"), ok, _time.time() - t0, c.packages,
                     step_id=c.step_id, reason=c.reason)

    if ok and result.get("captured"):
        num = len(cells.windows(workflow["id"]))
        result["window"] = num
        result["captured_note"] = (
            f"this was window {num}" + (f" ({c.reason})" if c.reason else "")
            + ". It kept " + ", ".join(result["captured"]) + ". Read any of "
            f"them in the next cell with \"$recorded:<name>#{num}\" "
            "(\"$recorded:<name>\" is always the newest window); read_input returns the recording already parsed, and read_input(name, path=True) gives a file path when a library wants one - the traffic (har) holds every request and response, the page (page) is the document as the window left it, and the listing (calls) is one line per call with its id; the listing on this result is folded (repeats of one address in one row, other kinds as counts), and read_input(\"calls\", kind=\"image\") or ids=[...] unfolds any row in full (url=, status=, method= filter the same way, on har too). Every earlier window's recording stays readable by its number until the build is done, so compare them before opening the page again.")
    result.update({"cell": name, "runs": n, "seconds": round(_time.time() - t0, 1)})
    if ok:
        if c.prior and c.code_key(c.prior.get("code")) != c.code_key():
            result["note"] = ("recorded - this CHANGED code replaces the earlier proof as the step's code and first test. If the user already accepted this step, tell them in one line what changed and why it was re-tested.")
        else:
            result["note"] = ("recorded - this code and this run are now ATTACHED to the same-named plan step as its code and first test. Do NOT repeat either in save_plan: leave `code` and `tests` out for this step and they are filled in for you.")
        shape_note = steps.shape_question(result.get("output"))
        if shape_note:
            result["shape"] = shape_note
    return result

_CELL_KEYS = ("name", "code", "inputs", "packages", "fresh", "browser_ok", "reason",
              "send_ok", "missing", "ask_again", "browser", "url")

# The cells of one call that read another cell of the same call, named with what they read
def _dependent_cells(workflow: dict, specs: list) -> list[str]:
    plan_nodes = (workflow.get("plan") or {}).get("nodes") or []
    outputs_of = {}
    for spec in specs:
        step = next((n for n in plan_nodes if n.get("name") == spec.get("name")), None)
        outputs_of[spec.get("name")] = {str(p.get("name")) for p in (step or {}).get("outputs") or []}
    out = []
    for spec in specs:
        for k, v in (spec.get("inputs") or {}).items():
            if not (isinstance(v, str) and v.startswith(steps.RECORDED_PREFIX)):
                continue
            wanted = v[len(steps.RECORDED_PREFIX):].lstrip(":").split("#", 1)[0] or k
            for other, outs in outputs_of.items():
                if other != spec.get("name") and wanted in outs:
                    out.append(f'"{spec.get("name")}" reads {wanted!r} from "{other}"')
    return out

# Several independent cells in one call: every gate in order for each, then the runs together, browser cells one at a time
def run_many(workflow: dict, specs) -> dict:
    import concurrent.futures as _cf
    if not isinstance(specs, list) or not specs:
        return {"error": "cells must be a list of cells: [{name, code, inputs?, ...}, ...]"}
    bad = [i for i, s in enumerate(specs)
           if not isinstance(s, dict) or not str(s.get("name") or "").strip() or not str(s.get("code") or "").strip()]
    if bad:
        return {"error": f"every cell needs a name and code (cells {bad} lack one)"}
    unknown = sorted({k for s in specs for k in s if k not in _CELL_KEYS})
    if unknown:
        return {"error": f"a cell of the call takes the single call's arguments only; not {unknown}"}
    names = [str(s["name"]) for s in specs]
    if len(set(names)) != len(names):
        return {"error": "each cell of one call has its own name; the same name twice is one cell"}
    dependent = _dependent_cells(workflow, specs)
    if dependent:
        return {"error": "cells in one call must not read each other's outputs: "
                         + "; ".join(dependent) + " - a cell that needs another's result runs after it, in its own call"}
    calls = [CellCall(workflow, str(s["name"]), str(s["code"]), s.get("inputs"),
                      s.get("packages"), bool(s.get("fresh")), bool(s.get("browser_ok")),
                      str(s.get("reason") or ""), bool(s.get("send_ok")),
                      str(s.get("missing") or ""), str(s.get("ask_again") or ""),
                      bool(s.get("browser")), str(s.get("url") or ""))
             for s in specs]
    results: list = [None] * len(calls)
    runnable: list = []
    for i, c in enumerate(calls):
        answer = None
        for g in CELL_GATES:
            answer = g(c)
            if answer is not None:
                break
        if answer is not None:
            results[i] = {**answer, "cell": c.name}
        else:
            runnable.append(i)
    together = [i for i in runnable if not calls[i].is_browser]
    windows = [i for i in runnable if calls[i].is_browser]
    if together:
        with _cf.ThreadPoolExecutor(max_workers=len(together)) as pool:
            for i, r in zip(together, pool.map(lambda j: execute(calls[j]), together)):
                results[i] = r
    for i in windows:
        results[i] = execute(calls[i])
    ok = all(isinstance(r, dict) and r.get("ok") for r in results)
    return {"ok": ok, "results": results,
            "note": (f"{len(results)} cells ran in one call; each result is under its "
                     "own cell name, in the order given")}

# One cell call: the gates in order, then the run
def run(workflow: dict, name: str, code: str, inputs=None, packages=None,
        fresh: bool = False, browser_ok: bool = False, reason: str = "",
        send_ok: bool = False, missing: str = "", ask_again: str = "",
        browser: bool = False, url: str = "") -> dict:
    c = CellCall(workflow, name, code, inputs, packages, fresh, browser_ok,
                 reason, send_ok, missing, ask_again, browser, url)
    for g in CELL_GATES:
        answer = g(c)
        if answer is not None:
            return answer
    return execute(c)
