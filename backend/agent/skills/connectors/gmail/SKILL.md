---
name: gmail
description: WHEN a step sends email from a Gmail address: use a connection that already exists, or set up SMTP with an app password
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Sending from Gmail

## SMTP with an app password when the account allows it

Use a connection that is already authorised, if it suits. SMTP with an app password is a simple option for accounts that are eligible for it, and it is not the best route for every account. Before you start setup, check that app passwords are available and allowed for this account. Do not promise how long setup takes, and do not replace an OAuth connection the user chose.

## User setup

Give the user these steps in full, with the links, so that they click rather than search through menus:

1. Open https://myaccount.google.com/apppasswords (sign in if asked). If it says 2-Step Verification is required first, turn it on at https://myaccount.google.com/signinoptions/twosv, then open the link again.
2. Type a name for the password, click Create, and copy the 16-character password it shows (spaces are fine).
3. Have them paste it into the masked field on a card (a secret named for what it is, such as the Gmail app password), never into the chat.

## How to send

Use the standard library `smtplib`, with no packages: smtp.gmail.com, port 465, SMTP_SSL, timeout=30. Build the message with email.message.EmailMessage and set Subject, From and To explicitly, with From being the Gmail address. Make one send attempt. A timeout or SMTPServerDisconnected can happen after Gmail has already accepted the message, so check whether it was delivered before you send again. Never retry SMTPAuthenticationError: the password is wrong or revoked, so tell the user, and go through step 1 with them again. The domain is smtp.gmail.com.

## What goes wrong

- The error "Username and Password not accepted" means the app password was revoked or mistyped, or 2-Step Verification is off. It will not fix itself, so never retry. The normal account password can never work here ("less secure apps" no longer exists), so do not try it.
- 2-Step Verification must be on first: without it, the app-passwords page does not show the option at all.
- Store the password exactly as the user pasted it. It works with or without the spaces.
- Limits: about 500 mails a day on a personal account, about 2,000 a day on Workspace, and at most 100 recipients per message over SMTP. Tell the user if they plan to send in volume. Going over the limits blocks sending for up to 24 hours.
- Gmail rewrites the From address to the authenticated account. To send as an alias, the alias must first be set up under Gmail's own "Send mail as" settings.

## If the company's IT department blocks it

Google Workspace can switch off app passwords, and then the app-passwords page says the option is unavailable. Tell the user plainly, and give them the one-line request for IT: please allow app passwords for my account, or provide an SMTP relay address I can send through. Workspace admins can change both settings. If IT offers the relay (smtp-relay.gmail.com), the same sending code works with the relay as the host.

## Official docs

If a route fails, check here: https://support.google.com/mail/answer/185833
