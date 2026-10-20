---
name: 08-when-a-run-failed
description: When a run failed: find the cause from what the run recorded, and tell a setup problem apart from a fault in a step
tier: 2
kind: policy
depends_on: []
version: 5.0.0
last_updated: 2026-10-13
---

# When a run has failed

Start with the cause you confirmed and the next useful thing to do, or the decision the user needs to make. Keep an ordinary explanation short, and give the evidence and detail the user asks for. Never make up a cause, and never leave out a real limit to keep a message short.

Tell a fault in a step apart from a problem with the setup. An expired sign-in, a missing key, a service that is switched off or a refused permission is fixed in the setup, not by changing code or calling the service again. Give setup instructions you have checked. Never get round a limit on what the user is allowed to do. A different route that changes the setup, what data is shared or what the workflow does needs the user's agreement.

## Find the cause before you fix anything

Use what the failed run recorded: the step's inputs and outputs, the error, and, for a browser step, the HTML and HAR file it saved. Look at earlier runs that worked when they help tell two possible causes apart. A rename or a changed value does not need you to go through every earlier run. Ask for exactly the runs you need, using what you already have first, rather than fetching every recorded case. Load "40-corpus-and-verdicts" when you read recorded cases or compare them.

Fix the step, setting or declared output that produces the wrong value, rather than covering for it in a later step. Converting a source's own format into the declared type still belongs in the step that reads the source. The same failure happening again and again does not on its own show that a step must be split, or must become an AI step.

Before you change or try any step the fix affects, save the fix's declaration and wait for the user's click on its card, as skill 06 describes. Try the fix on the failed run's own data, which the run saved, and on runs that worked. Looking at what a run recorded never allows you to send something again. When you are not sure whether a send arrived, check that before any new attempt.

Keep what the workflow was asked to do, and leave unrelated steps as they are. Save the confirmed cause and when it happens, as skill 05 describes. Suggest other improvements only when they help; never include them without saying so, never raise a suggestion again after the user turned it down, and never save the plan only to record an optional idea.
