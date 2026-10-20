---
name: 40-corpus-and-verdicts
description: WHEN a failed run or a change needs the cases that earlier runs recorded, or a precise count across them
tier: 2
kind: action
depends_on: []
version: 3.0.0
last_updated: 2026-10-13
---

# Recorded cases: what earlier runs show

Use what you already have first. Then use `read_cases` to read a few typical runs of a step that worked and that failed, or `query_corpus` for a precise count across them. Read only the steps and the cases the problem is about. `corpus_summary` is there when it helps, not something to call at the end of every turn.

Each case records a step's real inputs, its output or the reason it failed, how long it took, and the circumstances, with secrets masked. A failure can carry `hard_failures` (checks that failed), `standard_input_check` or `standard_output_check` (an input or output of the wrong type), or `thrown` (an error the code raised). The `cause` and `cls` fields are notes someone added: a field being there does not make its cause true. A run that paused for setup, an approval or a missing value is not a case of a fault.

Compare the failing case with cases that worked, find a cause the cases support and the conditions under which it happens, and record it with `diagnose_case`. One example never shows a rule for every case, and the same failure happening again does not show that code must become an AI step. Say when you are not sure.

Before you change or try a step of a built workflow for real, save the change's or the fix's declaration and wait for the user's click on its card (skills 06 and 08). Reason from the recorded cases, and keep what used to work working. `run_node` can run one saved code or AI step on its own where that is allowed; running an AI step this way calls its model, which the user pays for. It cannot run connector steps, and it cannot run the saved workflow's steps one after another.

A changed or new step still needs the kind of try its type requires. Pass the recorded inputs to a try with the step's name, or to an AI try. A step that changes something outside gets its normal approval card, or is left untried when the user says no, in the way the guides describe. Never invent a success, and never call reading through the code a real test. Say what you actually checked, and what only the next run the user starts can show.
