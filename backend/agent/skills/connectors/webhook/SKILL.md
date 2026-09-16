---
name: webhook
description: WHEN a workflow step needs to call a generic webhook or plain HTTP API endpoint the user provides - the resilient request pattern, verification and gotchas
tier: 2
kind: action
depends_on: []
version: 1.1.0
last_updated: 2026-09-06
---

# Generic webhooks and plain HTTP endpoints

The catch-all route when the target is just a URL the user owns: their own service, a Zapier, Make or n8n hook, IFTTT.

## Setup

The URL is usually a credential, since possessing it is permission to post: collect it through the masked ask unless the user says it is public. Any separate token or key is always a masked ask. Ask the user what a successful response looks like (status and body). If they do not know, send one agreed test request during exploration and read the answer together. Never guess the contract.

## The request pattern

An explicit timeout of 15 to 30 seconds. Retry timeouts, connection errors, 5xx and 429 with a short, bounded backoff; never retry any other 4xx, because the request is wrong and needs fixing. Set Content-Type explicitly, send JSON, and parse the response body for the service's own success or error signal: a 200 with {"error": ...} is a failure. Follow at most one redirect, and declare both hosts in `domains` if the service redirects, which test hooks often do.

## What goes wrong

- Zapier and Make test hooks return 200 for anything. Verify that the action happened (ask the user to check once during exploration), not just the status.
- A trailing slash and http versus https are real 404s and 301s: store the URL exactly as pasted.
- Payload size limits are common (about 1 MB). Send references or trimmed content, not whole documents, unless the contract says otherwise.
