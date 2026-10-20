---
name: browser-login
description: WHEN a browser workflow needs the user to be signed in to a site, or a sign-in has stopped working
tier: 2
kind: action
depends_on: []
version: 5.1.0
last_updated: 2026-10-17
---

# Browser steps: signing in

The app looks after the signed-in session. Every browser step's window opens with this workflow's own browser profile. When a window opens, the app puts back any cookie the profile has lost since the last window closed, and before a window closes the app saves the whole session again, so even a cookie that would normally disappear when the browser closes is still there next time. So by default the user signs in once per workflow. A workflow asks the user to sign in on every run only if the user asks for that, or if the site really requires it.

## The step checks whether the user is signed in

Check first where the site sends a signed-out visitor: after the page has loaded, look at the page's address (`page.url`). A login page, a sign-up wall or the site's public home page means signed out, and the page you asked for means signed in. If the check also looks for an element, take its name from a page you loaded in a try, from that window's saved HTML ("$recorded:page"), never from what you remember of the site: sites rename their elements, and a check that looks for an element the page no longer has reports a signed-in user as signed out. A cookie being there does not prove the user is still signed in. Check after the page has finished loading.

If the user confirmed they signed in and the check still says they are not, read the HTML that window saved, in a code try that opens no window, before you ask them anything again: the check is more likely wrong than the sign-in. Do not sign the user out on purpose to see what a signed-out page looks like. Never write cookie values into an output, and never read the app's private profile files.

## If the user is not signed in, ask them in the window

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

`confirm_with_user` brings the window to the front, the user is asked to sign in wherever they are, and the call returns when they confirm. If the user closes the window or nobody answers, the run pauses, and running the step again asks again. The user types their password into the site's own page. Never ask for the password yourself, and never start Chrome, look for where it is installed or stop a program yourself.

## Signing in is its own step in the plan

Make signing in a browser step of its own, before the steps that do the work, with `read_only: true`: typing into the site's login form changes nothing on the site, and a step marked as changing something would stop every run at an approval popup. It opens the site, checks whether the user is signed in, asks only if they are not, and writes whether they are as an output. When the user is already signed in it asks nothing, so they see nothing during that run. The later browser steps find the session in the profile and never wait for a sign-in themselves: if one of them meets a signed-out page, it stops with a plain message that the session has expired, and the sign-in step asks on the next run.

## When you meet a sign-in page during the build

If a site asks for a sign-in while you are working out a step, the user decides. Ask them first, if you can in the same card as the other setup questions: sign in once so the workflow can read the site, or leave that part out. If they say yes, save the plan with the sign-in step and try it in a try of the same name. The card asking to open the window is the only question before it opens.

## Single sign-on

Use the way the site normally offers to sign in, with the user's existing account. If the site's single sign-on (for example "Sign in with Google") refuses to work in the window, say what the site showed and look for another way the user can approve. Never make creating or resetting a password, or changing how the user signs in, part of the normal setup.

## Keeping the session

- Each workflow has its own browser profile folder. Deleting it means the user has to sign in again.
- A session that has expired is normal: the sign-in step finds it, and after the user signs in, the next runs ask nothing.
- Never ask for the user's password for the site. They type it into the site's own page in the window. Nothing is stored and nothing is asked.
- When you have worked out how to tell the user is signed in on a site (which element shows it, on which page), save it with `save_intent` as a fact in the same turn.
