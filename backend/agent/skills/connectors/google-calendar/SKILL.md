---
name: google-calendar
description: WHEN a workflow step needs to read events from or create events in Google Calendar - the secret iCal read route, service-account routes, the attendee limitation, gotchas
tier: 2
kind: action
depends_on: []
version: 1.2.0
last_updated: 2026-09-10
---

# Reading and writing Google Calendar

First, check the workflow's attached environments for an existing Google service-account key. If one is there, lead with the route that uses it. If none exists and you present a fork, include the reuse angle: a user who will build more Google automations, or already has a service account, sets it up once, saves the JSON key in an environment, and every future workflow reuses it without re-entering.

## Route 1: the secret iCal address, read-only, zero setup, the locked-in route for reads

The user opens calendar.google.com, the gear, Settings, picks the calendar in the left sidebar, "Integrate calendar", and copies the "Secret address in iCal format" (the .ics URL). The URL is the credential: collect it through the masked ask, as a secret named for what it is (the calendar's iCal URL). "Reset" on that page revokes it.

- A plain GET gives a 200 with text/calendar. Parse it with the `icalendar` package; recurring events arrive as RRULEs, so expand them with `recurring-ical-events` when the workflow needs concrete instances, and never hand-roll RRULE arithmetic.
- Freshness: direct fetches are usually fresh within minutes, but Google publishes no guarantee. Never promise real time; poll, do not hammer.
- Workspace accounts may hide the secret address under an admin external-sharing policy. The fork question is whether the user sees "Secret address" on that page; if not, Route 2.
- The domain is calendar.google.com. Read-only: never a write through ICS.

## Route 2: a service account, for reads with filters and for creating events

The same service-account pattern as Drive, and one JSON key can serve both. Setup: console.cloud.google.com, a project (one project serves every workflow the user builds; reuse theirs when they have one), enable the Calendar API, Service accounts, Create, a JSON key (collected through the masked ask). Then the user shares the calendar: Calendar settings, "Share with specific people or groups", add the service account's email, with "See all event details" for reads or "Make changes to events" for writes.

- Read: GET `www.googleapis.com/calendar/v3/calendars/<calendarId>/events?timeMin=...&singleEvents=true&orderBy=startTime` with the bearer token. The calendarId is the user's email for the primary calendar, or the long `...@group.calendar.google.com` id from "Integrate calendar".
- Write: POST `.../events`; success is a 200 with the event JSON, whose `htmlLink` is the user-facing proof, so show it.
- The domains are oauth2.googleapis.com and www.googleapis.com.

What goes wrong:

- The attendee wall: a service account cannot invite attendees without domain-wide delegation (a 403; admin-only and Workspace-only). Events with times, a description and a location work fine. If the workflow must invite people on a personal account, use the Apps Script bridge (Route 3).
- The shared calendar never shows in the service account's own calendar list, so call events with the explicit calendarId. A 404 is a wrong calendarId or an unsaved share, not a missing list entry.
- Times are RFC 3339 with a timezone; all-day events use `date` and timed events `dateTime`, and mixing them gives a 400.

## Route 3: an Apps Script bridge, for invites on personal accounts

The same web-app pattern as the Drive guide: a `doPost` calling `CalendarApp.getDefaultCalendar().createEvent(title, start, end, {description, location, guests, sendInvites: true})`, which executes as the user, so invitations send. The /exec URL is the credential; follow redirects; allowlist script.google.com and script.googleusercontent.com; a new version on the same deployment for edits; success is the script's own JSON. The quota is about 5,000 created events a day, far above any workflow.

## If company IT blocks it

When there is no secret address, no external sharing and Apps Script is restricted, the one-line ask for the admin: please allow calendar sharing with a service account (read or edit on the calendar), or enable the secret iCal address for it.

## Official docs

Check here if a route fails: https://support.google.com/calendar/answer/37648
