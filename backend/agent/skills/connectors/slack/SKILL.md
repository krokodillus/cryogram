---
name: slack
description: WHEN a step posts a message to Slack: how to set up an incoming webhook for one fixed channel, how to post to it, and what makes a post fail
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Posting to Slack

## Use an incoming webhook

If the user already has a working webhook or bot that posts to the channel you need, use it. An incoming webhook is a simple way to post to one fixed channel. When the step must choose the channel on each run, it needs a different route through Slack's API. If a new webhook is needed, tell the user to do these steps:

1. Go to api.slack.com/apps, choose Create New App, then From scratch. Name the app and pick the workspace.
2. In the app, open Incoming Webhooks and switch it on. Choose "Add New Webhook to Workspace", pick the channel, and copy the https://hooks.slack.com/services/... address.
3. Ask for the address with the masked ask (the card that hides what the user types), and store it as a secret with a name that says what it is, such as the Slack webhook URL. The address is the credential: anyone who has it can post to the channel.

## How to post

Send POST with a JSON body {"text": "..."} and format the text with mrkdwn (*bold*, <url|label>). Use blocks only when the message needs a layout that text cannot give. Set `timeout=15` and send the request once. If it times out, the connection fails, or Slack answers 5xx or 429, stop and check whether the message arrived before you send it again, because Slack does not promise that a repeated webhook post is safe. The post worked when Slack answers 200 with the body exactly "ok". An error comes back as plain text, such as "invalid_payload" or "channel_is_archived". Show that word to the user.

## What goes wrong

- A webhook posts to one channel, which is fixed when the webhook is created. A "channel" field in the payload is ignored by current webhooks. Only very old webhooks obeyed it, so do not rely on it. To post to another channel, create another webhook, or use the chat.postMessage API with a bot token. The bot token is more work to set up, so use it only when the channel must change on each run.
- Slack allows about one message a second for each webhook. Space out a large batch of messages, and wait for as long as the Retry-After header says when Slack answers 429. If the app keeps sending too fast, Slack can switch it off. Put many lines into one message rather than sending many posts.
- If Slack answers "no_service" or 404, the webhook was cancelled (someone removed the app). Create a new webhook; retrying will not help.
- The host to declare is hooks.slack.com.

## If the company's IT department blocks it

When the workspace restricts who can create apps, the "Create New App" step fails with a message about permissions. Give the user this one sentence to send to the workspace admin: please approve an incoming-webhook app that posts to the channel, or create the webhook yourself and share the address with me. A webhook is the Slack connection with the fewest permissions, and admins can create one directly at https://api.slack.com/apps.

## Official docs

If a request fails, check Slack's documentation: https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/
