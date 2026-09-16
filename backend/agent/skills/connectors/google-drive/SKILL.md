---
name: google-drive
description: WHEN a workflow step needs to read files from or write files to Google Drive - share-link and service-account reads, the Apps Script write bridge (service-account writes are dead), gotchas
tier: 2
kind: action
depends_on: []
version: 1.4.0
last_updated: 2026-09-10
---

# Reading and writing Google Drive files

First, check the workflow's attached environments for an existing Google service-account key. If one is there, lead with the route that uses it. If none exists and you present a fork, include the reuse angle: a user who will build more Google automations, or already has a service account, sets it up once, saves the JSON key in an environment, and every future workflow reuses it without re-entering.

## Route 1: a share-link read, zero credential, for known files

The user right-clicks the file, Share, "Anyone with the link" as Viewer, and copies. The file id is the segment after `/d/` in the URL.

- Fetch `https://drive.usercontent.google.com/download?id=<ID>&export=download&confirm=t` (any confirm value; follow redirects). This skips the old virus-scan interstitial entirely; do not implement the confirm-token scrape from older tutorials.
- Google-native files (Docs, Sheets) do not download this way. Use the export endpoints: `docs.google.com/spreadsheets/d/<ID>/export?format=csv` and `document/d/<ID>/export?format=txt`.
- The success signal is a 200 with a content-disposition attachment. The trap: "no access" and quota pages come back as a 200 with an HTML body, so check that the body does not start with `<!DOCTYPE html>` before treating it as the file.
- Some share links carry `resourcekey=` (the 2021 security update, on certain older files): forward it or access fails.
- The domains are drive.google.com, drive.usercontent.google.com and docs.google.com, plus the redirect hosts under googleusercontent.com.

## Route 2: a service-account read, for private folders and listing

No OAuth consent screen or verification, but a one-time Google Cloud detour. The user opens console.cloud.google.com, creates a project (or reuses the one they made for another workflow), enables the Drive API, Service accounts, Create, Keys, Add key, JSON, downloads it and stores its content through the masked ask. Then they share the folder with the service account's `...@...iam.gserviceaccount.com` address as Viewer. Capture that email as its own non-secret variable at setup, because nobody can read it out of the stored secret later, not the user and not you; for an existing key without one, it is visible at https://console.cloud.google.com/iam-admin/serviceaccounts.

- Python: the `google-auth` package for a bearer token, then plain REST: `www.googleapis.com/drive/v3/files?q='<FOLDER_ID>'+in+parents` and `files/<id>?alt=media`. Paginate with nextPageToken.
- The domains are oauth2.googleapis.com and www.googleapis.com.

## Route 3: writes through the Apps Script bridge, the only personal-account route

Service-account uploads are dead on a personal My Drive (since 2025 a 403 "Service Accounts do not have storage quota"; shared drives fix it, but consumer accounts cannot create shared drives). Do not spend a round discovering this; go straight to the bridge:

1. script.google.com, New workflow, a `doPost` that base64-decodes `data`, calls `DriveApp.getFolderById(FOLDER_ID).createFile(blob)`, and returns JSON `{ok: true, id: ...}`.
2. Deploy, New deployment, Web app, Execute as Me, access Anyone; authorise; copy the /exec URL, which is the credential, collected through the masked ask.
3. POST JSON {filename, mimetype, data(base64)}; timeout=30.

What goes wrong:

- The /exec URL redirects with a 302: follow redirects, and allowlist both script.google.com and script.googleusercontent.com.
- An edit needs Manage deployments, then a new version on the same deployment; a fresh deployment mints a new URL and breaks the stored secret.
- Six minutes per execution and a payload cap of about 50 MB, with base64 inflating by a third, so keep a single upload under about 30 MB raw.
- A failure can be HTML with HTTP 200 (an expired authorisation shows a sign-in page), so the success signal is the script's own JSON envelope.
- Files land owned by the user, on their quota, which is exactly right.

## If company IT blocks it

When a Workspace admin restricts Apps Script or external sharing, the deploy or share step refuses. The one-line ask for the admin: please allow an Apps Script web app on my account writing to the folder, or provide a shared drive the tool may use.

## Official docs

Check here if a route fails: https://developers.google.com/workspace/drive/api/guides/manage-downloads
