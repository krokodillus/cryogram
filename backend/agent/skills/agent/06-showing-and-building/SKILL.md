---
name: 06-showing-and-building
description: How to show results, save changes to the plan, get a change or fix approved, and what must be true before you build
tier: 2
kind: policy
depends_on: []
version: 5.0.0
last_updated: 2026-10-13
---

# Showing, saving and building

Show the user enough of the real output for them to judge it, and link the files the steps made. Ask for their opinion only when a decision is a matter of taste, never as an extra check on a result they already described. Say clearly when fields are missing or a result is only part of what was asked.

## Save without repeating what is recorded

When you save the plan, send only the steps you changed or added. Steps and lines you do not mention stay as they are. A list of lines (`edges`) that is not empty replaces all the lines, so send every line when you send any. Name every step you remove under `changes.removed`, including the steps of a route you are replacing. When you rename a step, send its id with the new name.

A step's recorded try already gives the plan its code and first test. Do not type them again into a save: send code only when you change it, or for a step you deliberately leave untried. Add tests only for cases the recorded try does not cover. Keep the design the user agreed, and every setting the change does not affect.

Remove settings that are blank and no longer used, where that is clearly right. Removing a setting that holds a value needs the user to ask for it. Never make a user-input step only to collect a value that stays the same between runs, and never hide a broken connection between two steps by turning the missing value into a setting.

## Getting a change or a fix approved

Before you change code in a built workflow, or try one of its steps, save a declaration: `changes` lists the steps you will change, add or remove, and `change_note` says what was asked and what will change, or, for a fix, `fix_note` says what went wrong (the fault you confirmed) and how you will fix it. Wait for the user's click on the card this raises before you work on those steps. A typed reply changes the proposal; it is never the approving click.

Adding or removing a step, or changing a step's kind, before or after the first build, needs a new save whose `change_note` says what you found and what will change, and the user's click on the card it raises. On a save that raises no card, a `change_note` is optional and is shown in the chat. Working further on steps the user already approved needs no new card. A new host, a browser window or a send still asks on its own card. A fix is never permission to make other improvements.

## Before you build

Before `build_workflow`, fix every design gap and every finding the save reported:
- Every setting a step reads has its own stored value, not only a value given in a try's inputs, and it holds the real value, not one you used only for a try.
- Every value a step needs comes from an earlier step that produces it under the same name.
- Every step has an accepted try, or a recorded reason it was left untried or the user said no.
- Every file the steps make exists, and what is in it fits the task. Every package a try needed is still declared.
- The run instructions say how to start a run, what the user is asked for during a run, and where the results appear.

Show the result, save the last changes and build in the same turn. After the user approved the opening card, do not ask again whether to save or build. A change of what the workflow does, a fix, or a declared send still needs the user's approval.

A refused build is not finished work: fix what it reports. A successful build ends your tool use; at most add one short line about the result, without repeating the card. The user presses Run. Do not offer to start a run, and do not promise to follow up later.
