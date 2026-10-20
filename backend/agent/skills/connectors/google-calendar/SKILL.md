---
name: google-calendar
description: WHEN a step reads or creates Google Calendar events: the secret iCal address, the Calendar API with a service account, or an Apps Script bridge
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Reading and writing Google Calendar

First check the environments attached to the workflow for a Google service-account key. If one is there, offer the route that uses it first. If there is none and you offer the user a choice, mention the reuse option: a user who will build more Google automations, or who already has a service account, sets it up once, saves the JSON key in an environment, and every later workflow uses it without entering it again.

## Route 1: the secret iCal address, for read-only tasks that the feed can answer

The user opens calendar.google.com, clicks the gear, then Settings, picks the calendar in the left sidebar, clicks "Integrate calendar", and copies the "Secret address in iCal format" (the .ics URL). Anyone with this URL can read the calendar, so collect it through the masked ask as a secret named for what it is (the calendar's iCal URL). The "Reset" button on that page makes the old URL stop working.

- A plain GET returns a 200 with `text/calendar`. Parse it with the `icalendar` package. Recurring events arrive as RRULEs. When the workflow needs each separate occurrence, expand them with `recurring-ical-events`, and never write your own RRULE arithmetic.
- Do not promise real-time data. Fetch the feed when the user runs the workflow; Cryogram does not poll in the background. When the task needs queries the feed cannot do, use the API route.
- Workspace accounts may hide the secret address when an admin has restricted external sharing. Ask the user whether they see "Secret address" on that page. If they do not, use Route 2.
- The domain is calendar.google.com. This route only reads: never write through ICS.

## Route 2: a service account, for reads with filters and for creating events

This is the same service-account pattern as Drive, and one JSON key can serve both. Setup: the user opens console.cloud.google.com and creates a project (one project serves every workflow the user builds; use theirs when they already have one), enables the Calendar API, goes to Service accounts, Create, and makes a JSON key (collected through the masked ask). Then the user shares the calendar: Calendar settings, "Share with specific people or groups", add the service account's email address, with "See all event details" for reads or "Make changes to events" for writes.

- Read: GET `www.googleapis.com/calendar/v3/calendars/<calendarId>/events?timeMin=...&singleEvents=true&orderBy=startTime` with the bearer token. The calendarId is the user's email address for the primary calendar, or the long `...@group.calendar.google.com` id shown under "Integrate calendar".
- Write: POST `.../events`. A 200 with the event JSON means it worked. Its `htmlLink` is the link the user can open to see the event, so show it.
- The domains are oauth2.googleapis.com and www.googleapis.com.

What goes wrong:

- A service account cannot invite attendees unless domain-wide delegation is set up. Without it you get a 403, and only an admin can set it up, and only on Workspace. Events with times, a description and a location work fine. If the workflow must invite people from a personal account, use the Apps Script bridge (Route 3).
- A calendar shared with the service account does not appear in the service account's own calendar list, so call events with the explicit calendarId. A 404 means the calendarId is wrong or the share was not saved. It does not mean the calendar is missing from the list.
- Times are RFC 3339 with a time zone. All-day events use `date` and timed events use `dateTime`. Mixing them gives a 400.

## Route 3: an Apps Script bridge, for invitations from personal accounts

Use an Apps Script handler that checks authentication, and only when the user has agreed to this route. The handler checks a shared secret from Script Properties before it creates the event and sends the invitations, and returns explicit JSON that includes the event's id. A deployment open to Anyone needs this check, because its URL alone is not authentication. Store the caller's credentials as secrets, each under its own name. Allow the hosts the web app redirects to (script.google.com and script.googleusercontent.com), and keep the same deployment when you edit the script. Check the current quotas for the volume you need, and do not assume every workflow fits within them.

## If the company's IT department blocks it

When there is no secret address, no external sharing and Apps Script is restricted, the one-line request for the admin is: please allow calendar sharing with a service account (read or edit on the calendar), or enable the secret iCal address for it.

## Official docs

If a route fails, check here: https://support.google.com/calendar/answer/37648
