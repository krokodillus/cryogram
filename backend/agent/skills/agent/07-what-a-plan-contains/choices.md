# Choices: where the options in a dropdown come from

Only a user-input step can offer the user a choice: a field of type `enum` with its `options`. A setting never can. A setting holds a value the user types and keeps, not a choice from a list, and a setting of type enum is a design gap when the plan is saved. The options must come from somewhere real, and there are only two places they can.

## From an earlier step, which is the usual case

For example, one step reads the user's accounts, and the next step lets the user pick one. The step that offers the choice runs after the step that produced the list, and reads the list by its name from the earlier step's outputs; no line carries it. When the list holds records, the choosing step declares `value_field` on its output that holds one value (not a list): the field of the chosen record that becomes the value, while the user sees a label they can read. Do this whenever the list is something that exists outside the workflow and can change: channels, workflows, folders, people, files, anything a person could add tomorrow.

## Written into the step, only when the code would change anyway

Ask: would the code have to change if the list changed? Export formats (`csv`, `json`, `xlsx`), a mode the step's code branches on, or units the code converts between are listed in the step's own code, so a new option needs new code either way: these can be fixed options. A list that does change, written in as fixed options, passes every check, is built, works in the demo, and then offers the wrong three names on the day someone adds a fourth. Nothing in the workflow notices; the user just cannot pick what they need. That is the failure this rule prevents.

## While you work out the steps

If you cannot tell yet which kind of list it is, ask the user whether the options should stay fixed or be looked up on each run. One question, and the answer decides how the step is built.
