# The AI step: asks a model, with the answer held to the step's declared outputs
from __future__ import annotations

import time

TYPE = "ai"

MAY = {"code": False, "network": False, "app": False,
       "browser": False, "model": True, "process": False}

RECEIPT = "request-assembly"
EVIDENCE = "ai-try"
BAR = "prompt + model + enforced schema + authored example"

# A plan finding when the step has no prompt
def missing_prompt(name: str, node: dict) -> list[str]:
    if str(node.get("prompt") or "").strip():
        return []
    return [f'the "{name}" step is an AI step with no prompt - '
            "say what you are asking the model to do"]

# A plan finding when no output has a type, or every list has no item shape, so no answer shape is enforced
def answer_shape_unenforced(node: dict) -> list[str]:
    outs = node.get("outputs") or []
    if not (outs and all(str(o.get("type") or "") in ("", "list")
                         and not o.get("item_fields") for o in outs)):
        return []
    return [f'step "{node.get("name")}": its AI answer shape is unenforced - '
            "no output has a type, or a list has no item shape. Declare the real fields (name, concrete type, description each), or for a LIST output give the port item_fields: [{name, type, description}] so every item is held to that shape - one enforced try is then enough"]

# A plan finding when every output is a list, so an empty answer cannot be told from a failed call
def every_output_is_a_list(node: dict) -> list[str]:
    outs = node.get("outputs") or []
    if not (outs and all(o.get("item_fields") for o in outs)):
        return []
    return [f'step "{node.get("name")}": every output is a list - an empty '
            "answer would be indistinguishable from a failed call. Add one short text output (e.g. \"note\" - description: one line on what was judged, and why the list is empty when it is) so the model always states its outcome"]

# A note for the Builder Agent naming output fields that have no description
def undescribed_output_fields(node: dict) -> list[str]:
    bare = [p.get("name") for p in node.get("outputs") or []
            if not str(p.get("description") or "").strip()]
    if not bare:
        return []
    return [f'"{node.get("name")}": output field(s) '
            f"{', '.join(bare)} have no description - each "
            "field should say what goes in it (the format is enforced from these, so the prompt never describes it)."]

# The step's outputs: the model's answer, held to the declared outputs
def run(node: dict, inputs: dict, entry: dict, capability) -> tuple:
    cfg = node.get("config", {})
    t0 = time.monotonic()
    out = capability.ai_call(cfg.get("prompt", ""), cfg.get("model", {}), inputs,
                             output_ports=node.get("outputs", []),
                             max_tokens=capability.step_max_tokens(node),
                             timeout=capability.step_ai_timeout(node))
    if isinstance(out, dict) and out.get("_unparsed"):
        out["_elapsed_s"] = round(time.monotonic() - t0, 1)
    return (out, False)

# After the call, take the model usage it spent and hand it to be recorded
def record_usage(node: dict, capability, record) -> None:
    usage = capability.take_ai_usage()
    if usage:
        record(usage)

# What a failed model call means for the run: a stop with its reason, a pause, or nothing
def read_call_failure(node: dict, raw, capability) -> dict | None:
    if not (isinstance(raw, dict) and raw.get("_unparsed")):
        return None
    kind = str(raw.get("error_kind") or "")
    detail = str(raw.get("_text") or "")[:300]
    elapsed = raw.get("_elapsed_s")
    if kind == "file-unsupported":
        return {"halt": {"verdict": {"file": [str(raw.get("_text") or "")]},
                         "reason": "file-unsupported"},
                "ai_failure": None, "elapsed": elapsed}
    if kind == "auth":
        return {"pause": {"reason": "environment-check-failed", "lines": [
            "the AI step could not sign in to its model "
            f"provider - {detail}. Check the provider's API "
            "key in Admin, then retry from this step."]}}
    if kind == "max-tokens":
        room = capability.step_max_tokens(node)
        return {"halt": {"verdict": {"ai_call": [
                    "the AI step's answer was cut off at "
                    f"{room:,} tokens, the most this step "
                    "allows. \"Fix it\" can give the step more room or make it return less."]},
                         "reason": "ai-answer-truncated", "output": raw},
                "ai_failure": "truncated", "elapsed": elapsed}
    if kind == "timeout":
        budget = capability.step_ai_timeout(node)
        return {"halt": {"verdict": {"ai_call": [
                    "the AI model didn't answer within "
                    f"{budget} seconds, the most this step "
                    "waits, usually because the step asks for too much in one go. \"Fix it\" can split the work or give it more time."]},
                         "reason": "ai-timed-out", "output": raw},
                "ai_failure": "timed-out", "elapsed": elapsed}
    if kind == "transport" or kind == "http-429" or kind.startswith("http-5"):
        return {"pause": {"reason": "model-unavailable", "lines": [
            "the AI step didn't get an answer - the model service was unavailable or overloaded. Nothing is wrong with the workflow - run again from this step."]}}
    return {"halt": {"verdict": {"ai_call": [f"the AI step didn't answer: {detail}"]},
                     "reason": "ai-call-failed", "output": raw},
            "ai_failure": "call-failed", "elapsed": elapsed}

# A stop when every field of the answer came back empty, or None
def blank_answer(node: dict, output: dict) -> dict | None:
    ports = node.get("outputs") or []
    if len(ports) < 2:
        return None

    def _blank(v):
        return v is None or v == {} or v == [] or (isinstance(v, str) and not v.strip())
    if not all(_blank(output.get(p["name"])) for p in ports):
        return None
    return {"halt": {"verdict": {"ai_call": [
                "the model returned empty answers for every field - usually a refusal or a prompt/schema mismatch, not a real result"]},
                     "reason": "ai-answered-blank", "output": output},
            "ai_failure": "blank", "elapsed": None}

# The failure mode an answer that missed its declared shape is filed under
def output_check_failure_mode(node: dict) -> str:
    return "wrong-shape"
