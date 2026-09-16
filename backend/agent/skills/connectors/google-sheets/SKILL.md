---
name: google-sheets
description: WHEN a workflow step needs to read from or write to a Google Sheet - the proven connection routes, the exact user setup steps and the known gotchas, so you never rediscover them live
tier: 2
kind: action
depends_on: []
version: 1.6.0
last_updated: 2026-09-10
---

# Connecting to Google Sheets

Three proven routes. Present the viable ones to the user with one-line trade-offs, your recommendation first, and let them pick before you set anything up.

First, check the workflow's attached environments for an existing Google service-account key. If one is there, lead with Route 2 using it; never walk the user through Apps Script setup while a working credential sits unused. If none exists, include the reuse angle whenever you present the fork: a user who will build more Google automations, or already has a service account, sets it up once, saves the JSON key in an environment, and every future workflow reuses it without re-entering. Recommending Apps Script for a one-off stays right; the reuse angle is what must never be left out.

## Route 1: an Apps Script web app, recommended for writes at small scale

Free, no API key, no cloud console: the sheet carries a tiny script you POST to. The setup is on the user's side, so give them these steps complete, and put the script in a fenced code block of its own so the copy box carries exactly the code:

1. Open https://sheets.new (or the existing sheet).
2. Add the header row the workflow expects.
3. Extensions, Apps Script; delete anything in the editor and paste the `doPost(e)` handler you write for the task (parse `e.postData.contents`, `sheet.appendRow(...)`, return JSON through ContentService).
4. Deploy, New deployment, type "Web app"; Execute as: Me; Who has access: "Anyone", exactly that wording, not "Anyone with Google account", which returns Google's login page to your POST.
5. Deploy, approve the authorisation prompt, copy the Web app URL.

What goes wrong, to check before debugging anything else:

- The URL must be the deployed Web app URL ending in `/exec`, never the editor or preview link. A login or consent HTML page in the response means the access setting or the URL is wrong.
- Editing the script does not update the deployment: Deploy, Manage deployments, the pencil, Version "New version", Deploy, every time.
- Google redirects the response through script.googleusercontent.com, so the step's domains need script.google.com and script.googleusercontent.com.
- The web app URL grants write access to that sheet: treat it as a secret, collected through the masked ask and stored by name.

## Route 2: the Google Sheets API with a service account, for reads at scale and robust writes

The official API, with structured errors and no redirect quirks, but the user must create a Google Cloud project (or reuse the one they made for another workflow), enable the Sheets API, create a service-account key (JSON, stored as a secret) and share the sheet with the service account's email. Right when the workflow is business-critical or needs reads and writes; heavy for a casual sheet, but it reuses: once the key sits in an environment, every later Google workflow rides it with zero setup.

A service account can never create a spreadsheet: a sheet is a Drive file and service accounts have no storage quota (the same wall as the drive guide), so creating the sheet automatically is impossible on this route; never offer it. A "permission denied" on create is this wall, not a missing API enable, so do not send the user to enable APIs for it. The sheet must exist first: the user opens https://sheets.new, names it, shares it with the service account's email as Editor, and pastes the link back. The workflow then writes to that sheet every run.

The service account's email: nobody can read it out of the stored key, not the user (a secret is never shown again) and not you (the value exists only inside a running step). Capture it as its own non-secret variable at setup, while the user still has the JSON in hand. For an existing stored key with no such variable, the email is visible without the file at https://console.cloud.google.com/iam-admin/serviceaccounts; ask the user to read it there, then save it so it is never asked again.

- Python: the `google-auth` package for a bearer token, then plain REST against `sheets.googleapis.com/v4/spreadsheets/<id>/...`.
- The domains are oauth2.googleapis.com (the token POST), sheets.googleapis.com and www.googleapis.com. Declare all of them up front or the first token fetch dies egress-blocked.

If company IT blocks it: Workspace admins can disable Apps Script or external sharing, and then the Deploy step errors or "Anyone" is missing from the access options. The one-line ask for IT: please allow Apps Script web-app deployments for my account. Or fall back to Route 2, which IT often prefers anyway.

## Rows by key, columns by header, on every route

Users sort, insert and delete rows between runs, and add columns. Read the header row once per run and build a map from header name to index; every column the step touches is looked up by name from that map, and a column that is not there is an `unexpected_input` naming what the sheet has and what the step needs, never a hardcoded letter or index and never a column-position setting. A row is identified by a key value in it (the profile URL, the email, an id column), never its row number, and the bookkeeping of what is already done (checkpoints, a done-list) keys on that value. A write-back (a timestamp, a status, a message) re-reads the sheet at write time, finds the row by key, computes the A1 range from the header map then, and writes; row numbers exist only inside that one call. Prefer writing all the values of one row in one call (a `values:update` of the row's range) over several passes over the same row.

## Route 3: the CSV export URL, read-only, zero setup

A sheet shared "Anyone with the link" reads as CSV at `https://docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=<gid>`. No auth, one fetch. Only for data the user is happy to make link-public.

## Official docs

Check here if a route fails: https://developers.google.com/workspace/sheets/api
