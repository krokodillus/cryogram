---
name: microsoft-teams
description: WHEN a step posts a message to Microsoft Teams: how to set up the webhook for a channel, a group chat or one person, and how to tell that Teams accepted a post apart from Teams delivering it
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Posting to Microsoft Teams

## Use a Workflows webhook if the account and the destination allow it

For a new integration, use a Workflows webhook if the user's Microsoft 365 tenant supports it. Work out from the user's request whether the destination is a channel, a group chat or one person. Ask the user only if the request does not say.

## Setup for each kind of destination

A channel is the simplest. In Teams, open the channel's ... menu, choose Workflows, search for "webhook" and pick the template that posts to the channel when it receives a webhook request. It is currently named "Send webhook alerts to a channel". Microsoft renames these templates, so never insist on an exact name that the user cannot see. Accept the defaults. The confirmation screen shows the URL.

If the user missed the URL, they can copy it from Teams. Do not send them to Power Automate for it. In Teams, open the channel's ... menu, choose Workflows, find the flow in the list, open its ... menu and choose "Copy webhook link". The wording varies slightly between Teams versions; it is the copy action on the flow's row. Only if that menu is missing, go to https://make.powerautomate.com, choose My flows, open the flow and copy the HTTP URL of its trigger. If no template matches at all, build the flow from scratch as in the group-chat route below, with Post in: Channel.

For a group chat or a direct message, build the flow at https://make.powerautomate.com. Choose Create, then Instant cloud flow, then the trigger "When a Teams webhook request is received" (Who can trigger: Anyone), then the action "Post card in a chat or channel":

- Post as: Flow bot. This is the generic sender. If you choose "User" instead, every message reads "<their name> via Workflows".
- Post in: Group chat (pick the chat; the flow owner must be a member of it), or "Chat with Flow bot" plus the recipient for a direct message. A one-to-one chat between two other people cannot be reached; Teams does not allow it.
- Card: the content of the trigger body. Save the flow, and copy the HTTP URL of the trigger.

For every destination, ask the user for the URL with the masked ask, and save it as a secret named for what it is (the Teams webhook URL). The URL is the only credential, so treat it as a secret.

## The sender line: what can and cannot be changed

- The sender line is fixed by how Workflows posts. A post as Flow bot shows the Workflows bot as the sender, and a post as a user shows "<name> via Workflows". It cannot be removed. Only a custom bot registered in Azure changes it, and that is work for the user's IT department, so tell the user so if they ask.
- Mention the attribution that the template adds only if the user asks about it. A copied flow may look different, but this is a workaround seen in practice, not a guarantee. If you try a replacement flow, keep the original until the new URL is set up and tested. Deleting a working flow needs the user's separate approval.
- The card body holds the message the user asked for. Do not add Cryogram branding or a question about attribution unless the user asks for it.

## How to send

- POST JSON in the Adaptive Card envelope: `{"type":"message","attachments":[{"contentType":"application/vnd.microsoft.card.adaptive","content":{"type":"AdaptiveCard","version":"1.4","body":[{"type":"TextBlock","text":"...","wrap":true}]}}]}`. Only the channel template "Send webhook alert to a channel" also accepts the older MessageCard payloads. Do not rely on that for new flows.
- Set timeout=15 and make one attempt per POST. A timeout, a connection failure, a 5xx or a 429 does not prove that Teams did not accept the message. After any of these, look in the flow's run history before you send again.
- A 202 means only that the trigger of the flow received the request, not that the message was delivered. During testing, confirm the posting action in the flow's run history or in the destination. Keep any id that ties the request to the flow's run, and report separately which posts were accepted and which were delivered. Read every other response against what the configured flow is built to return, and redact sensitive content in a response.
- Declare the URL's actual host as the step's domain, for example prod-XX.westeurope.logic.azure.com. Read the host from the URL the user pasted.

## What goes wrong

- If a custom flow posts raw JSON as text, the Adaptive Card field of the "Post card" action is bound wrongly. It must be the Body dynamic content of the trigger (triggerBody()), not pasted text. Fix it in the flow, not in what you send.
- If nothing arrives, stop and open the flow's run history first (make.powerautomate.com, My flows, the flow, the latest run). It names the action that failed and the error. Never keep sending to find out.
- The URL belongs to that one flow. A new destination needs a new flow and a new URL.
- If "Who can trigger" is left at its default, external POSTs get a 401. Set it to Anyone. The URL itself stays secret.
- The flow stops working without any message if its owner leaves the team or the chat, or if the flow is turned off. Posts then fail with 4xx errors. The fix is a new flow or turning the old one back on, never a retry.
- Adaptive Card version: Microsoft's official examples use 1.2. Use 1.4 or lower, because newer features may show blank in some Teams clients.
- Keep each message within the connector's documented size limit. Workflows throttling follows the tenant's Power Automate profile. Pace bulk sends, and when a rate limit is hit, let it pause the run: do not catch it in the code. Do not retry a POST automatically, because a retry may post the message twice.

## If company IT blocks it

If Power Automate is disabled and the Workflows menu is missing, give the user this one line to send to IT: please allow me to create a Power Automate instant flow with the Teams webhook trigger; it only posts messages I send it into one chat or channel I own. If IT refuses outright, the alternative is an Azure bot registration with Graph permissions. That is work for IT. Tell the user so plainly, and do not walk a user who is not an admin through admin consent.

## Official docs

If a route fails, check here: https://support.microsoft.com/en-us/office/create-incoming-webhooks-with-workflows-for-microsoft-teams-8ae491c7-0394-4861-ba59-055e33f75498
