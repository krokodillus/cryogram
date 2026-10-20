---
name: outlook-email
description: WHEN a step sends mail from Outlook or Microsoft 365: which routes fit the account type, and how to confirm that the mail was delivered
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Sending from Outlook / Microsoft 365

## Settle these questions before setup

Use the account type the user has already stated. Ask whether it is a personal or a work/school account only if you do not know and the answer changes which route is possible.

Prefer an OAuth or mail connection that is already authorised and suits the task. Whether basic SMTP authentication is available, and when it is retired, varies and changes. Check the official documentation and the tenant's policy before you recommend it. The Power Automate bridge is one option. It is not a replacement that will always work.

## Power Automate bridge: needs a work/school account that owns the flow

Managing Power Automate cloud flows needs a work/school account. Support for personal accounts ended in July 2025. Before you offer this bridge, check three things: there is a work or school account that can own the flow, the tenant gives access to Teams Workflows, and the Outlook connector the step needs is available. A personal Outlook mailbox alone is not enough. Licensing and tenant policy can block the route, so do not say it is free everywhere or never needs an admin. If the bridge is not available, check whether an OAuth connection that is already authorised can be used, or tell the user what IT must allow.

1. Go to https://make.powerautomate.com and choose Create, then Instant cloud flow, then the trigger "When a Teams webhook request is received" (Who can trigger: Anyone).
2. Add the action "Send an email (V2)". Use the connector "Office 365 Outlook" for work accounts and "Outlook.com" for personal accounts. Map To, Subject and Body from the trigger body (triggerBody()?['to'] and so on).
3. Save the flow, copy the URL of the trigger and ask the user for it with the masked ask. The URL is the only credential, so treat it as a secret.

Send the to, subject and body the user agreed to in one POST, with a timeout and one attempt. A 202 means only that the trigger accepted the request, not that mail was delivered. During the approved test, confirm the "Send an email" action in the flow's run history, or at the recipient's end. If the outcome is unclear, find out what happened before you send again. Never send again just to find out whether the previous mail arrived.

## SMTP: works on some accounts today, so test it live and warn about the end date

- Personal Outlook has no general route that uses a password or an app password for mail. Use an OAuth client or connection that Microsoft supports. Do not send the user looking for an app-password setting as your first approach.
- Work accounts: "Authenticated SMTP" may be enabled for the mailbox. Try it live with the password collected through the masked ask. The error "SmtpClientAuthentication is disabled for the Tenant" means the organisation's policy blocks it. It is not a typo, so stop at once.
- Where SMTP is explicitly supported and the user has authorised it, use STARTTLS on the documented host, with timeout=30 and EmailMessage. Do not send again automatically after a timeout or a dropped connection, because the server may have accepted the mail. Find out first whether it was delivered.
- If you choose SMTP, write the date Microsoft retires it in the workflow's instructions, so that the failure when it ends has a known cause.

## If neither works

Give the user this one line to send to IT: please enable "Authenticated SMTP" for my mailbox, or allow me an instant cloud flow with the Teams webhook trigger that sends mail as me. If IT declines both, offer the practical alternative: send from Gmail (the gmail guide) with the work address as the reply-to address, and tell the user plainly that sending as the work address then needs IT.

## What goes wrong

- Read the actual authentication error. A wrong credential and a tenant policy need different fixes. Do not try passwords again and again, and do not change the authentication method, on the strength of a status code alone.
- The bridge flow stops without any message if it is turned off or its owner leaves. Look at the flow's run history first.

## Official docs

If a route fails, check here: https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online
