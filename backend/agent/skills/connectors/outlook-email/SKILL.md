---
name: outlook-email
description: WHEN a workflow step needs to send email from an Outlook / Microsoft 365 address - the one question that decides the route (personal vs work account), the basic-auth retirement, the bridge-flow route, and the company-IT fallback
tier: 2
kind: action
depends_on: []
version: 1.4.0
last_updated: 2026-09-06
---

# Sending from Outlook / Microsoft 365

## Ask first

This is a genuine fork with different routes: is this a personal Outlook or Hotmail address, or a work or school (Microsoft 365) account? The options are Personal / Work or school.

The clock: Microsoft is retiring basic SMTP auth, and with it app passwords. New accounts and tenants often have it off already; existing ones lose it by default after December 2026, though admins can re-enable it until the final removal date (to be announced in the second half of 2027). SMTP may still work today, but the future-proof route is the bridge flow below. Prefer it unless SMTP proves available live and the user wants the quickest path now.

## The locked-in route for both account types: the Power Automate bridge flow

The free "When a Teams webhook request is received" trigger (a standard connector; the plain HTTP trigger is premium) drives the Outlook send action, and the auth is the flow's own sign-in, so no password ever enters the workflow.

1. https://make.powerautomate.com, Create, Instant cloud flow, the trigger "When a Teams webhook request is received" (Who can trigger: Anyone).
2. Add the action "Send an email (V2)", with the connector "Office 365 Outlook" for work accounts and "Outlook.com" for personal. Map To, Subject and Body from the trigger body (triggerBody()?['to'] and so on).
3. Save; copy the trigger URL and collect it through the masked ask. The URL is the credential.

The send pattern: POST JSON {"to","subject","body"}; timeout=30; retry timeouts, 5xx and 429 only; success is a 202 with an empty body. It is fire-and-forget: confirm arrival once with the user during exploration, and when mails stop, check the flow's run history (make.powerautomate.com, My flows, the latest run), which names the failing action. Never resend blind.

## SMTP, which works on some accounts today: verify live, warn about the clock

- Personal: an app password from https://account.live.com/proofs/manage/additional, with two-step verification on. No "App passwords" option means the account is already past basic auth, so use the bridge flow.
- Work: "Authenticated SMTP" may be enabled for the mailbox; try it live with the masked-collected password. The error "SmtpClientAuthentication is disabled for the Tenant" is the policy signal, never a typo, so stop immediately.
- The pattern for both: smtp-mail.outlook.com (smtp.office365.com for work), port 587 with STARTTLS, timeout=30, EmailMessage; retry once on a timeout or disconnect, never on an auth error. The domain is the SMTP host used.
- If SMTP is chosen, note the retirement date in the workflow's instructions so the eventual failure is no mystery.

## If neither lands

The one-line IT ask: please enable "Authenticated SMTP" for my mailbox, or allow me an instant cloud flow with the Teams webhook trigger that sends mail as me. If IT declines both, offer the pragmatic alternative: send from Gmail (the gmail guide) with the work address as reply-to, and say plainly that sending as the work address then needs IT.

## What goes wrong

- Auth failures on work accounts are policy, not typos: never retry, and never ask the user to re-type the password more than once.
- The bridge flow dies silently if it is turned off or its owner leaves; run history first.

## Official docs

Check here if a route fails: https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online
