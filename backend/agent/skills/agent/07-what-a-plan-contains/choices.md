# Choices: where a dropdown's options come from

Only a user-input step can offer a choice (an `enum` field with `options`). A setting never can: a settings table holds values the user types and keeps, not a selection, and an enum setting is flagged as a design gap at save. The options must have a source, and there are exactly two honest ones.

## Produced by an earlier step, the usual answer

Scan the accounts, then pick one: the picking step runs after the step that produced the list and reads it by name from the run's pool, with no line carrying it. Over records it declares `value_field` on its scalar output, which field of the picked item becomes the value, while the user sees a readable label. This is right whenever the list is real-world state: channels, workflows, folders, people, files, anything a person could add to tomorrow.

## Static, and only when the code test passes

The test is whether the code would have to change if the list changed. Export formats (`csv | json | xlsx`), a mode the step branches on, units the code converts between: the step's own code enumerates them, so a new option means new code either way, and those are safe as static options. A list that really changes, baked in as static options, passes every check, ships, works in the demo, and quietly offers the wrong three names the day someone adds a fourth; nothing in the workflow notices, the user just cannot pick the thing they need. That is the failure this rule exists for.

## While exploring

If you cannot yet tell which kind a list is, ask whether the options should stay fixed or be looked up each run. One question, and the answer decides the shape.
