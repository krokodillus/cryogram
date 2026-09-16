---
name: browser-login
description: WHEN a browser step needs a signed-in session - first sign-in, an expired session, a captcha or verification page - how the app keeps the session, how the step tells whether the person is signed in, confirm_with_user, the sign-in step and the single sign-on warning
tier: 2
kind: action
depends_on: []
version: 4.0.0
last_updated: 2026-09-13
---

# Browser sessions: signing in

The app keeps the session. Every browser step's window opens on this workflow's own profile, the session saved when the last window closed is put back for any cookie the profile has lost, and the whole session is saved again before the window closes, so a cookie that lives only as long as the browser survives too. One sign-in per workflow is the default; a sign-in every run happens only when the user asks for it or a site genuinely forces it.

## Whether the person is signed in is the step's own check

What says signed in differs per site: a cookie in the jar (`page.context.cookies()`), something only a signed-in page shows, or where the site sends a signed-out visitor. A site can also replace a cookie as the page loads, so check after the page has settled. Work the check out while exploring by looking at the page signed out and signed in, and use the difference that holds. Known session cookies: LinkedIn sets `li_at`, Reddit sets `reddit_session`.

## When they are not, ask the person in the window

```python
browser_goto(page, "https://www.example.com/")
if not signed_in(page):
    browser_goto(page, "https://www.example.com/login")
    confirm_with_user("Sign in to Example in the window, then confirm here", page)
    browser_goto(page, "https://www.example.com/")
    if not signed_in(page):
        raise RuntimeError("still signed out of Example - sign in in the window and run this step again")
write_output("signed_in", True)
```

`confirm_with_user` brings the window on screen, the person gets the request wherever they are, and it returns when they confirm. A closed window or nobody answering pauses the run, and running the step again asks afresh. The person types their password into the site's own page; never collect it, and never open Chrome, look for its path or stop a process yourself.

## The sign-in is its own plan step

Make the sign-in a browser step of its own, before the work: it opens the site, checks, asks only when signed out, and says so as an output. When the session is there it asks nothing, so a run where the person is already signed in shows them nothing. Every later browser step finds the session in the profile and never waits for a sign-in itself: on a signed-out page it raises a plain message that the session expired, and the next run's sign-in step asks.

## During exploration

A sign-in wall met while exploring is a user decision. Ask the fork first, ideally with the setup question: sign in once so the workflow can read the site, or skip that part. On yes, save the plan with the sign-in step and run a cell of the same name. The window's own approval card is the one ask before it opens.

## Tell the user about single sign-on before they sit down

Recommend the site's own username and password up front. Signing in through Google or Apple may refuse an automated window ("this browser may not be secure"). A user with no site password creates one through the site's password-reset flow.

## Session care

- One profile folder per workflow; deleting it forces a fresh sign-in.
- An expired session is normal: the sign-in step catches it, and after a sign-in the next run is silent again.
- Never collect the user's site password; they type it into the site's own page in the window. No secret, no ask, no storage.
- Record what a sign-in investigation proves (what says signed in, on which site) as an intent fact the same turn.
