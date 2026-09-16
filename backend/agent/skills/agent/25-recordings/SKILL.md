---
name: 25-recordings
description: WHEN an undocumented external system must become scriptable - capture its traffic (an app-recorded HAR/demonstration, or an uploaded HAR) and derive the call contract from the recording sample
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-09-06
---

# Recordings: the user showed you once

An external system with no public API and no documentation becomes scriptable from a recording of it being used. The app captures the recording itself, so offer these routes in this order. When you already drive a browser session, record the traffic while browsing (load "browser/har-capture") and import it with import_recording; the user does nothing. When the user has to show the flow, the demo recorder (load "browser/demo-recording") records their one demonstration over the app's own window, after their explicit yes. Only as a fallback do they export a HAR file from their browser's developer tools ("Save all as HAR") and upload it; the app recognises a recording upload and scrubs it the same way.

A sample of kind "recording" holds `actions` (the clicks, with timestamps) and `calls` (the requests and their responses). Credential values are already masked to "<redacted, N chars>"; the field that carried one is your sign of where the authentication happens.

Working a recording:

- A recording is large. read_sample gives a capped preview and the file's path; read the rest selectively, filtering by url or method, and never dump it whole.
- Correlate clicks with calls: the click at one moment caused the calls just after it. That separates the task's calls from page noise such as assets and analytics, which you ignore.
- Distil the contract of the calls that matter: method, url shape, required parameters, request and response structure. That contract goes into the connector step's code; the raw recording is never read at run time.
- Follow the authentication: where a masked field first appears in a response (the login or token endpoint) and where it is then sent (a header, a cookie, a parameter). The credential is a secret the user provides by name: ask for the name, never the value, and never quote a masked value's surroundings in a way that invites pasting the real secret into the chat.
- The hostnames the demonstration touched are the plan step's `domains`; carry them, or the connector is blocked from reaching them.
- Seed the connector's tests from real request and response pairs, masked values staying masked. One demonstration is one example: treat an uncertain field as a question for the user, not a guess.
