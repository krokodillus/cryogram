---
name: 62-ai-practice
description: WHEN designing or fixing an AI step (per-run judgement - summarise, classify, extract, draft) - what to include, how to structure it, what to test, how to tune
tier: 2
kind: action
depends_on: [07-what-a-plan-contains]
version: 2.2.0
last_updated: 2026-09-10
---

# AI steps: the practice

An AI step earns its place only where genuine per-run judgement is needed; everything deterministic around it (parsing, formatting, filing) is code. The user's verb decides: summarise, classify, draft, prioritise and judge are AI steps, and you never approximate one with code to avoid it, because a worse result is never a saving. The plan save tells you when a judgement request has no AI step.

## What to include

The prompt and the fields divide the work.

- The prompt describes the task and its context: what the input is, what judgement to apply, the domain rules that bear on it. It never describes the output format: no field lists, no instruction to return JSON, no examples of the shape. The declared output fields are enforced structurally when the model is called, so format text in the prompt is dead weight that drifts out of sync.
- Every output field carries all three of a name (snake case is fine, fields are code-facing), a type and a description saying exactly what goes in it. The description travels into the enforced schema; it is where extraction precision lives, and where to find the value in the source belongs there too.
- Closed choices (classification labels) are enum fields, so the model structurally cannot invent a label.
- A list output (a batch judgement, one record per item) declares item_fields on the port as [{name, type, description}]; the enforced schema then holds every item to that shape, enums included. Never a bare "any" list: with item_fields one try confirms the shape.
- Return the judgement, never a copy of the input. Per item, one field that identifies it (an id, a url) plus what you decided. Never ask the model to re-emit text it was just given (a title, a body, a whole row): the original is already in the run, and a later code step joins it back by that id. Echoing input overflows the answer room; the save flags it.
- If a step genuinely needs a long answer, say so on the step (`max_tokens`) rather than trimming what it decides.
- A judgement step declares a way to say that nothing was suitable: outputs that are only lists are a design gap at save. Add one short text field, a one-line note of what was judged and why the list is empty when it is, so a blank answer never masquerades as a quiet day.

## Files an AI step reads

The models line names what each model reads directly. On a model that reads the file's type, the file goes to the AI step whole, never extracted or OCR'd first; on a model that does not, a code step before it turns the file into what the AI step takes, and which conversion (the pages as images, text extraction, OCR) depends on what the workflow needs from the file. Load "62-ai-practice/files" when a step reads a PDF, an image or an office file: it carries the choice and the reasons.

## How to structure it

- One judgement per step. Classify and extract and summarise is three steps, or one deliberately combined one; decide, and do not drift into it.
- Inputs carry exactly the material the judgement needs, never a whole document when a section does.

## Temperature, set per step

Default to 0 and raise it only when sameness is the problem, saying why in one line when you announce the step. 0 is for anything with a right answer (classifying, extracting, scoring, routing, matching, pulling fields out of a document) and for anything whose output feeds a later step's logic: same input, same answer, and a regression test that means something. 0.2 to 0.3 is for judgement with no single right wording (a one-line reason, a short summary, a relevance call), still steady enough to test. 0.7 to 1.0 only where variety is the goal (drafting distinct options, wording a message that would read as canned if identical every time); the recorded example is then one of many valid answers, so never assert exact text. Never raise it to fix a bad answer, which is a prompt, model or input problem: a higher temperature makes a wrong answer less repeatable, not more right.

A non-default temperature or answer room (`max_tokens`) is the user's to accept: state it in one plain line with the plan. The card shows it, and their approval is the informed yes.

## What to test

- The authored example, never replayed because a replay is a live model call: one real input with the accepted output, seeded from the exploration try (run_ai_step records it). It documents what right looks like.
- The enforced schema is the run-time shape guard. Add criteria only for what is structurally certain (a rule the user stated, or an arithmetic fact from the evidence), each with a human label; a failing criterion stops the run. Not certain means do not write it. The expression rules are those of the code-practice skill.

## How to tune: a slice, never the full batch

Tune the prompt on a slice of two or three items. The full batch runs at most twice, once before showing the user and once to confirm the final prompt. An unchanged try never re-runs: the same prompt, inputs and model answer from the recording, so to learn something new, change something. Once the prompt's source text (criteria, a rubric) lives in a variable the step reads at run time, the variable is the live copy: never re-read the original sample on later turns, and never re-run a proven step after the user accepts it. Two consecutive tweaks that do not change the slice's verdicts mean the problem is not the prompt. If the output is wrong, the missing piece is information: re-read the source material (read_sample) or the tool's guide, or switch approach, and never ask the user for method. If the output is a matter of taste, show the user what you have and iterate on their words.

## Which fields must be there

Mark the fields the answer is meaningless without as `required: true`, so the model must give them non-empty. Everything else it still emits but may answer null when the source has nothing, which is a thinner row and never a failure. Never force the model to invent a value the source lacked.

## The walls an AI step runs against

Design within these and name them in a diagnosis. The call waits at most 300 seconds by default (`timeout_seconds` on the plan step extends it, never shortens); hitting it usually means the request is oversized, so fewer items per call or less output per item. The answer has room for 32,768 tokens by default (`max_tokens` on the plan step for more); running out usually means the output echoes input fields, so return the judgement plus one identifying field.

## When the user dislikes an output

Offer the two levers as an explicit ask, your recommendation first: rework the prompt (free, right for misses of tone, content or structure), or move the step to a stronger model (costs more per run, right when the cheap model misunderstands the task). Models run cheapest-first by default. The user can also change a step's model in its panel; respect their pick and change nothing unless something is broken.
