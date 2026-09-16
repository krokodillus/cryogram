---
name: 40-corpus-and-verdicts
description: WHEN diagnosing a failed run or designing a change to a built workflow - what the corpus records, how to diagnose the class of a failure, and how to design against stored cases without running the saved process
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-09-06
---

# The corpus: the workflow's memory and your test bench

Every run of every step is recorded as a case. A success holds the input and the verified output; a failure holds the input, what the check saw and the raw circumstances. Each case has a kind ("success" or "failure"), a case id, its run id and a time, and its inputs with every secret value scrubbed out. A failure's verdict says what the check saw, keyed by cause: hard_failures for the step's own criteria, standard_input_check or standard_output_check for the declared port shape, thrown for an exception with its type and message. A failure's observed part is the raw circumstances: the step type, soft flags, tracebacks and timings. Its cause and class are empty when captured; you fill them with diagnose_case after investigating. Pauses are not cases: a run waiting for approval, a missing value or secret, a blocked address or an environment problem halts resumably and records nothing here.

## Diagnosing a failure

Diagnose the class, not the instance: compare the failing input with the stored successes and find the property the failure has that every success lacks. That property names the class. Record it with diagnose_case(node_id, case_id, cause, cls) before designing the fix. A fix is class-level, handling every input of that class, and the failing case together with the stored successes is the regression set the fix must pass. When the same step fails the same class despite earlier fixes, stop patching: the step is misclassified, either deterministic code doing a fuzzy job or one step doing two jobs. Propose a split, with a small AI step exactly where the input turned out fuzzy.

The halt reasons that create a ticket are a failed criterion ("output-check-failed"), a failed declared shape ("output-check-failed (standard)" or "input-check-failed") and an exception ("node threw: <Type>"). The resumable pauses (awaiting-approval, missing-value, egress-blocked, environment-check-failed) never create one.

## Designing a change against the corpus, never by running the saved process

Read the current structure first: list_nodes, then read_node on everything the change touches. The relevant learnings are already in your context. Pull real cases: read_cases(node_id, "success") for representative inputs and outputs at the point of change, and the failures too when a failure motivates the change. Use query_corpus for precise questions (counts, clusters, contrast sets) rather than reading everything. Work the change through on paper against those cases: for each stored input, what would the changed step produce, and would any stored success now fail?

When you must see behaviour to be confident, run_node runs one saved code or AI step on a stored input, in isolation. That is as far as the built steps go: connectors are excluded, and you never execute or hand-chain the workflow. A new step the change adds is different: prove it as a cell (run_cell on stored cases or samples, with effects design-only), because the build refuses an unproven code step. The next real run is the live test.

Then save the plan: which steps change, are added or removed, with tests seeded from the cases you reasoned over, the motivating one included. Say plainly in the chat that the change is designed against the real past runs you used, how many, that the next run is the live test, and that a failure comes straight back to you.
