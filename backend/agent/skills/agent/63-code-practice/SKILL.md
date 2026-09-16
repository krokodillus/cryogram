---
name: 63-code-practice
description: WHEN designing or fixing a CODE step (deterministic work - parse, transform, compute, file) - the pure-step shape, what to include, what to test
tier: 2
kind: action
depends_on: [07-what-a-plan-contains]
version: 2.1.0
last_updated: 2026-09-14
---

# Code steps: the practice

A code step is deterministic: same inputs, same output, every time. That is its whole value, so protect it.

## What to include

- The pure-step shape: read the declared inputs with read_input, compute, write the declared outputs with write_output. No hidden state, no reading files outside the declared inputs, no network (that is a connector's job). A document arrives as a `file` or `filepath` input, read with read_input(name), or read_input(name, path=True) when a library wants a real path. A produced file goes through write_file and its blob reference onto a file-typed output.
- A loop over items stops at the maximum it reads with read_input from its setting, and never at a number written into the code (`items[:2]`, `islice(items, 2)`), however many items the step was tried on.
- Handle the input's real variety, learned from the sample: empty lists, missing fields, comma decimals, encodings. The sample showed you the shape, so code to the shape you saw plus the obvious neighbours, not to every imaginable input.
- Fail loud on the genuinely unexpected: raise with a message that names what was wrong with the data, and never swallow an exception to keep going, because a wrong output that flows on is worse than a halt. A raised error reports what was observed, the values, counts or state the code actually saw, not just what did not happen: naming the field, the row and the value it held is diagnosable, "invalid input" is not.
- The harness owns every pause, and code never re-creates one. A saved workflow runs with no chat and no model, so a step can never talk to the user: a real-world write is already fronted by its approval popup, a mid-run choice is a user-input step, a missing value pauses with its own form, and a login wait polls for the session cookie. Code that thinks it needs the user mid-execution is mis-designed; the moment it wants already exists.
- Empty is a value: any list input may legitimately arrive empty (its producer's edge already judged it, and two lists where one is empty is a normal run). Code runs correctly on it: loop over nothing, write empty outputs for that part, act fully on what is there. Never index the first element without knowing the list is non-empty, and never re-judge emptiness downstream; a crash on an empty input is a code defect.
- A step that parses a file, payload or link checks first for the columns, keys or page it needs and, when they are not there, calls `unexpected_input("<what this is not>", found=<the header or keys it has>, expected=<what it needs>)`, never a KeyError two lines later. The run then stops saying that the file it was given does not look like the one it expects, naming what it has and what it needs, and the user is asked how such files should be handled: the wrong file, another format to support, or not this workflow's job. That call is the validation; a later fix never adds validation to a step that already did this.
- Identity by content, never by position, for any table or list lookup (a sheet, a CSV, an API list, a database result): a row is identified by a stable value in the row that names the thing (an id, a URL, an email, a name and date), never by its row number, and a column is addressed by its header name, never by index or letter. Positions shift the moment someone sorts, inserts or deletes, and a tracker keyed by row number silently skips or collides after one edit. A write-back finds the row by its key at write time (re-read, locate, then write), because the table may have moved between the read and the write. Row numbers and column letters exist only inside the one call that needs them, computed from the header at run time, never stored, never a setting, never in a recorded list of what is done.
- A step whose output is a list degrades to fewer items, not to failure: when the full target cannot be gathered, return what was collected and let the workflow continue, and raise only when there is nothing at all.
- A step that works through a list keeps what it got: declare `per_item: {input, key}`, call `checkpoint(key, result)` as each item is done, skip the keys already in `checkpoints()`, and build the outputs from `checkpoints()`, so a stop says how far it got and the step finishes from its record. The loop itself is in browser/api-routes.
- No placeholder logic, ever: code that ignores its inputs or returns literals is refused at the gate.
- Surface what checks need: output the numbers a check can hold onto (rows written, counts, a status) instead of doing work and returning nothing. A check can only verify what the code surfaces.

## How to structure it

- Everything between two boundaries is one step, however much it computes. Split only at real boundaries: user input, an external effect, an AI judgement, a branch decision.
- Output port names are the seam contract, since the next step reads them by name. Name them once, well, and keep code and ports in lockstep.

## The walls a code step runs against

- The sandbox stops a step after 120 seconds of silence, never of total runtime. A loop that calls `heartbeat()` each iteration, saying how far it is, runs as long as it keeps making progress. Only one long blocking call that cannot ping declares `timeout_seconds` on its plan step instead: the heartbeat catches real stuckness, a raised limit just fails later.
- During exploration a cell is capped at 300 seconds of total wall time (prove on a couple of items; volume belongs to real runs); a declared `timeout_seconds` overrides it.
- The disk is bounded like the network: a step may read and write its own scratch folder, temp, the folders on the workflow's `folder` settings and the folders its plan step declares in `paths`. Anything else on the computer is refused ("path blocked"), the app's own folder always. Files come in through read_input and go out through write_file.

## What to test

- The recorded exploration run is the step's evidence (real input in, accepted output out) and its shape test: the nested shape of every record or list output is derived from it and held on every run. Prove the step on rows that show the variety you expect (a missing optional field, a null) so the derived shape already covers them. It is the regression baseline, replayed when the step is changed (so tests must not depend on the clock, randomness or the network), never on normal runs.
- Add one test per input variety the sample revealed (a missing due date, a comma decimal): the classes you handled, proven with real values.
- Author the one or two criteria the evidence makes structurally certain: relations that are arithmetic fact (rows written equals the number kept), never a literal out of the sample, never a guess about future data. Not certain means do not write it. Each carries a human label, and a failing criterion stops the run at this step.
- Asserts and criteria read output fields as bare names (len(rows) == 3, never output.rows). They may call only len, sum, min, max, abs, round, sorted, all, any, the type constructors (int, str, list and the like) and the checks is_number, is_positive, non_empty, in_enum, in_range, matches, is_date. No isinstance or type(): a field's type is already fixed by its port, so test the value. Anything else is refused at save.
