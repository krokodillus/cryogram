---
name: webhook
description: WHEN a step calls an HTTP address the user gave you (their own service, or a Zapier, Make, n8n or IFTTT hook): how to store the address, how to send the request, and how to check that the other side did the work
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Generic webhooks and plain HTTP endpoints

Use this guide when the target is only an address the user controls: their own service, or a Zapier, Make, n8n or IFTTT hook.

## Setup

An address or token that gives access is a credential. Ask for it with the masked ask (the card that hides what the user types), and store it as a secret. A user telling you the endpoint is public does not make its token any less secret. To learn what the endpoint answers with, use the documentation the user gave you or what the recorded tries show, and ask the user only if you still do not know. You may make one approved test request to find out the shape of the request. An answer of 200 or 202 does not prove that the work behind the endpoint is finished. Do not assume or promise a delivery guarantee that the service does not give.

## How to send the request

Set an explicit timeout of 15 to 30 seconds. Send a POST that has an effect only once, unless the receiving service promises to ignore duplicates and you send a fixed idempotency key with it. A request that times out may still have been accepted, so find out whether it was before you send it again. A request that only reads may be retried with the retry helper from the connector-practice guide. Set the Content-Type header yourself and send JSON. Read the response body for the service's own success or error answer: a 200 response with {"error": ...} in the body is a failure. Follow one redirect at most. Test hooks often redirect, so when the service does, declare both hosts in `domains`.

## What goes wrong

- Zapier and Make test hooks answer 200 to anything. Do not trust the status code. During the build, ask the user once to check that the action really happened.
- A missing or extra slash at the end of the address, and http instead of https, give 404 and 301 answers. Store the address exactly as the user pasted it.
- When the size of the request matters, look up the endpoint's own size limit: webhooks have no common limit. Never cut out data the request needs, and never replace it with a link the endpoint cannot open, unless the user agrees.
