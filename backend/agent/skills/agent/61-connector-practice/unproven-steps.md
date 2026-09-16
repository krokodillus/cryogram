# A step that cannot be proven now: the user's decision, not a blocker

Two situations, one rule: the step ships complete, its first real run is its test, and the choice is stated plainly rather than worked around.

When the user declines the live test send, that is a decision, not a blocker. Keep the step complete on the plan, the code and the authored test it would have used (the payload build and the documented success shape), so the build lands it. Only the live send is waived: the first real run performs it behind the approval gate, and the build's receipt notes that the step was built without a recorded send. Never re-plan around it, never second-guess the choice, and never re-ask later in the same build.

When the trigger cannot be reproduced right now (a logged-out branch while the session is logged in, an error path that is not erroring), the step is untested by choice: say so plainly in one line, keep the code and the authored test on the step, and let the first real run prove it. Never manufacture the condition destructively, and never loop trying to force it.

Either way, lead with the offer, never the failure: the recommended option is one real test now, the other is to set the step up and let the user run it once. An unproven write step is one send away, and never a reason to say it could not be built, even when a build refuses for it. When a later design change makes an earlier test write stale (a row sent during proving that no longer matches the shape), say so and offer to clean it up. The opening card carries the warning, and the user's approval on it is their informed yes; that yes stands and is not revisited.
