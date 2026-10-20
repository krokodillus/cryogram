---
name: onedrive
description: WHEN a step reads or writes files in OneDrive: private access that is already authorised, a Power Automate bridge flow for writes, or a share link for reads
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# OneDrive files

Use the direction the user asked for (read or write) without asking again. Reuse access that is already authorised if it suits the task. A Power Automate write bridge needs a work/school account that owns the flow, and tenant access to the connectors it needs. Managing cloud flows with a personal account ended in July 2025, so a personal OneDrive account alone is not enough. Check these before you give setup steps.

## Writes: a Power Automate bridge flow, when no suitable connection exists

The free trigger "When a Teams webhook request is received" is a standard connector (the plain HTTP trigger is premium), and it can drive OneDrive actions. Give the user these steps, with links they can click:

1. Go to https://make.powerautomate.com and choose Create, then Instant cloud flow, then the trigger "When a Teams webhook request is received" (Who can trigger: Anyone).
2. Add the action "Create file" (OneDrive for Business, or OneDrive for a personal account). Pick the folder. Set File Name from the trigger body (triggerBody()?['name']) and File Content to base64ToBinary(triggerBody()?['content_b64']).
3. Save the flow, copy the HTTP URL of the trigger and ask the user for it with the masked ask. The URL is the only credential, so treat it as a secret.

POST JSON with `name` and `content_b64`, an explicit timeout and one attempt. A 202 means only that the trigger accepted the request, not that the file was created. During testing, check the downstream action in the flow's run history, or read the file back with access that is already authorised, before you say the file exists. If the outcome is unclear, find out what happened before you retry. This template sends and does not wait for a result, which is enough for writes. Do not assume its limits apply to every flow you might build. Base64 makes the payload about a third bigger, so check the size limits.

## Reads: a share link, tested live

Use a link that is public to anyone only if the user agrees to that exposure. Prefer private access that is already authorised if there is any. URL shapes differ, so check the actual response in a cell before you rely on a download link. Do not assume a link to a folder is a link to one file.

- Personal accounts (1drv.ms or onedrive.live.com): add `&download=1` to the shared URL, or `?download=1` if the URL has no query yet. The URL then follows redirects to the file's bytes.
- Work or school accounts (contoso-my.sharepoint.com/...): add `?download=1`.
- The older api.onedrive.com/v1.0/shares/u!{base64url}/root/content trick is unreliable now (it gives auth errors on links in the new format). Do not try it first.
- The redirects pass through several hosts. List the share link's exact host, and every redirect host you saw, in `domains`, for example the tenant's `contoso-my.sharepoint.com` host. Wildcards such as `*.sharepoint.com` are not valid. If a redirect is newly blocked, it goes through the normal approval card. Never widen access on a guess.
- A response that is a login or consent HTML page is not file data. Look at the access settings and the redirects to find out why. Do not make a private share public on your own to fix it.

If the task needs both directions, use two steps: a read from a share link, and a write through the bridge flow. This one guide covers both.

## What goes wrong

- The bridge flow stops without any message if it is turned off or its owner leaves. Check the flow's run history first (make.powerautomate.com, My flows, the latest run). It names the action that failed.
- The "Create file" action does not overwrite by default. A file with the same name gives an error, unless the flow adds a timestamp to the name. Suggest adding a timestamp in the flow, or send a unique name each time.
- Personal accounts use the "OneDrive" connector and work accounts use "OneDrive for Business". The flow must use the one that matches where the folder is.

## If company IT blocks it

If Power Automate is disabled or sharing is restricted, give the user this one line to send to IT: please allow me an instant cloud flow with the Teams webhook trigger writing to my own OneDrive folder. If external sharing is what blocks it, ask for one "Anyone with the link" exception for one folder. Registering an app for Microsoft Graph is the alternative, and IT does that. Tell the user so plainly, and do not walk a user who is not an admin through admin consent.

## Official docs

If a route fails, check here: https://support.microsoft.com/en-us/office/share-onedrive-files-and-folders-9fcc2f7d-de0c-4cec-93b0-a82024800c07
