---
name: onedrive
description: WHEN a workflow step needs to read a file from or save a file into OneDrive (personal or work/school) - the no-admin routes, the Power Automate bridge for writes, share-link reads, and the gotchas
tier: 2
kind: action
depends_on: []
version: 1.1.0
last_updated: 2026-09-06
---

# OneDrive files

No public no-auth upload API exists, and Graph app registration is an IT workflow, so the routes split by direction. One question decides: does the workflow need to read a file from OneDrive, save files into it, or both?

## Writes: the locked-in route is a Power Automate bridge flow

The free "When a Teams webhook request is received" trigger (a standard connector; the plain HTTP trigger is premium) drives OneDrive actions. User setup, clickable:

1. https://make.powerautomate.com, Create, Instant cloud flow, the trigger "When a Teams webhook request is received" (Who can trigger: Anyone).
2. Add the action "Create file" (OneDrive for Business, or OneDrive for a personal account): pick the folder; File Name from the trigger body (triggerBody()?['name']); File Content as base64ToBinary(triggerBody()?['content_b64']).
3. Save; copy the trigger's HTTP URL and collect it through the masked ask. The URL is the credential.

The send pattern: POST JSON {"name": "...", "content_b64": "..."}; timeout=30; retry timeouts, 5xx and 429 only; success is a 202 with an empty body. The flow is fire-and-forget and cannot return data to the caller, so it serves writes, never reads. Keep payloads modest (megabytes, not hundreds); base64 grows content by about 35 percent.

## Reads: a share link, proven live

A file or folder shared "Anyone with the link" downloads without auth, but the URL shape varies by account type and Microsoft has changed link formats, so always prove the exact link in a cell before planning around it.

- Personal (1drv.ms or onedrive.live.com): append `&download=1` (or `?download=1` if there is no query yet) to the shared URL; it follows redirects to the bytes.
- Work or school (contoso-my.sharepoint.com/...): append `?download=1`.
- The older api.onedrive.com/v1.0/shares/u!{base64url}/root/content trick is unreliable now (auth errors on new-format links); do not reach for it first.
- The redirect chain crosses hosts: declare the link's host and *.sharepoint.com and onedrive.live.com in `domains`, and expect 302s with redirects followed.
- A response that is HTML (a login page) instead of bytes means the share is not truly "Anyone with the link": fix the share, never scrape the page.

If both directions are needed: a share-link read and a bridge-flow write, two steps, one guide.

## What goes wrong

- The bridge flow dies silently if it is turned off or its owner leaves. Check the flow's run history (make.powerautomate.com, My flows, the latest run) before guessing; it names the failing action.
- "Create file" overwrites nothing by default: the same name is an error unless the flow adds a timestamp. Suggest a name with a timestamp in the flow, or send unique names.
- Personal accounts use the "OneDrive" connector and work accounts "OneDrive for Business"; the flow must match where the folder actually lives.

## If company IT blocks it

When Power Automate is disabled or sharing is restricted, the one-line ask for IT: please allow me an instant cloud flow with the Teams webhook trigger writing to my own OneDrive folder. If external sharing is the block, ask for a single "Anyone with the link" exception for one folder. Graph app registration is the IT-grade alternative; say so plainly rather than walking a non-admin through admin consent.

## Official docs

Check here if a route fails: https://support.microsoft.com/en-us/office/share-onedrive-files-and-folders-9fcc2f7d-de0c-4cec-93b0-a82024800c07
