---
name: 61-connector-practice
description: WHEN designing or fixing a CONNECTOR step (anything that reaches an outside system - send, post, fetch, write) - what to include, how to structure it, what to test
tier: 2
kind: action
depends_on: [07-what-a-plan-contains]
version: 2.2.0
last_updated: 2026-09-16
---

# Connector steps: the practice

A connector is the workflow's hand in the outside world, so it is written to expect the outside to fail.

## What to include

- Request code is copied from the reference, never re-derived: load "61-connector-practice/resilient-request" when writing any request code. It gives every request an explicit timeout, retries only transient failures (never a 4xx, and never an "egress blocked" error, which must propagate so the host can be added to `domains`), treats a capacity or rate error as a plain failure, and never retries forever. A write gets one attempt.
- One request shape per step: build the payload, send, verify that the response says what "worked" means for this service (a 200 with an error body is a failure), and raise with the service's own message otherwise.
- Secrets by name: the value is get_secret('name'), or get_secret(read_input('port')) for a secret port. The name is never the value, and the value never appears in code, output or logs. Nobody reads a stored secret back, not the user and not you: values decrypt only inside the running step, what you see is ciphertext, and a cell never parses one. A non-secret fact that arrives with a credential (a service-account email, an account id) is its own variable, captured at setup while the user has it in hand.
- Idempotency where the service offers it (message ids, upsert keys), so a resumed run never double-sends.
- Copy the guide's list of hosts onto the plan step's `domains` before the first live cell, as an amend save, redirect hosts included (Google's routes ride script.googleusercontent.com). Each undeclared host interrupts the user once.

## Choosing the route

- Before proposing any setup, check the attached environments for an existing credential (a service-account key, a token). When one is there, lead with the route that uses it: the user set it up once already.
- A setup that takes the user into a cloud console (a Google Cloud project, a service account, an OAuth client, a Microsoft app registration) starts with one question: is this the first time you set this up, or do you have one from another workflow you want to use again? One project serves every workflow they build, so say so, and mention, as an option they can take or leave, that the values can later be saved as an environment on the Environments page and attached to the next workflow, so the setup is done once. Only then the steps.
- When a guide locks in a route, use it. Alternatives only when it proves infeasible here (an IT policy, a missing option). Ask the user only at a genuine fork the guide marks: equal routes, different trade-offs.
- The guide beats the docs: follow it without re-deriving. When a documented route fails in a non-transient way the guide does not predict, verify once against the official docs, say what changed, and proceed; never loop on the documented steps.
- Prefer routes that need no access to the user's computer (cloud endpoints, app passwords, webhooks) unless the workflow is inherently local.
- Setup instructions are the fewest possible steps with clickable links, never navigation prose. When IT blocks a route, say so and hand over the guide's one-line request for IT; never walk a non-admin through admin screens.

## How to structure it

- One connector step per external effect, and the connector is thin: a bare fetch or a bare post, never a transform, a parse or an analysis. Preparation is a code step before it and working the result a code step after; the connector only sends (or fetches) and verifies, which keeps the side effect small, approvable and stable, so one recorded send stays its proof for the workflow's life. Thin never splits the connector's own lifecycle: what the connection produced (a session, a token, the raw payload) is saved in the same step. A browser step is its own type, covered by the browser guide, one browser step is one page, and the work around it goes in code steps.
- read_only is true when the step only reads the outside system, even when it posts to get a token or a session first: what matters is whether anything out there changes. A write connector declares its external_impact in one plain sentence. A read never needs the user's go-ahead to run for real; a write asks once, and the yes holds for the step as it was: a changed step asks once more and the card says why, and "Yes, and don't ask again for this step" holds through changes for the rest of the build; the next build asks afresh.
- Tables (sheets, Excel, Airtable, Notion, databases) follow identity by content, the code-practice skill's rule: rows by a key value in the row, columns by header name, a write-back re-locating the row at write time; `checkpoint(key)` keys are those same values.

## Prove it with one real send, early, not at the build

- A write connector is proven only by one real send that succeeds. Authored code is not proof, and the build cannot finish a write step without a recorded successful send (it authors the test from that real shape). So send for real as soon as you reach the step, with whatever it needs: the webhook URL, the `requests` package, the host in `domains`.
- The real send is disposable and decoupled: the smallest disposable payload, one marked test row removed once it has served, never the full pipeline output into live data. Prove reads separately.
- The approval card is the ask: run the cell with a one-sentence `reason`, and the card asks the user for that one send. Never pre-ask with ask_user or for blanket permission. A no on the card settles the step: it counts as done, the build goes ahead, and the first real run is its first try. A missing user-side piece (a webhook URL) is fetched and then the cell runs; the send is never deferred to the build.
- An unproven write step is never a reason to say the step could not be built: it is one send, or one no, away, even when a build refuses for it.
- Tested on its current text, or declined: revising a proven send re-gates the step, so run the cell once and the card asks again, and the user's no settles it as done. Keep revisions rare and minimal; a carried older version heals itself, since the newest recording is adopted at save. When a design change makes an earlier test write stale (a row sent during proving that no longer fits), say so and offer to clean it up.
- Load "61-connector-practice/unproven-steps" when the user declines a live test send, or a step's trigger cannot be reproduced right now; both ship complete and let the first real run prove them.

## Sending many

A step that sends per item uses the loop in browser/api-routes: paced, `checkpoint(key, result)` as each item lands, outputs from `checkpoints()`, rate limits left to raise; the plan step declares `per_item: {input, key}`, `route` and `requests_per_minute`. A stop says how many of how many are done, and the user may proceed with those. The step says what happened per item as an outcome list (item, status sent / skipped / failed, reason), never a bare list of the ones that worked. Two kinds of failure, and they are handled differently: an item the service refused on its own merits (an invalid address, a duplicate, a field the service names) is reported as failed with the service's reason and the loop goes on; the service failing the send itself (429, any 5xx, a timeout, a dropped connection) raises after the checkpoint, so the run pauses at that row and the next run continues from it. A status outside 2xx is never written into the result as if the row went: the runtime counts every request's status by kind of request (one method and path, numbers aside), so the token call and the loop are judged apart, and a kind of send none of which answered 2xx, or one only some of which did, stops or pauses the run whatever the result says. A write given items that produces nothing and no reason stops the run (no-effect); a per-item exception is never swallowed. Volume and speed are the user's call: state the trade once, recommend, build what they chose, never re-litigate or quietly build slower.

## What to test

- Authored tests, never replayed against the live service (a replay fires the side effect): pin the payload build and the response handling. Given these inputs, the request body is exactly this; given this recorded response, the step reports success; given that error body, it raises.
- The recorded exploration run is the live evidence. When the success shape is unknown, ask the user for a real example response; never guess.
- After the real send, author the one or two criteria its recorded shape makes certain (a success flag true, two counts that must agree), each with a human label. Not certain means do not write it. The expression rules are those of the code-practice skill.
