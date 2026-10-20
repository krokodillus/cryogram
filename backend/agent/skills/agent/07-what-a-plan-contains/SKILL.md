---
name: 07-what-a-plan-contains
description: What a plan holds: where one step ends and the next begins, typed inputs and outputs, the lines between steps, checks, and where each value is stored
tier: 2
kind: policy
depends_on: []
version: 5.2.1
last_updated: 2026-10-14
---

# What a plan contains

The plan describes a workflow that will run again and again on new data, not only the sample that worked. Write its summary, purpose, facts and run instructions in English, keep them short, and update them when what the workflow does changes.

## Steps and their inputs and outputs

Give each step a name a person can read, one kind, a short description of how it works, typed inputs/outputs with names a person can read, and the fields its kind needs. Keep a step's id when you rename it: a new name sent without an id is read as a new step.

- A code, connector or browser step carries the code from its accepted try, or, for a step deliberately left untried, complete code you wrote.
- A connector step declares the exact host names it reaches (only the host, for example `api.example.com`, including any address it is known to redirect to) and `read_only`. A browser step declares its `url` and `read_only`, not `domains`. A connector or browser step that changes something outside also declares `external_impact`, one plain sentence the user reads before they approve it; a step that only reads needs none. Only a browser step gets a `page`.
- Declare the Python packages a step needs and the folders it may read, as the user approved them. Never add a library, a host or a setting only because another workflow used it.
- An AI step carries the prompt that tells the model what to judge, and what each output field means. A model or provider the user names is the one to use. Otherwise leave `model` blank, and the default model the user set up is used. Never swap in another model without telling the user. Set the temperature to suit the judgement (see skill 62). Change the output limit or the time limit only when a try showed it was needed.

Input and output types, and how arguments are spelled, follow each tool's own description and the list of functions available to step code. Use one name for a value from the step that makes it to every step that reads it. A file is passed as its stored file reference, never as its contents or a path into the app's storage. The kinds of file a step takes come from the real sample; accept more kinds only when the user asks for it and a try shows it works.

The shape of nested outputs of a code, connector or browser step is worked out from its tries. An AI step's record and list outputs need their `item_fields` before its first call, each with a type and a description that says what it means (see skill 62). When a value can only be one of a fixed set, list every option: one example does not show all the values that are allowed.

## The lines between steps

A line between two steps means only "run this step after that one". It does not carry data: a step reads its inputs by name from the outputs of steps that ran before it on its own path, then from the values the run was started with, then from saved settings. So the step that makes a value must come before the step that reads it, on the same path of lines. Steps that do not depend on each other stay unconnected. A step can have several lines coming into it. Do not add lines nothing needs, do not put steps that do not depend on each other in a row, and never let two earlier steps produce a value under the same name that a later step reads.

A line can have a condition (`when`), which reads the outputs of the step the line comes from. Say which runs take each path and which steps they skip, and do not add paths for cases nobody expects. The decision on a path is made by a code, connector or AI step. A part of the workflow that only has work when an earlier list has items, such as a deeper look at the people a filter kept, starts with a condition on the line into its first step (`len(review_candidates) > 0`), so a run where the list is empty skips that part instead of running it on nothing.

Split the work into a new step only where something really changes: the user gives a value, something outside is changed, an AI judges something, or the run takes a different path. Keep the work that prepares data on this computer together. Each outside service gets its own connector step. A browser step may open many pages if they are the same kind of page. Collecting a list and opening each item in it are two steps: the first does whatever it takes to collect the whole list (scrolling, clicking "next", going to the next page) and saves each item's link as it goes, and the second opens each saved link, each with its own maximum setting. Never split a step into copies to handle more items, and never move the building of a small request into its own step only to have more steps.

A step that only transforms data on this computer works on all of its input. A browser or connector step that reads a list from outside declares `max_setting`: the name of its number input that holds the most records it collects. Set that input to the number the request states, or, when it states none, to the number the user gave when you asked them once. Declare it, and write the step's code to read it, in the first save that has the step, so the step never has to be rewritten later to add it. The step collects until it reaches that maximum, the end of the list, or a cutoff the user gave, whichever comes first. A plan without it has a design gap. A step that does something to each item and must be able to carry on after a stop declares `per_item: {input, key}`, where `key` is the field that tells two real items apart. Two items with the same content are not therefore the same item.

Use an AI step when each run needs real interpretation, and code when the rule can be written down exactly. Never replace a judgement the user asked for with a rule of thumb, and never redesign an AI step the user accepted without their agreement.

## Checks, empty values and partial results

A check states a business rule the task really has, never a value copied from one sample. In a check, use only the step's input and output names, and only the functions the app allows. A failing check stops the run at the step that produced the value. Keep the tests you write specific, and make them cover cases the recorded try does not.

Mark an output, or a field inside it, optional only when the value can really be missing. Every step that reads it must work without it. Required means the workflow cannot do its job without the value; never make an AI step invent a value the source does not have. An empty list can be a complete and correct result, but it never shows that a blocked page was read. Decide on purpose whether an output can be empty: otherwise a try that returned items marks it as one that must not be empty.

Decide from the task whether the run may carry on with only part of the items. An export or a reconciliation that must be complete stops when items are missing. A report where the user accepted partial results says which items are missing and why. A failed item must never be marked as sent. Use the step's `on_invalid_items` setting (`stop`, `proceed` or `log`) to make this explicit.

## Where values are stored

- A choice that is different on every run belongs on a user-input step, and is not saved. Load "07-what-a-plan-contains/choices" when you offer the user options to choose from.
- A value the user controls and that stays the same between runs (a destination, a sheet tab, a threshold, a folder, a real limit) is a saved setting with a type, stored as soon as the user agrees it. Passing it in a try's inputs does not save it. Use only the types the setting tool supports: a choice or a record cannot be a saved setting.
- A value a step works out belongs on that step's output, connected to the steps that read it, and never in a setting the user has to fill in.
- API paths, selectors and fixed values of a site's structure belong in the code. Values the user gave belong in settings, not written into the code.
- A value from an environment attached to this workflow is read by its name, never copied into the workflow. When two environments hold the same name, ask the user which one to use and record their choice.
- A secret is read by its name from the secret store, never stored as an ordinary text value.

## Files and reference data

Decide where a file comes from by what it is for, in the first plan you save. If each run works on a different file (an invoice the user uploads, a new export), the plan starts with a user-input step that asks for the file on each run, and that step's `per_run_reason` field says why in one sentence. If it is the same file on most runs (a template, a document of criteria), it is a setting whose value is the uploaded sample's stored file reference. The sample the user gave you while building is only for trying the steps in the first case.

A stored file is a copy kept with the workflow. A file path is read fresh from the user's folder on every run, through the folder the user allowed. Use the input or setting type that matches, and call `read_input(name, path=True)` when a library needs a path on disk.

Use a real sample the user uploaded, text they pasted and you saved as a file, or a template they asked you to make. Never make up a lookup table, and never say a file you made is the user's data. Use a file for structured or binary material or for reference data that changes; use text for a short plain value. Not every document you can read is a text setting.

The checks before a build are in skill 06. A sample that worked shows that one run worked. It does not show that the setup is complete, that every future input will be handled, or that a send reached its destination.
