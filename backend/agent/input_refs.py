# Resolves sample names and retained evidence references consistently for code and AI tries
from agent import steps

def resolve(workflow, inputs, recorded):
    samples = steps.fresh_samples(workflow)
    fixed = {}
    for key, value in (inputs or {}).items():
        if isinstance(value, str):
            if value.startswith((steps.RECORDED_PREFIX, steps.ISSUE_PREFIX)):
                value, error = steps.resolve_recorded(key, value, recorded,
                                                     steps.issue_case_inputs(workflow))
                if error:
                    return {}, error
            else:
                matches = [s for s in samples if s.get("name") == value]
                refs = {s.get("ref") for s in matches if s.get("ref")}
                if len(refs) > 1:
                    return {}, f"input {key!r}: sample name {value!r} is ambiguous; choose its exact ref from list_samples"
                if refs:
                    value = next(iter(refs))
        fixed[key] = value
    return fixed, ""
