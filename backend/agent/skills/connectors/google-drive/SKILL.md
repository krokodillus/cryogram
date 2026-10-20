---
name: google-drive
description: WHEN a step reads files from Google Drive or writes files to it: downloading by share link, a service account, an Apps Script bridge, and who owns an uploaded file
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Reading and writing Google Drive files

Check the attached environments for a credential that suits the operation and the destination. A service account can read files that are shared with it, and can write to a Workspace shared drive it has permission for. It cannot own uploads in a personal My Drive. Use a suitable credential that already exists, and explain only the setup routes that fit this user's account.

## Route 1: a share-link read, no credential, for known files

Use this only when the user has agreed that making the file available to anyone with the link is acceptable: they share it as Viewer and give you the copied link. For sensitive files, prefer private access that is already authorised. Read the file id from the link's actual form; do not assume every share link contains /d/.

- Download from `https://drive.usercontent.google.com/download?id=<ID>&export=download&confirm=t` (any value for confirm works; follow redirects). This skips the old virus-scan warning page entirely, so do not write the confirm-token scraping that older tutorials show.
- Google-native files (Docs, Sheets) cannot be downloaded this way. Use the export endpoints: `docs.google.com/spreadsheets/d/<ID>/export?format=csv` and `document/d/<ID>/export?format=txt`.
- A download worked when the response is a 200 with a content-disposition attachment header. Pages saying "no access" or "quota exceeded" also come back as a 200 with an HTML body, so check that the body does not start with `<!DOCTYPE html>` before you treat it as the file.
- Some share links on certain older files contain `resourcekey=` (from Google's 2021 security update). Pass it on in your request, or access fails.
- The domains are drive.google.com, drive.usercontent.google.com and docs.google.com, plus the redirect hosts under googleusercontent.com.

## Route 2: a service-account read, for private folders and for listing files

It needs no OAuth consent screen and no verification, but the user makes a one-time trip to Google Cloud. They open console.cloud.google.com, create a project (or use the one they made for another workflow), enable the Drive API, then go to Service accounts, Create, Keys, Add key, JSON, download the file, and give you its content through the masked ask. Then they share the folder with the service account's `...@...iam.gserviceaccount.com` address as Viewer. During setup, save that email address as its own setting that is not a secret, because nobody can read it out of the stored secret later, neither the user nor you. For an existing key without such a setting, the address is shown at https://console.cloud.google.com/iam-admin/serviceaccounts.

- Python: use the `google-auth` package to get a bearer token, then plain REST calls: `www.googleapis.com/drive/v3/files?q='<FOLDER_ID>'+in+parents` and `files/<id>?alt=media`. Page through results with nextPageToken.
- The domains are oauth2.googleapis.com and www.googleapis.com.

## Route 3: writes to a personal My Drive through Apps Script

A service account has no storage quota in a personal My Drive. To write there as the user, use a route the user authorises with OAuth, or an Apps Script bridge. The bridge below is a simple choice when no suitable credential exists. For Workspace shared drives, use a service account only after you have checked that it is a member with write access and that the shared-drive API supports the operation. Do not propose a new bridge when you do not need one.

1. Open script.google.com and make a new project, or add to an existing project in a way that breaks nothing. Write a `doPost` that checks a shared secret from Script Properties, then decodes the data, writes only into the intended folder, and returns an explicit result. Keep the existing handlers and triggers. Collect the matching secret for the caller through the masked ask.
2. Deploy it as a web app that runs as the user, with only the access this route needs. If you choose Anyone, the handler's own authentication check is required: the URL alone does not control access. Store the endpoint and the caller's token, each under its own name.
3. POST JSON {filename, mimetype, data(base64)}; timeout=30.

What goes wrong:

- The /exec URL answers with a 302 redirect. Follow redirects, and allow both script.google.com and script.googleusercontent.com.
- To change the script, open Manage deployments and make a new version of the same deployment. A new deployment gets a new URL and breaks the stored secret.
- Base64 makes the data about a third bigger. Check the current request and quota limits for the file sizes you need, and do not treat a limit from an old example as a guarantee.
- A failure can come back as HTML with HTTP 200 (an expired authorisation shows a sign-in page), so the only sign of success is the script's own JSON result.
- Files are owned by the user and count against the user's quota, which is what you want.

## If the company's IT department blocks it

When a Workspace admin restricts Apps Script or external sharing, the deploy or share step is refused. The one-line request for the admin is: please allow an Apps Script web app on my account writing to the folder, or provide a shared drive the tool may use.

## Official docs

If a route fails, check here: https://developers.google.com/workspace/drive/api/guides/manage-downloads
