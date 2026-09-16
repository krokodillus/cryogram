---
name: email-inbox
description: WHEN a workflow step needs to read incoming email (poll a mailbox, fetch new messages/attachments) - Gmail IMAP app-password route, Outlook bridge reality, the new-since-last-run pattern
tier: 2
kind: action
depends_on: []
version: 1.1.0
last_updated: 2026-09-06
---

# Reading incoming email

## Route 1: Gmail IMAP with an app password, the locked-in route for Gmail

Still alive in 2026: the "less secure apps" shutdown of 2024 killed plain passwords, not app passwords. IMAP is on by default since January 2025, so the old enable-IMAP step is gone.

User setup, given complete:

1. 2-Step Verification must be on: https://myaccount.google.com/signinoptions/two-step-verification
2. https://myaccount.google.com/apppasswords, name it, Create, and copy the 16-character password (shown once; strip the display spaces). Collect it through the masked ask, as a secret named for what it is (the Gmail app password).
3. The fork question: does that page open for you? A work account may refuse under admin policy, which means Route 2.

The poll pattern:

- `imaplib.IMAP4_SSL("imap.gmail.com", 993)`, LOGIN with the address and the app password; one connection per poll and LOGOUT after, because there is a 15-connection cap and heavy use can suspend IMAP for 24 hours.
- Fetch with `BODY.PEEK[]`; a plain BODY[] silently marks messages read. Parse with the stdlib `email` module; an attachment is `part.get_filename()` plus `get_payload(decode=True)`.
- New since the last run: store (folder, UIDVALIDITY, last_uid), and never rely on UNSEEN, because the user's phone marks mail read and the poll silently skips it. Each poll: SELECT INBOX; if UIDVALIDITY changed, re-baseline, since all stored UIDs are void; otherwise `UID SEARCH UID <last+1>:*` and filter the results to those above last_uid (the `*` quirk returns the last message again when nothing is new); then advance to the highest UID. The first run must baseline or it ingests the whole mailbox.
- The success signal is an authenticated SELECT plus a search. "[AUTHENTICATIONFAILED] Invalid credentials" means a revoked or blocked app password and is never transient; "Too many simultaneous connections" means poll discipline, so back off.

What goes wrong:

- The `[Gmail]` folders are localised ("Todos", not "All Mail"). Use INBOX, which is never localised, or the special-use flags from LIST, never hardcoded names.
- Every message also sits in All Mail: poll exactly one folder or you double-process. A Gmail filter that skips the inbox hides mail from an INBOX poller entirely, so ask about filters when mail seems missing.
- Revoking 2-Step Verification silently revokes every app password.
- The domain is imap.gmail.com.

## Route 2: an Apps Script pull bridge, when Workspace blocks app passwords

The user's own account serves mail over a web app, with no OAuth app. script.google.com, New workflow, a `doGet` that runs `GmailApp.search('in:inbox -label:processed')`, labels what it returns, and replies JSON (attachments base64); then Deploy, New deployment, Web app, Execute as Me, access Anyone; authorise (Advanced, Go to ...); copy the /exec URL, which is the credential, collected through the masked ask. The step polls it.

- The /exec URL redirects with a 302: follow redirects and allowlist both script.google.com and script.googleusercontent.com.
- A code edit needs Manage deployments, then a new version; a new deployment mints a new URL and breaks the stored secret.
- Errors can come back as HTML with HTTP 200, so the success signal is the script's own JSON envelope, never the status code.

## Route 3: Outlook and Microsoft 365, the honest reality

No password-style direct route is left: outlook.com app passwords died in September 2024, Microsoft 365 basic auth in 2023, and IMAP is OAuth-only, needing an app registration and usually admin consent. The registration-free route is a bridge into Gmail.

- A personal outlook.com account: Settings, Mail, Forwarding and IMAP, Enable forwarding, the Gmail bridge address, keep a copy. Then Route 1.
- A work Microsoft 365 account: make.powerautomate.com, Create, Automated cloud flow, the trigger "When a new email arrives (V3)" (a standard connector, no premium), the action "Forward email (V2)" to the Gmail address. The trigger watches one folder, so mailbox rules bypass it; Defender can fire it twice, first with an empty attachment list, so dedupe by message id; the generic HTTP action is premium-only, so forward rather than POST.
- The IT hard stop: tenants often block external auto-forwarding (a 550 5.7.520 on the forward). Nothing user-side gets past it; the ask is for IT, or the workflow runs against a personal mailbox.

## Official docs

Check here if a route fails: https://support.google.com/mail/answer/7126229
