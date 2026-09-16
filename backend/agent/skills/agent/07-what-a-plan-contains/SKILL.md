---
name: 07-what-a-plan-contains
description: The plan the build reads - the workflow's summary and purpose, what each step declares, the order steps run in, how the work splits into steps, the checks, what may be empty, where each value lives, documents, and the design gaps to resolve before building
tier: 2
kind: policy
depends_on: []
version: 4.4.0
last_updated: 2026-09-15
---

# What a plan contains

The plan is what you build from, and it stays on the workflow. Make it complete enough that building needs nothing else: a design that hangs together before the code is written is what makes the build work first time, so give every port a real type, have every input come from an earlier step or a stored value or the user, and line the names up.

## The workflow

The summary is two to four plain sentences of what the workflow, or the change, does. It is written in English for a non-technical owner and appears on the card the user reads; the precision lives in the steps.

The workflow also carries its purpose: two to four sentences, the facts the user has stated that bear on it, and the instructions for running it.

## Each step

A step's name is a plain human phrase, shown exactly as written: ordinary words with spaces, capitalised like a sentence, never a run-together or hyphenated identifier.

A step's id is its identity and its name is only its label. Every saved step carries an id for its whole life, built or not. You keep writing names in the order, the changes and the cell names, and they are matched to ids for you. To rename, carry the id with the new name, or say in the changes that this name became that one, and the recording and the user's answers follow. A new name with no id is a new step.

Each step declares its name, its kind, what it does, how it works, and what it takes in and gives out, plus the fields its kind needs:

- A code step or a connector carries the code, or, where no cell ever ran it, a prose sketch of what the code will do.
- An AI step carries its prompt and, only when the user asked for one, its model and provider. You never choose a model: leave `model` blank and it is picked for the step from the user's default provider, and when nothing can be picked the user is asked on a card without your doing anything. A model or provider the user names, in the chat or in the step's prompt, before the build, during it, or in a change or a fix, is the one the step uses: put their words in the step's `model` ("Sonnet 5", "haiku") and the set-up list is searched for it; a name that fits nothing comes back to you to put to the user. Set a temperature on every AI step: 0 for a judgement that should come out the same every run, higher only when the user wants variety; a model that takes no temperature ignores it, so it is never left off. Its answer room is left at the default unless the answer is genuinely long, or one came back cut off.
- A connector says in plain words what it changes outside, which is the sentence the user approves. One that only reads says so, and then no approval appears in front of it.
- A browser step is its own kind. It declares the `url` it opens, which is the address the person is asked about and where the app opens the window before the step's code runs; it names no `domains`, because Chrome reaches the web itself. Its cell always records the traffic and the whole page.
- A step declares the Python libraries it needs, and, if it is a code step or a connector, the bare hostnames it reaches. Anything not named is blocked when the workflow runs.
- A step that sends one message per item declares how it reaches the service and how fast it was agreed to go, so a later repair knows what to change.

"How it works" is one to three plain sentences shown in the step's panel: what it uses each input for and why it produces what it produces.

A port (one named input or output of a step) declares its name, a human label, its type, what goes in the field, and, for a list, the shape of one item. The type decides the control the user sees wherever the value is entered, so pick it for what the user has to do. Give every port a label, and reuse the label an existing key already has: one field, one name. The types are text, long text, number, boolean, date, choice, file, file path, folder, record, list and secret; a list declares the shape of one item beside it. While you are sketching the plan a port may carry its name alone, but every input and output has its type before the step is tried or built, because the try is judged exactly as a run is and a run checks every value against its type: the type is what the code expects, decided when you write the code, never read off whatever happened to arrive. A file port's `file_kinds` (pdf, image, spreadsheet, document, text) is the step's own check on what it takes: you never fill it in, the first try on a real sample does, and you widen the list only when the user asks the step to take another kind as well.

## The order steps run in

A line between two steps means order and only order: when this one finishes, that one may start. Draw the order the work actually happens in, and leave independent steps unconnected.

Two steps that need nothing from each other, such as a step that asks the user for a name and a step that fetches a page, are two branches: neither is drawn into the other, and each is drawn into the first step that needs both. A step may have several lines into it; it starts after every step on a line into it has finished, and reads all of their values by name. The save reports a line between two steps that share nothing, and the fix is to take it out and draw each of the two into the step that needs both.

A line never carries data. A finished step drops what it produced into a pool for the run, and a later step reads what it needs by name from the steps that ran before it on its own path, then from the stored values. So never draw a line because a later step needs an earlier value. Two earlier steps must not produce one name a later step reads, because nothing can say which was meant: rename one of them.

A line can carry a condition over the outputs of the step it comes from, such as one document type going one way. A step that runs only sometimes has the condition on the line into it, and a step that has to run whether or not that step ran gets its own line from the step before it, with the other condition. Every path is equal, so a path that skips most of the work is still a path and gets its own condition. Branch only on a condition you know is likely, never a speculative one, and the value that decides comes from a code, connector or AI step, never from a step that asks the user.

## How the work splits into steps

A step exists only at a boundary: the user putting something in, an effect out in the world, a judgement that needs a model, or a branch. Everything between two boundaries is one step, however much it computes.

Never two steps that do the same thing. Handling many items is one step looping in its own code, never a step per item, per batch or per chunk. Many workflows loop over items, and every such loop reads its maximum from a setting (a number such as `max_conversations`, set to what the user asked for, or asked about with the other setup values when they did not say); the number is never written into the code. Volume never justifies more steps: a step tested on the sample stands, and it is never split into copies of itself because the real job looks large, because copies bake in a count no setting can change. If a real run later shows the step could not cope, that is evidence and a repair; until then there is nothing to fix.

