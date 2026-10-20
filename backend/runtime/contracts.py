# Defines the injected runtime surface and derives signatures and call validation from its functions
import ast
import inspect

HELP = {
    "read_input": "Returns an input's contents, not its blob handle. For files use binary=True for bytes or path=True for a library needing a temporary path. JSON is parsed. No default= argument.",
    "write_output": "Publishes a declared output. Use this even for a probe; print or a trailing expression is not an output.",
    "get_secret": "Takes a stored secret NAME. Never output, log or copy the value into source.",
    "write_file": "Filename FIRST, text/bytes SECOND. Returns a blob: reference; publish that reference on a file output. Example: write_output('csv', write_file('report.csv', csv_text)).",
    "heartbeat": "Reports progress during bounded work; does not extend approval or authorise another attempt.",
    "checkpoint": "Records one confirmed item keyed by stable identity. It cannot close the crash gap after a remote effect; reconcile uncertain writes before retrying.",
    "checkpoints": "Returns a dict {key: result} of the items already done, for skipping them. It is not a list: publish checkpoint_values() on a list output.",
    "checkpoint_values": "Returns the results recorded with checkpoint() as a list. Pass the current input's item keys to get theirs in that order, so items from outside this input are left out. Publish this on a list output.",
    "mark_sent": "Takes NO arguments. Marks a send that has left; it is not an idempotency key or a delivery guarantee.",
    "browser_goto": "Only for a declared browser step/probe, using its supplied page and approved route.",
    "confirm_with_user": "Browser confirmation only; use existing workflow approval/value controls for other pauses.",
    "unexpected_input": "Stops with observed and expected input details; never conceal missing data as an empty successful result.",
    "ai_call": "Runtime-internal AI entry point. Builder exploration uses run_ai_step, never this function in run_cell.",
}

def surface():
    from runtime import capability
    return {name: getattr(capability, name) for name in HELP}

def signature(name):
    sig = inspect.signature(surface()[name])
    return sig.replace(parameters=[p.replace(annotation=inspect.Parameter.empty)
                                   for p in sig.parameters.values()],
                       return_annotation=inspect.Signature.empty)

def reference():
    lines = ["# Runtime contract (generated from this source version)",
             "These functions are injected into cell code: do not import, redefine or stub them. Workflow tools such as save_plan are called as tools, never from Python.",
             "A sample name is a label, blob:... is a stored file reference, contents are data, and a filesystem path is obtained with read_input(..., path=True). Use the ref from read_sample/list_samples in run_cell/run_ai_step inputs."]
    for name in HELP:
        if name != "ai_call":
            lines.append(f"- {name}{signature(name)}: {HELP[name]}")
    return "\n".join(lines)

def check_calls(tree):
    errors = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        name = call.func.id
        if name not in HELP or any(isinstance(a, ast.Starred) for a in call.args) \
                or any(k.arg is None for k in call.keywords):
            continue
        try:
            signature(name).bind(*[None for _ in call.args],
                                 **{k.arg: None for k in call.keywords})
        except TypeError as e:
            errors.append(f"line {call.lineno}: {name}{signature(name)} — {e}")
    return errors
