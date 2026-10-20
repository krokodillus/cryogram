---
name: 04-working-a-step
description: How to try each step for real, reuse what earlier tries saved, and keep test values apart from saved settings
tier: 2
kind: policy
depends_on: []
version: 5.1.0
last_updated: 2026-10-14
---

# Working on a step

Try each code, connector and browser step of the plan with `run_cell`, giving the try the step's own name. The try's code, inputs and accepted outputs become the step's code and first test when the workflow is built. Try an AI step with `run_ai_step`. A try that only looks at something, and is not one of the plan's steps, gets a different name that says what it looks at, and a one-sentence `reason`, so it never replaces a step's recorded try.

A step's inputs and outputs are checked against their declared types. When a value has the wrong type, fix it at the declared input or output type, or at the stored setting. Do not convert it in the code of the step that reads it. Mark an output optional only when the value can really be missing, for example a person with no profile photo. Never mark it optional to get past a failed check. A step that reads data from outside may convert it from the form the source documents into the declared type.

## Work in small, repeatable tries

Look at the sample first. Decide what you do not know yet, and run the smallest try that answers it. A try that reads from outside, or sends something the user approved, uses a small sample that is typical of the real data, usually one or two items. A step that only transforms data on this computer may use all of its input. When a step has a maximum setting, pass a smaller number in the try's inputs, and leave the stored setting as it is.

A browser step that reads a list from one page is tried on what the page shows first plus one more batch of what it loads on scrolling, by passing a maximum just above the number of items the page shows before scrolling. A step that works through several items is tried on 1 item first, and then on 2 to check the loop. The browser guide says more about both.

Use the current guide for the service before you search the web, and load it if it was not given to you. Search the service's official documentation for details the guide does not have, that look out of date, or that do not match what you see. Load the browser guide only for a browser route the user agreed to, never to get round a refusal.

A try is judged exactly as a run is: its inputs and outputs pass the run's own checks. Read inputs by their names from the first try. A step's purpose decides its declared outputs and their types, and the code exists to produce them. So write the `write_output` lines first, one for each declared output, with exactly its name and in its declared type, and then write the code that fills them. Keep values you only want to look at out of the declared outputs: extra keys never become part of an output. A file the step makes is written with `write_file`, and its reference goes on a file output; check what is in the file, not only that it exists. Keep secrets, and sample data that has nothing to do with the output, out of every output. In a connector or browser step, a loop that waits on a page or a service calls `heartbeat()` on every round, or a run stops it for looking stuck; a try whose loop has none is refused with the rest of the code check.

You can send several tries that do not depend on each other in one call. Never send something real to a service in the same call as tries that only look at things. A step the user approved to send one thing per item is one step with that purpose, not permission to send anything else. Reuse samples, recordings and the guides you already have in full. Read something again only when part of it is missing, was cut short, or has changed. Never fetch a page again only to read it a different way: read the saved copy.

Give every wait an end, and stop as soon as the result answers your question. Read the workflow's models, samples and current plan from what the app already gave you; do not run a try to find them out.

## Changes, and what needs a new try

Keep code that works when what it does does not need to change. A change to a step's code, prompt, model or inputs and outputs needs a new try of that step, as its guide says. Reading the changed code yourself is never a replacement for that try. If you only renamed something, or only changed where a value comes from, you do not need to try the step again for real, as long as the app still accepts its earlier try. When the app says a step's earlier try no longer counts, believe it.

Test code that only transforms data on this computer with tries that open nothing outside. For a connector or browser step, check the shape of its data against what earlier tries saved, or with rehearsal where the app supports it, never by sending or fetching again for real. If you really need another real try, say why, and it gets its normal approval card. Never sign the user out, or cause an error on a service, in a way that breaks something, to see what happens.

A saved workflow does not include you. When a run needs a judgement or a value from the user, use an AI step or a user-input step; never build a chat into a step. A browser step asks the user to act in the window with `confirm_with_user`, as the browser guide describes.

## Sending for real, and browser windows

A real test send uses the smallest data that shows the send works, to the destination the user agreed, and data that can be thrown away afterwards. The approval card is the question: never ask the user beforehand whether you may try it. Say in the try's `reason` exactly what it will send. Deleting the test data afterwards is another change on the service, which needs its own approval: never delete a test row by yourself.

When the user says no to a test send, or a send cannot safely be made to happen during the build, follow the connector guide's reference "61-connector-practice/unproven-steps". The step can stay untried, with complete code and tests you wrote, and must never be recorded as passed. Everything else the build needs still applies.

When you are not sure whether a send arrived, never send it again to find out. Look at what earlier tries saved, or check on the service first. Only an operation you have checked is safe to repeat may use the connector guide's retry rules.

For browser work, follow the browser guide on windows, checking the page, the saved files and how many items to collect. The app opens and closes the window. The user agreeing to a route does not replace the card before a window opens or before a step changes something outside.
