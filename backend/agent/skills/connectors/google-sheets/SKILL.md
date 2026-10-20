---
name: google-sheets
description: WHEN a step reads or writes a Google Sheet: how to choose between an Apps Script web app, the Sheets API with a service account, and the CSV export link
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Connecting to Google Sheets

Use a connection that already exists, or the route the user chose. Compare routes only when more than one of them works and they really differ in setup, in what the step can do, or in how much of the user's data they share. Do not make the user choose between all three routes for every Sheets task.

First check the environments attached to the workflow for a Google service-account key. If one is there, offer Route 2 with it first, and do not walk the user through Apps Script setup while a working key sits unused. If there is none, always tell the user about the reuse option when you offer the choice: a user who will build more Google automations, or who already has a service account, sets it up once, saves the JSON key in an environment, and every later workflow uses it without entering it again. Recommending Apps Script for a one-off is still right, but always mention the reuse option.

## Route 1: an Apps Script web app, recommended for writes at small scale

It is free and needs no API key and no cloud console: the sheet carries a small script that your step sends a POST request to. The setup happens on the user's side, so give them these steps in full, and put the script in a fenced code block of its own so the copy box holds exactly the code:

1. Open https://sheets.new (or the existing sheet).
2. Add the header row the workflow expects.
3. Click Extensions, then Apps Script. Look at the existing project first. Keep its scripts, triggers and deployments, and do not overwrite an existing doPost without first talking with the user about who relies on it. The handler checks a shared secret against Script Properties before it writes to the intended sheet, then returns an explicit JSON result saying what happened. Collect the matching secret for the caller through the masked ask; never write it in the prompt or in example code.
4. Click Deploy, then New deployment, and choose the type "Web app". Set Execute as: Me. Set Who has access: "Anyone", in exactly that wording. Do not choose "Anyone with Google account": Google then answers your POST with its login page.
5. Click Deploy, approve the authorisation prompt, and copy the Web app URL.

What goes wrong, to check before you debug anything else:

- The URL must be the deployed Web app URL ending in `/exec`, never the editor link or the preview link. If the response is a login or consent page in HTML, the access setting or the URL is wrong.
- Editing the script does not update the deployment. Every time, click Deploy, Manage deployments, the pencil, set Version to "New version", and click Deploy.
- Google redirects the response through script.googleusercontent.com, so the step's `domains` must include script.google.com and script.googleusercontent.com.
- A deployment open to Anyone can be called by anyone. Keep the endpoint private and make the handler check the shared secret before it does anything; a URL that is hard to guess is not enough to protect it. Store the endpoint and the token, each under its own name.

## Route 2: the Google Sheets API with a service account, for reads at scale and robust writes

This is the official API. It gives structured errors and has no redirect problems. The user must create a Google Cloud project (or use the one they made for another workflow), enable the Sheets API, create a service-account key (a JSON file, stored as a secret), and share the sheet with the service account's email address. Use it when the workflow is business-critical or needs both reads and writes. It is a lot of setup for a casual sheet, but it can be reused: once the key is in an environment, every later Google workflow uses it with no setup.

A service account has no storage quota in a personal My Drive, so it cannot create files there: use a sheet the user already owns and share it with the service account as Editor. This does not mean a service account can never create a file. In a Workspace shared drive that the account has permission for, it can create files that the shared drive owns. Before you offer that route, check the destination and the API call that creates the file. If you get a 403, work out the cause from the actual response, and do not assume it is always the quota.

Nobody can read the service account's email address out of the stored key: not the user (a secret is never shown again) and not you (the value exists only inside a running step). During setup, while the user still has the JSON file, save the email address as its own setting that is not a secret. If a key is already stored with no such setting, the email address is shown, without the file, at https://console.cloud.google.com/iam-admin/serviceaccounts. Ask the user to read it there, then save it yourself, so it is never asked again.

- Python: use the `google-auth` package to get a bearer token, then plain REST calls to `sheets.googleapis.com/v4/spreadsheets/<id>/...`.
- The domains are oauth2.googleapis.com (the token POST), sheets.googleapis.com and www.googleapis.com. Declare all three from the start, or the first token request is stopped as egress-blocked.

If the company's IT department blocks it: Workspace admins can switch off Apps Script or external sharing, and then the Deploy step gives an error, or "Anyone" is missing from the access options. The one-line request for IT is: please allow Apps Script web-app deployments for my account. Or use Route 2, which IT often prefers anyway.

## Rows by key, columns by header, on every route

Users sort, insert and delete rows between runs, and add columns. Read the header row once per run and build a map from each header name to its column index. Look up every column the step uses by name in that map. If a column is missing, raise `unexpected_input` naming the columns the sheet has and the columns the step needs. Never write a column letter or index in the code, and never make the column position a setting. Identify a row by a key value in it (the profile URL, the email, an id column), never by its row number, and track what is already done (checkpoints, a list of done items) by that key value. To write back (a timestamp, a status, a message), read the sheet again at the moment you write, find the row by its key, work out the A1 range from the header map then, and write. Row numbers exist only inside that one call. When you write several values to one row, prefer writing them all in one call (a `values:update` of the row's range) to going over the row several times.

## Values read back are text

On every route, cells come back as text by default: a number as "240", a tick box or a written true or false as "TRUE" or "False", a date as the sheet shows it. When a step reads rows back, including rows the workflow wrote in an earlier run, convert each value to the type its output declares, in the code that reads it, so that a row read from the sheet matches a row the step builds itself. On the Sheets API, `valueRenderOption=UNFORMATTED_VALUE` returns numbers and true or false as real numbers and booleans. Text and dates still need the step's own conversion.

## Route 3: the CSV export URL, read-only, no setup

A sheet shared as "Anyone with the link" can be read as CSV at `https://docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=<gid>`. It needs no login and one request. Use it only for data the user is happy to make public to anyone with the link.

## Official docs

If a route fails, check here: https://developers.google.com/workspace/sheets/api
