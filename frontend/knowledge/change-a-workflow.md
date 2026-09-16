# Change or fix it

Describe changes in the workflow's chat and review **Here's what will change** before approving them. If a run stops with a problem, open **Issues** and choose **Investigate**; once a fix is ready, **Continue** resumes the run. If a send was not confirmed, check whether it arrived before repeating it. All changes are tracked in the workflow's **Version history**, so you can always revert back if needed.

After a change, **Here's what changed** shows the result. You can also click an AI step on the canvas to edit its prompt or model directly. These edits are not tested; the next run is where they are first used.

Open **Information > Version history** and choose **Restore** beside a previous version to return to it. Restoring keeps your current saved values, chat and run history. Versions are numbered; the restored row records which version it returned to, and the next change gets a new number.

When an issue shows **Ready**, **Continue** resumes the failed run and **Start from beginning** starts again. **Continue the failed run** in the chat does the same as **Continue**. **Dismiss** leaves the issue without further action.

If a step processes a list and stops partway, you can retry the failed items, continue with the successful items while the rest wait in a separate run, or repeat the whole step if the successful items were discarded by the other system. Check which items arrived before repeating sends.
