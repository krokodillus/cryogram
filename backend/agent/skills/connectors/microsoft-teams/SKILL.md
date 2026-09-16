---
name: microsoft-teams
description: WHEN a workflow step needs to post a message to Microsoft Teams (channel, group chat, or direct to a person) - the Workflows-webhook route (locked in), the use-case fork, sender attribution facts, click-through setup, and the company-IT fallback
tier: 2
kind: action
depends_on: []
version: 1.8.0
last_updated: 2026-09-06
---

# Posting to Microsoft Teams

## The locked-in route: a Workflows (Power Automate) webhook

The classic "Incoming Webhook" connector is retired. One question decides the variant: where should the message land, a channel, a group chat, or a direct message to one person?

## Setup by destination

A channel is simplest. In Teams, the channel's ... menu, Workflows, search "webhook" and pick the template that posts to the channel on a webhook request (currently named "Send webhook alerts to a channel"; Microsoft renames these templates, so never insist on an exact label the user cannot see), accept the defaults, and the URL is shown on the confirmation screen. If they missed the URL, it is copyable from Teams, with no need to send anyone to Power Automate: the channel's ... menu, Workflows, find the flow in the list, its ... menu, "Copy webhook link" (the wording varies slightly by client version; it is the copy action on the flow's row). Only if that menu is missing: https://make.powerautomate.com, My flows, open the flow, the trigger's HTTP URL. With no matching template at all, build from scratch like the group-chat route below, with Post in: Channel.

A group chat or a direct message: build the flow at https://make.powerautomate.com, Create, Instant cloud flow, the trigger "When a Teams webhook request is received" (Who can trigger: Anyone), the action "Post card in a chat or channel":

- Post as: Flow bot (the generic sender; as "User" every message reads "<their name> via Workflows").
- Post in: Group chat (pick it; the flow owner must be a member), or "Chat with Flow bot" plus the recipient for a direct message. A one-to-one chat between two other people is not reachable; that is a platform limit.
- Card: the trigger body's content. Save, and copy the trigger's HTTP URL.

Either way, collect the URL through the masked ask, as a secret named for what it is (the Teams webhook URL). The URL is the credential.

## Attribution: what can and cannot change

- The sender line is architecture: Flow bot posts show the Workflows bot, and user posts show "<name> via Workflows". It cannot be removed; only an Azure-registered custom bot changes it, which is an IT workflow, so say so honestly if asked.
- The template footer ("...used a Workflow template to send this card. Get template") is added by Teams to template-created flows. Removal, verified in real tests: copy the flow, delete the original, and use the copy's URL, because a copy is a plain flow and the footer is gone. Do it inside Teams: the channel's ... menu, Workflows, the flow's ... menu, copy or duplicate it, delete the old flow, "Copy webhook link" on the new one. If the Teams menu lacks a copy action: https://make.powerautomate.com, My flows, ..., Save As, then turn the copy on. Either way the copy mints a new trigger URL: replace the stored webhook secret with it, and re-send one test message. Never route the user into building a custom flow from scratch just for this.
- The card body is otherwise yours: a small closing TextBlock is welcome; suggest "Sent via Cryogram" (subtle, isSubtle: true).

## The send pattern

- POST JSON in the Adaptive Card envelope: {"type":"message","attachments":[{"contentType":"application/vnd.microsoft.card.adaptive","content":{"type":"AdaptiveCard","version":"1.4","body":[{"type":"TextBlock","text":"...","wrap":true}]}}]}. Only the channel template "Send webhook alert to a channel" also accepts legacy MessageCard payloads; never rely on that for new flows.
- timeout=15; retry timeouts, connection errors, 5xx and 429 with a short backoff; never retry any other 4xx.
- The success signal is a 202 with an empty body. Anything else raises with the body text.
- The domain is the URL's actual host (such as prod-XX.westeurope.logic.azure.com), read from the pasted URL.

## What goes wrong

- A custom flow printing raw JSON as text means the "Post card" action's Adaptive Card field is mis-bound: it must be the trigger's Body dynamic content (triggerBody()), not pasted text. Fix it in the flow, not in what you send.
- Nothing arriving means stop and check the flow's run history first (make.powerautomate.com, My flows, the flow, the latest run): it names the failing action and error. Never keep resending to find out.
- The URL is bound to that one flow; a new destination is a new flow and URL.
- "Who can trigger" left at the default makes external POSTs 401; it must be Anyone (the URL itself stays the secret).
- The flow dies silently if its owner leaves the team or chat or the flow is turned off; failures turn 4xx, and the fix is a new or re-enabled flow, never a retry.
- Adaptive Card version: the official examples use 1.2; stay at 1.4 or lower, since newer features may render blank in some clients.
- The message cap is 28 KB (larger is an error). The old 4-requests-a-second figure was the retired classic webhook's; Workflows throttling follows the Power Automate profile, which is generous, but still batch or pace bulk sends and keep the 429 backoff.

## If company IT blocks it

When Power Automate is disabled and the Workflows menu is missing, the one-line ask for IT: please allow me to create a Power Automate instant flow with the Teams webhook trigger; it only posts messages I send it into one chat or channel I own. If refused outright, the alternative (an Azure bot registration with Graph permissions) is genuinely an IT workflow; say so plainly instead of walking a non-admin through admin consent.

## Official docs

Check here if a route fails: https://support.microsoft.com/en-us/office/create-incoming-webhooks-with-workflows-for-microsoft-teams-8ae491c7-0394-4861-ba59-055e33f75498