Each distinct outside source is its own step. Two sites, APIs or feeds are two steps running in parallel before the step that combines them, and a second source is always a new step rather than a widened one.

An AI step exists only where a per-run judgement is genuine. A step you did the same way every time while working it out is a code step. You never propose turning an AI step into fixed code, and never suggest it after looking at earlier runs either: that is a redesign the user asks for.

Every step the user watched you do maps to a step in the plan, and nothing extra or implied.

## Checks

Checks are the one or two always-on tests the recorded evidence makes you structurally certain of: relations that are arithmetic fact, such as the number of rows written matching the number kept. Never a literal out of the sample, and never a guess about future data. If you are not certain, do not write it.

Every check is hard, so a failure stops the run at that step. A rule the user stated always becomes one, and so does a fact the workflow's own identity fixes, such as the link having to be a search page of a particular kind, written on the step where the value comes in. Checks sit on the step that produces the value, so a wrong value stops under that step's own name before anything else runs on it.

A check is written as Python over that step's own inputs and outputs as bare names, and a field inside a record output is looked up by key. A name the step neither takes in nor produces cannot be worked out and fails the run.

Tests are cases built from the outputs the user accepted, with real values in them and never placeholders.

## What can be empty

For every output and every field inside one, decide whether the value is there on every run. One that legitimately is not on some runs is declared optional, and then it may be missing without anything stopping: the checks tolerate it, the enforced answer shape stops demanding it, and a step reading it gets nothing instead of the run stopping. Two things follow: every step that reads an optional value has to run without it, skipping the photo or leaving the column blank rather than failing; and a value the workflow truly needs every run is never marked optional to dodge a failure, it gets a real source, from the user or from another step. Where you cannot tell which of the two a field is, ask.

The same call for a list. One whose recorded run had items in it is expected to have items, so a run that finds none stops there, which is how a wrong link shows up. Where finding none is normal, say so on the port. Emptiness is judged once, where the list is produced: a step reading an empty list treats it as ordinary data and runs on it. A step that works through a list declares which input it walks and what identifies each item.

The full nested shape of a record output is worked out for you from the recorded run and held at the boundary on every later run, so you never write it out. It tolerates a missing field by default. Your one call is which fields the record would be meaningless without: a prospect with no job title is still a prospect, one with no name is not. Mark those as required, and a required text that comes back empty is a failure rather than a value. Where a field is a fixed set of choices, declare them, because those are never worked out for you.

Then decide, for each step that produces a list, what a run does with items that do not fit the shape: stop there and show the rows, carry the good ones on and set the rest aside with reasons, or set them aside and record it without raising a problem. Recommend carrying on only where the items are independent and what follows is a read or a report, and never the quiet option on a step that feeds a write.

## Where a value lives

A workflow automates a task, so it asks the user only what it cannot work out for itself. Values the user sets steer it: an address, a cap, a choice. Anything it could fetch, compute or read out of something else is a step's output.

So thread the data end to end: each handover uses one name on both sides, every output feeds a later step or a saved result, and every input names an earlier step's output exactly, or is a genuine steering value. If the user would have to paste it in, the seam is wrong. An input nothing produces sitting beside an earlier output nothing reads is a broken join, never a setting.

Three homes, one question each, asked in order:

- **A step that asks the user.** Is it likely to be different each run? Today's search phrase, a choice for this run. It never persists, and one that opens the workflow says why it has to be asked each time. No honest reason means it is a setting.
- **A stored setting.** Is it mostly fixed, something the user would change now and then? The sheet it saves to, links, folders, addresses, a limit, a threshold. It is created as the value arrives, never left to appear after the first run.
- **The code.** If this value changed, would the code have to change with it? An API path, a selector, a field name in someone else's system, a pattern. Those belong in the code, and the user cannot meaningfully change them. Never write a value the user gave you into the code as a literal: declare an input instead.

A value a step works out, such as an id read out of a link or a column position read from a header, is that step's output travelling by name, and never a setting. A per-run value never persists as one either. The place the user sets values holds only what they set and keep between runs.

A setting's type is a closed set: text, number, date, boolean, file, file path or folder. Never a choice and never a record. Only a step that asks the user can offer a choice; load "07-what-a-plan-contains/choices" when a step offers a list to pick from.

A value from an attached environment (a set of shared values several workflows read) is read by name and never copied: never declare it again, never ask for it again, and never write it into code or a prompt. Where it lives in an environment that is not attached, tell the user to attach that environment.

Secrets appear by name only, and the masking follows from the port's type.

## Documents

For things that cannot be typed: a spreadsheet, a PDF, an image. Anything whose content is text a person could read in a box is a text setting instead.

A file setting keeps a copy with the workflow, so it travels with it and never changes underneath. A file path setting leaves the file where the user keeps it and reads it fresh at the start of every run, which suits a file they keep replacing in place, and ties the workflow to this computer.

Either way the step declares a port of that type and reads it by name, asking for a real path when a library needs one. Reference data the user may want to change later, a list, a template, a lookup table, is a file they provide, never a list written into the code, and never a file you wrote yourself: a file you invented is not their data, and if they have no such file, say plainly that you cannot make it for them and ask what they can give you instead.

## Before you build

The save reports design gaps: a name two steps produce, an input nothing supplies, a value set in two environments, a step no cell ran. Resolve every one: rename something, reorder something, add a step, or ask one plain question.
