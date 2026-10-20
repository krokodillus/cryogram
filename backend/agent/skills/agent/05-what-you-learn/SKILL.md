---
name: 05-what-you-learn
description: What to save for later: facts about this workflow, and learnings that hold for other workflows too
tier: 2
kind: policy
depends_on: []
version: 5.0.0
last_updated: 2026-10-13
---

# What you save for later

Save decisions that will still hold next time and findings you have confirmed, not a record of everything you saw. When a fact is already saved, do not save it again. Mark a guess as a guess until a try shows it is true.

## Facts about this workflow

Save, with `save_intent`, the choices, limits, the shape of the source's data and the confirmed causes of problems that belong to this workflow, so later turns have them. Save a correction from the user in the same turn they give it. Update the purpose and the run instructions when what the workflow does, or what it needs set up, changes. Save only the data needed to explain a finding: no credentials, and no personal details it does not need.

## Learnings that help other workflows

Save a learning with `save_learning` when something you acted on and confirmed holds beyond this workflow: a limit a service has, a fix that worked, or something an API needs that is not obvious. One failure you could not explain is not a rule for every case: say under which conditions the learning holds, and what showed it. A learning is only published when the build finishes successfully. If a learning turns out wrong before then, save it again under the same title with the corrected text, or it is published as it stands.

A learning is shared with every workflow, so it must not contain the names of workflows or companies, private links, ids, file paths, credentials or text copied from a sample. If the lesson cannot be told without them, save it as a fact about this workflow instead. Name the situation in the title, and say in the first line when it applies. To change a learning, save it again under the same title, rather than adding a second one that disagrees with it.

The learnings that match the task are already given to you; load another only when you need it. What you see now, and the list of functions available to step code, outrank an old learning. When something you see contradicts a learning, correct it or narrow it, and do not follow a route it describes that has been shown not to work.
