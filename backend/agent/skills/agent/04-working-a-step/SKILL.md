---
name: 04-working-a-step
description: Doing each step for real as a cell on the real sample - what a cell may and may not do, probing without waste, leaving working code alone, opening a browser window, and testing a step that sends something for real
tier: 2
kind: policy
depends_on: []
version: 4.4.0
last_updated: 2026-09-15
---

# Working a step

Do the task yourself on the real sample, and work each real step as a cell named after that step. The run is recorded (the code, its inputs and its output; that recording is the step's evidence), and a step of the same name carries that code and that recording as its first test when you save the plan.

A try is judged exactly as a run is. Before the code runs, the step's inputs are checked against the types its plan step declares, so a setting stored as text where a number is declared fails the try with the same sentence a run would give; afterwards the outputs are checked the same way, so a value that came back empty or in another shape fails the try too. A failed try is kept as one. When a value is the wrong type, the fix is at the port or at the setting that owns it, never a conversion written into the step that reads it. When an output may genuinely be empty some runs, say so on the port; when it should not be, make the step produce it.

Everything you do while working a step must be something you could write as code afterwards. That is the point: a step worked out as a cell repeats by construction, because the run executes the same code in the same place. Anything done by hand or by clicking about proves nothing and can never become a step. Prefer a command or an API to anything interactive, and read a recording of the network traffic to find the call behind the clicks.

Look at the sample first, form the smallest hypothesis you can, and test it by doing it rather than reasoning about it. The cell is your computer here, so run the parse, call the API, fetch the page.

While building, work a step that loops over items on a sample of 2, unless there is a reason to use more, and say the reason. The sample goes in through the step's maximum setting, passed as 2 in the cell's inputs; the step's code reads that setting and never cuts the list to a number itself, so the built step works through as many items as the setting allows.

Load the connector guide for an outside tool before searching the web, and search only for what the guide does not answer. The guide carries the routes that work, the exact setup steps for the user, and the traps. A website that answers a plain fetch with a refusal or a bot check has a guide too, the browser one.

Two rows, posts or pages is the standard while working a step, passed explicitly rather than by using the workflow's real cap. One pass along the path where everything goes right, and then build. Volume and the awkward cases belong to real runs.

## What a cell's code may do

A cell reads its values by name from the first attempt, named exactly as the plan step's inputs. Never write a value into the code meaning to swap it for a real input later: the swap changes the code, which throws away the test and makes the step run for real again.

Step code speaks a fixed set of calls: read an input, write an output, get a secret, record a checkpoint, and write a file. Writing a file hands back a reference, and it is that reference a file-typed output carries; plain text on a file port never becomes something the user can download.

Step code can never talk to the user. A saved workflow runs with no chat and no model, and the product owns every pause a run has: the approval in front of a write, a step that asks the user for a value, the form when a value is missing. Code that wants to ask the user something in the middle is designed wrongly, because the moment it wants already exists as a step.

Switching approach inside a step is invisible to the user, so just do it. If the switch changes what the workflow does, how robust it is, or what the user has to set up, ask about that one step, your recommendation first; the rest of the plan stands. Reshaping a step the user has already accepted, such as splitting it, changes what they agreed to, so say what you saw and ask, in the same turn, before rebuilding it (07 says why a step is never split into copies of itself).

A change that only moves where a value comes from, such as a per-run question becoming a stored setting, is a value declaration, a dropped step and an amended save. No code changes and nothing is tested again, because the input finds the value by name. Test again only a step whose code truly changed, once and on its own: never the whole chain, and never a browser window for steps you did not touch.

## Probing without waste

A probe is a cell that only looks, to answer a question of yours. One cell per question, never one per fact: a probe can look at several things and write one labelled result. Each extra call costs about twenty-five seconds, so keep a probe you know is slow on its own call, and never put anything with an outside effect in a batch: a write always stands alone and says plainly what it does.

A probe carries a plain name saying what it checks, never the name of a plan step, because a cell named after a step becomes that step's own test. Every probe passes a one-sentence reason, which is the line the user watches, so a detour reads as purposeful work rather than as poking about.

The same economy holds for your own reads. The calls that orient you at the start of a turn go in one round together, never one per round. Your context already carries the workflow's outline and every write hands back the updated state, so read a step again only when a result surprises you, and read an uploaded document again only when you need its contents, never to reorient. Never use a cell or a model call to find out something your context already tells you, such as which models are set up or which samples are stored. To see what a built workflow would do, where it pauses and where each value comes from, walk it yourself rather than asking the user what their screen showed.

Reads that do not depend on each other (a step, the step list, recorded cases, samples, a guide) go as several calls in one response, never one per round, because every response is a round trip the user waits through and everything already said is read again on every later one. Your most recent turns are replayed in full at the start of each turn, so a guide, a step or a recording read there needs no second look. Never re-type what you already have: a cell's code and its recorded run belong to the step by themselves, so a save never carries code or tests for a step a cell ran, and a re-save is an amendment carrying only the steps that changed.

A cell blocked from reaching an address raises the question with the user by itself, and when the answer comes back the cell runs again. Never route around a blocked cell, because the step has to work through the connection it will really use.

Nothing spins with nothing showing. Every wait has a bound, and the bound ends in a plain message or a question to the user; a step may wait on the user, never past them.

Samples can hold real personal or commercial data. It stays where the work happens, and reaches the chat only as far as the user needs it to judge the result.

## Code that already works

A remark about the style of code that already ran is information and never a reason to rewrite it. Rewriting throws away the recorded run, puts the step back through the test, and for a browser step costs the user another sign-in. Rewrite code that already works only when its behaviour has to change, and then extend what is there, by wrapping it or adding the branch, rather than writing the step again; what is genuinely new is the few lines that are new.

Never run something again to check what the code plainly makes true: an output's field names, a renamed input, a line you removed. Reading the text tells you, and for a send or a window the extra run costs a real action.

When your own change forces a step the user watched pass to be tested again, say so in one line before you run it.

A step whose trigger you cannot reproduce right now, such as the signed-out branch while the session is signed in, is untested on purpose. Say so plainly, keep its code and a written test on the plan, and let its first real run be the test. Never force the condition destructively and never loop trying to make it happen.

## Opening a browser window

The first window for a step asks the user, and every launch says why it is opening. A yes covers that window; for a step that only reads, the user can also say yes for every window on that site until the build is done, and a step declared as sending asks for every window. Any action that needs the user at their machine gets a question immediately before it happens, every time, even when they agreed to the route earlier.

Working out a page usually takes more than one window: whether scrolling loads more, how the next page of a list is fetched, what a call needs to be accepted. Open another window when the next question needs the live page, after reading what the earlier windows kept, and say in `reason` what this window is for. Each window's recording is numbered and kept beside that reason for the rest of the build, so compare windows (`"$recorded:har#2"` against `"$recorded:har#3"`) rather than opening the page again for something one of them already shows.

Plan the pages a phase of the work needs and visit them in the same window where you can, because each launch is another signal to the site's bot detection and, on a site the user is signed in to, a risk to the session. The main step's own cell does that step's real job in the shape it will keep and captures the raw pages every other step needs, so the rest work from the recordings with no window at all. Where several methods might work, try them in the same window and keep each capture.

On a site the user is signed in to, opening a page is an action other people may see, because the site records it. Visit as few pages as answer the question, read the recorded page again rather than fetching it a second time, and never look around the site to test an idea without first telling the user what you will open and why.

## Sending something for real

A step that has an effect in the world is tested by one real send with a small throwaway payload, never by pushing the full output into live data. Run the cell and let the approval question ask: never ask the user beforehand whether you may test it, and never ask for a blanket yes. The one-line reason you pass with the cell is what they read; its summary says what the action really does, and you never split an action into pieces to make it look harmless.

A no settles that step: say so once, in one line, and build. The first real run performs the send behind its own approval.

A step added deep into a built workflow, which only a real run can feed, is never tested with a row you invented. Either one throwaway send now, if the user says yes, or nothing now and the first real run tries it; if that run stops there, the repair works on the run's own data until it passes (08).

A write is one attempt. Once a send has fired, or you do not know whether it fired, it is never fired again to work out what happened. Work that out from what was recorded and from what the user can see.
