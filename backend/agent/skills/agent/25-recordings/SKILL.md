---
name: 25-recordings
description: WHEN no documentation says how to call a service, and you work it out from a HAR file or from the user showing the steps
tier: 2
kind: action
depends_on: []
version: 3.0.0
last_updated: 2026-10-13
---

# Recordings: work out a request from the traffic you saw

Use a recording when neither the service's documentation nor the current guide says how to make the request you need. A recording shows one example the user was allowed to make. It does not show what else the service can do, and it is not permission to call every host in it.

A browser try already records the page's traffic: load "browser/har-capture" to read it. When the user must show you steps nobody has documented, load "browser/demo-recording", which says how to ask them and what is recorded. A HAR file the user uploads is a last resort, never a chore you give them by default.

`read_sample` returns a short preview of a sample and its stored file reference. Read the requests you need in a code try, filtered by address, method or time, rather than printing the whole recording. Match what the user did to the requests it caused, and tell the requests that carry the task's data apart from tracking and page files. Write down the method, the address, the parameters, how the request signs in, and what its answer means. The saved workflow uses that description of the request, not the recording you explored.

Credentials in a recording (passwords, keys, tokens and cookies) are masked. A masked field shows how the request signs in; it is never a credential you can use. Ask for a missing credential with the masked question card. Never read the browser profile's private files or session files, and never put a cookie value in an output. A request that needs the browser's signed-in session stays in the browser step, unless the user gives a credential a connector step can use.

Declare only the hosts the connector step needs for the task, including an address it is redirected to on the way, not every host in the HAR file. Keep the real masked requests and answers as evidence and as tests. When you are not sure what a field means, find it in the documentation or run a small try that answers it. Ask the user only about facts that are theirs.
