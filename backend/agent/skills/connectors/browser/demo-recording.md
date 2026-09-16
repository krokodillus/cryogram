# Demo recording: the user shows the flow, the app captures it

For an undocumented system the user must demonstrate (an internal portal, a flow only they know), record the network traffic of their session while they click through it once. It replaces hand-exported developer-tools recordings, with zero setup for the user.

## Consent and scope

- Ask first, plainly: you will open a window and record what the site sends while they do the task once, credentials are masked before anything is stored, and they do the task and then confirm. No recording without that yes.
- Never record a login: the session must already exist (the browser-login flow first). A recorded login would carry the password.
- The recording runs only between the window opening and their confirmation; the window closes as soon as the cell ends.

## The cell

The window is the app's, and it records every request and response by itself; the demonstration is one browser cell that opens the window on screen and waits for the person:

```python
browser_goto(page, "https://portal.example.com/start")
confirm_with_user("Do the task once in this window, then confirm.",
                  page=page)
write_output("done", True)
```

`page` is handed to you, already open. `confirm_with_user` brings the window on screen and returns when they confirm; closing the window or nobody answering pauses the run the ordinary way. The app closes the window when the cell ends, which is what completes the recording, and the cell's result names what the window kept under `$capture`: the recording as `har`, and the pages it settled on.

## Import and mine

- `import_recording(from_blob=<the har reference under $capture>)` turns it into a scrubbed "recording" sample: credential values become shape descriptors, the field that carried one survives, and the hostnames it touched come back for `domains`.
- Mine it per 25-recordings, and test candidate calls with jar-auth (browser/har-capture). The session scope rule applies: the declared task only.
