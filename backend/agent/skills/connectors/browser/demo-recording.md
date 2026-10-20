# Demo recording: the user shows the steps, the app records them

Use this when the task runs on a system you have no documentation for, such as an internal portal or a process only the user knows. The user goes through the task once in a browser window, and the app records every request the site makes while they do it. The user does not need to set anything up or export anything themselves.

## Ask first, and what is recorded

- Ask the user before you record, in plain words: you will open a window and record what the site sends while they do the task once, logins and passwords are masked before anything is saved, and they confirm when they have finished. Do not record without their yes.
- Run the workflow's sign-in step (see the "browser-login" guide) before the demonstration, so that typing the password is not part of the recording. The app records and masks the browser's traffic by itself. Do not tell the user that the recording can be switched off for parts of the demonstration: your code cannot do that.
- The recording runs from when the window opens until the user confirms. The window closes as soon as the try ends.

## The try

The app opens the window and records every request and response. The demonstration is one browser try that opens the page and waits for the user:

```python
browser_goto(page, "https://portal.example.com/start")
confirm_with_user("Do the task once in this window, then confirm.",
                  page=page)
write_output("done", True)
```

Your code gets `page` already open. `confirm_with_user` brings the window to the front and returns when the user confirms. If they close the window or nobody answers, the run pauses as usual. The app closes the window when the try ends, which finishes the recording, and the try's result lists what was saved under `$capture`: the HAR file as `har`, and the HTML of the pages the window ended on.

## Import it and read it

- Call `import_recording(from_blob=<the har reference under $capture>)`. It saves the recording as a "recording" sample: every login value is replaced by a description of its shape, the name of the field that carried it stays, and the result lists the hosts the site used, for the step's `domains`.
- Read it as the "25-recordings" and "browser/har-capture" guides describe. A request that only works because the browser is signed in stays inside the browser step; a connector step needs its own credential that the user has given. Use the recording only for the task the user asked for.
