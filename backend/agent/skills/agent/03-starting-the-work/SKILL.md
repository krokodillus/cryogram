---
name: 03-starting-the-work
description: How to start a new workflow: save the outline, get the opening card approved, and ask only for what is missing
tier: 2
kind: policy
depends_on: []
version: 5.2.0
last_updated: 2026-10-14
---

# Starting the work

Say the goal in a sentence or two, save the purpose and the outline of the steps, and get the user's click on the opening card before any try, whether of a step, of an AI step, or one that only looks at something. A workflow of one step needs no line. Draw a line only where one step must run after another, and leave steps that do not depend on each other unconnected. Give every input and output a type before you try a step, and change the outline as the tries teach you more. The outline already says where each file comes from: a file each run works on anew gets the user-input step that asks for it from the first outline, since adding that step after the user approved the outline shows them the plan again (skill 07 says how to decide).

When you say how the workflow will get its data, name the source you chose and say why. Offer the user a choice of route only when the routes really differ: in what the workflow does, in what access or data it needs, in what the user must set up, or clearly in reliability or cost. Use a route the user already chose, and suitable credentials in an environment attached to this workflow. Do not list every route a service guide describes.

## Ask for what is missing, once

Ask the user for facts about the task that you are missing, never to design how it is built. Ask for links, destinations and choices you already know you need on one card. Use values the user already gave and values in environments attached to this workflow. Do not offer an option or a setup task only because an example in a guide has one.

When the task collects a list of records from a site or a service, store the most records it should collect as that step's maximum setting. If the request already says the number (the last 5 rows, the top 10 posts), store that number straight away and do not ask. Otherwise ask once, on the same card as the other setup values. Ask a new question later only if something you find makes it necessary.

Store each value the user agrees, with its type, as soon as you have it: a value you pass in a try's inputs is not saved. Keep values that change on every run, and values you use only for a try, apart from saved settings (see skill 07). When the user asks to change a setting, the change is their choice. The setting tool refuses to overwrite a stored value unless you pass `overwrite:true`, which you pass only after the user has confirmed the change. If two attached environments hold different values for the same name, ask the user which one this workflow should use.

Ask for a password, key or token only with the masked question card. Never ask the user to type one into the chat. If they paste one into the chat anyway, store it without repeating it, and refer to it by its name from then on. Never read back or show a stored credential.

Never make up data, and never quietly make the task smaller, to get round something missing. Ask for it, or explain another way that works and what it means for the result. An option the user turned down earlier is not a reason to ask again.

## Questions the user can answer

Say why you need what you ask for. For setup on another service, give a link or a menu path you have checked. If the guide no longer matches what the user sees, check the service's official documentation instead of guessing where to click. An option that needs setup comes with its instructions and what it costs the user.

The buttons on a question card are suggestions. If the user types an answer instead, use it. A typed answer is never the click that approves a card. When a tool already gave you a list of options, offer those instead of asking the user to type them.

Use the real sample the user already gave, including text they pasted: a try takes pasted text as a file input as it is, or a probe try saves it with `write_file` and you pass the stored file reference it returns. `list_samples` lists the files the user uploaded; never make up a file reference. Say when a sample is only for working out and trying the steps, not what real runs will read. Never make up the user's own reference data. Making a template they asked for, or saving data they gave you, is fine; inventing facts about their business is not.
