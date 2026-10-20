---
name: 63-code-practice
description: WHEN you design or fix a code step: work on this computer that gives the same result every time, such as transforming data or reading and writing files
tier: 2
kind: action
depends_on: [07-what-a-plan-contains]
version: 3.0.0
last_updated: 2026-10-13
---

# Code steps

A code step reads its inputs by name, writes its outputs by name, and gives the same result every time for the same inputs. It cannot use the network, and it must not depend on anything outside its inputs, such as the time of day or a file it was not given.

## What to write

The step's purpose decides its declared outputs and their types, and the code exists to produce them. Write the `write_output` lines first, one for each declared output, with exactly its name and in its declared type. Then write the code that reads the declared inputs and fills those outputs. A list output takes a list. A loop that records each item with `checkpoint` writes `checkpoint_values(keys)` on its list output, with the keys of the input's items, because `checkpoints()` returns a record keyed by item, not a list. Read a file with `read_input`: `binary=True` gives its bytes, `path=True` gives a path on disk for a library that needs one. Write a file the step makes with `write_file`, and put its reference on a file output. Call the functions exactly as the list of functions at the top of these instructions shows.

Work through all of the input, unless the task gives a real limit. Handle the variations the sample and the declared inputs show you: empty data, optional fields that are missing, text encodings and number formats. Do not add code for problems an earlier, unrelated run once had.

Check that the columns or keys the step needs are there before you read the data. When the input is the wrong kind of data, call `unexpected_input(reason, found=..., expected=...)`, saying what is there and what the step needs, without copying private values into it. Let an unexpected error stop the step: never catch it and return a result that looks complete. Never write whole private rows or credentials into an error only to make it more specific.

Handle an empty or optional value without assuming the first item exists. Tell an output that is empty because there was nothing apart from one that is empty because collecting failed. Return part of the results only when the task says partial results are fine.

When you look up or write back rows in a table that can change, such as a sheet, find a row by a value that identifies it and a column by its header name, read at the time the step runs. Never store a row number or a column position. Going through a list in order is fine, but an item's position is never its identity. Never merge two different rows only because they have the same content. Sending to another service, and the rules for retrying and recording each item, belong to the connector and browser guides.

## How to structure it

Keep work that belongs together on this computer in one step. Start a new step only where something really changes: the user gives a value, something outside is changed, an AI judges something, or the run takes a different path, never only because the code is long. Never write placeholder code that ignores the step's inputs. Put the values that the checks in later steps need on outputs, rather than returning nothing.

A code step does not need `per_item` and `checkpoint` only because it has a loop. Use them only when the user wants the step to carry on after a stop, or for a long operation on this computer. A step that sends to another service for each item has its own rules for this in the connector guide.

## What to test

Test the step on the real sample, and on small cases you write for each variation the code handles. Keep the recorded try, and run it again when what the step does changes. A test must not depend on a live service, the clock or random numbers.

Look at what is in a file the step made, its columns and the values of its rows, not only its stored file reference. Check rules the business really has, not values copied from one sample. The required outputs and the types of saved inputs are what the app checks on every run.

A check uses the step's input and output names, and the keys of a record, written as plain names. It can use these functions: len, sum, min, max, abs, round, sorted, all, any, the type conversions such as int and str, and is_number, is_positive, non_empty, in_enum, in_range, matches and is_date. It cannot use isinstance or type.

## Time limits

A step that reports no progress for 120 seconds is stopped, in a try and in a run alike. Call `heartbeat()` with real progress during long work. A call that can block for a long time needs a time limit you can explain. A heartbeat must never keep a stuck operation alive for ever.

A step can use its own temporary folder and the folders the user approved, and nothing else on the disk, including the app's own storage. When a run needs a value or a decision from the user, the app pauses with its own forms and cards; never build a conversation with the user into the code.
