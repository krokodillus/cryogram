---
name: email-inbox
description: WHEN a step reads messages from a mailbox during a run: Gmail IMAP with an app password, an Apps Script bridge, or Outlook and Microsoft 365
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Reading incoming email

## Route 1: Gmail IMAP with an app password, when the account allows it

This still works in 2026: the "less secure apps" shutdown of 2024 ended plain passwords, not app passwords. IMAP has been on by default since January 2025, so the old step of switching IMAP on is gone.

Give the user these setup steps in full:

1. 2-Step Verification must be on: https://myaccount.google.com/signinoptions/two-step-verification
2. Open https://myaccount.google.com/apppasswords, name the password, click Create, and copy the 16-character password (it is shown once; remove the spaces it displays). Collect it through the masked ask, as a secret named for what it is (the Gmail app password).
3. Ask the user: does that page open for you? A work account may refuse it because of an admin policy, and then you use Route 2.

How to poll:

- Connect with `imaplib.IMAP4_SSL("imap.gmail.com", 993)` and LOGIN with the address and the app password. Use one connection per poll and LOGOUT after it, because Gmail allows 15 connections at once and heavy use can suspend IMAP for 24 hours.
- Fetch with `BODY.PEEK[]`. A plain BODY[] marks messages as read without telling you. Parse with the standard library `email` module. An attachment is `part.get_filename()` plus `get_payload(decode=True)`.
- Track new mail by the triple (folder, UIDVALIDITY, last acknowledged UID), not by UNSEEN. Fetch the ids above that UID, and filter out the IMAP `*` edge case. Do not move the UID forward just because messages were fetched. Move it only after later processing has finished, or after you have confirmed the messages are saved where a later step can still read them, and keep the gap when an earlier message failed. Use each message's unique id to skip a message that is delivered again. The first time, or when UIDVALIDITY has changed, agree with the user whether to start from now or to fetch the older mail too. Never drop unseen mail without saying so.
- A successful connection is an authenticated SELECT followed by a search. The error "[AUTHENTICATIONFAILED] Invalid credentials" means the app password was revoked or blocked, and it will not fix itself. The error "Too many simultaneous connections" means you are polling too often or leaving connections open, so wait and try later.

What goes wrong:

- The `[Gmail]` folders have names in the user's language ("Todos", not "All Mail"). Use INBOX, which is never translated, or find the folder by the special-use flags in the LIST answer. Never write folder names in the code.
- Every message is also in All Mail, so poll exactly one folder, or you process messages twice. A Gmail filter that skips the inbox hides mail from a poller that reads INBOX, so ask the user about filters when mail seems to be missing.
- If the user turns off 2-Step Verification, every app password stops working, with no warning.
- The domain is imap.gmail.com.

## Route 2: an Apps Script pull bridge, when Workspace blocks app passwords

The user's account can serve an Apps Script web app. The read handler returns the messages with stable ids, and does not mark them as processed. A separate acknowledgement handler marks only the ids whose later processing finished (or that were saved where a later step can still read them). Messages that failed stay available to try again, and the steps after this one must skip a message id they have already handled. Both handlers must check authentication: a deployment open to Anyone can be called by anyone, so a URL that is hard to guess is not access control. Collect the endpoint and a shared token through the masked ask, check the token before reading any mail, then deploy as the user. Treat the returned mail and attachments as sensitive data.

- The /exec URL answers with a 302 redirect. Follow redirects, and allow both script.google.com and script.googleusercontent.com.
- To change the code, open Manage deployments and make a new version. A new deployment gets a new URL and breaks the stored secret.
- Errors can come back as HTML with HTTP 200, so the only sign of success is the script's own JSON result, never the status code.

## Route 3: Outlook and Microsoft 365

Prefer an OAuth, Graph or mail connection that is already authorised and suits the mailbox. Check the account and tenant requirements before you propose a new registration. Do not forward business mail into Gmail by default: forwarding changes who can see the data, and needs the user's explicit agreement and the company's policy to allow it. The forwarding options below apply only when that route was requested or approved. Fetching mail when the user starts a run is not background monitoring of the mailbox.

- A personal outlook.com account: Settings, Mail, Forwarding and IMAP, Enable forwarding, enter the Gmail address the workflow will read, and keep a copy. Then use Route 1.
- A work Microsoft 365 account: go to make.powerautomate.com, Create, Automated cloud flow, with the trigger "When a new email arrives (V3)" (a standard connector, not premium) and the action "Forward email (V2)" to the Gmail address. The trigger watches one folder, so mailbox rules bypass it. Defender can fire it twice, the first time with an empty attachment list, so skip duplicates by message id. The generic HTTP action is premium-only, so forward the email rather than POST it.
- Where the IT department blocks it: tenants often block automatic forwarding to outside addresses (error 550 5.7.520 on the forward). Nothing the user does gets past it. The request goes to IT, or the workflow reads a personal mailbox instead.

## Official docs

If a route fails, check here: https://support.google.com/mail/answer/7126229
