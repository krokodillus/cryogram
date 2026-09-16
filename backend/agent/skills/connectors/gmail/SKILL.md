---
name: gmail
description: WHEN a workflow step needs to send email from a Gmail address - the app-password route (locked in), exact click-through setup, the send pattern, and the company-IT fallback
tier: 2
kind: action
depends_on: []
version: 1.2.0
last_updated: 2026-09-06
---

# Sending from Gmail

## The locked-in route: SMTP with an app password

It is the best route for Gmail: five minutes, no cloud console, stdlib only. Do not offer alternatives unless this proves infeasible (see the IT note at the end).

## User setup

Give them these steps complete, with the links, so they click rather than navigate:

1. Open https://myaccount.google.com/apppasswords (sign in if asked). If it says 2-Step Verification is required first, turn it on at https://myaccount.google.com/signinoptions/twosv, then reopen the link.
2. Type a name for the password, press Create, and copy the 16-character password it shows (spaces are fine).
3. Paste it into the masked field you show (a secret named for what it is, such as the Gmail app password), never into the plain chat.

## The send pattern

Stdlib smtplib, no packages: smtp.gmail.com, port 465, SMTP_SSL, timeout=30. Build the message with email.message.EmailMessage and set Subject, From and To explicitly, From being the Gmail address. Retry once on a timeout or SMTPServerDisconnected. Never retry SMTPAuthenticationError: the password is wrong or revoked, so say so and walk step 1 again. The domain is smtp.gmail.com.

## What goes wrong

- "Username and Password not accepted" means the app password is revoked or mistyped, or 2-Step is off. It is not transient, so never retry. The normal account password can never work here ("less secure apps" is fully retired); do not try it.
- 2-Step Verification is a hard prerequisite: without it the app-passwords page hides the option entirely.
- Store the password exactly as pasted; with or without spaces both work.
- Limits: about 500 mails a day on a personal account, about 2,000 a day on Workspace, and at most 100 recipients per message over SMTP. Say so if the user plans volume; exceeding them blocks sending for up to 24 hours.
- The From address is rewritten to the authenticated account. Sending as an alias needs that alias configured under Gmail's own "Send mail as" settings first.

## If company IT blocks it

Google Workspace can disable app passwords, and then the app-passwords page says the option is unavailable. Tell the user plainly and give them the one-line ask for IT: please allow app passwords for my account, or provide an SMTP relay address I can send through. Workspace admins have both switches. If IT offers the relay (smtp-relay.gmail.com), the same send pattern works with the relay host.

## Official docs

Check here if a route fails: https://support.google.com/mail/answer/185833
