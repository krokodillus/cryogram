---
name: slack
description: WHEN a workflow step needs to post a message to Slack - the incoming-webhook route, exact user setup steps, the resilient post pattern and known gotchas
tier: 2
kind: action
depends_on: []
version: 1.3.0
last_updated: 2026-09-06
---

# Posting to Slack

## The route: an incoming webhook

The recommended route for posting. User setup, given complete:

1. api.slack.com/apps, then Create New App, then From scratch; name it and pick the workspace.
2. In the app, Incoming Webhooks, toggle it on, then "Add New Webhook to Workspace", pick the channel and copy the https://hooks.slack.com/services/... URL.
3. Collect the URL through the masked ask, as a secret named for what it is (the Slack webhook URL). The URL is the credential.

## The post pattern

POST JSON {"text": "..."} with mrkdwn formatting (*bold*, <url|label>); use blocks only when the layout genuinely needs it. timeout=15; retry timeouts, connection errors, 5xx and 429 with a short backoff, honouring the Retry-After a 429 carries; never retry any other 4xx. The success signal is a 200 with the body exactly "ok". Errors come as plain-text bodies ("invalid_payload", "channel_is_archived"); surface that word.

## What goes wrong

- One webhook is one channel, fixed at install: a "channel" field in the payload is ignored on modern webhooks (only ancient legacy ones obeyed it, so do not plan around that memory). Posting elsewhere needs another webhook, or the chat.postMessage API with a bot token, the heavier route, only when channels are dynamic.
- The rate limit is about one message a second per webhook: pace bulk sends, honour the Retry-After on a 429, and know that persistent flooding can get the app disabled. Batch many lines into one message rather than many posts.
- "no_service" or a 404 means the webhook was revoked (the app removed): a new webhook, not a retry.
- The domain is hooks.slack.com.

## If company IT blocks it

When app creation is restricted in the workspace, the "Create New App" step fails with a permissions message. The one-line ask for the workspace admin: please approve an incoming-webhook app posting to the channel, or create the webhook and share the URL with me. Webhooks are the least-privileged Slack integration, and admins can create them directly at https://api.slack.com/apps.

## Official docs

Check here if a route fails: https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/
