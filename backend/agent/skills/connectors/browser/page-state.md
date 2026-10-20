# Page state: check the page before you read it

Before your code reads, scrolls or clicks anything, check two things: that it is on the page the step asked for, and what that page is showing. Learn how to tell from the real page: the saved HTML and HAR file of an earlier window.

The page can be showing one of four things, and each needs its own handling:
- The content you expected: read it.
- The page's own message for having nothing, for example "No messages": return an empty result, but only if the task allows an empty result.
- A sign-in page, a captcha, a message saying you are sending too many requests, or a message saying access is blocked: this is never an empty result. Handle it as described under "When the user has to act" below.
- None of these: call `unexpected_input(reason, found=..., expected=...)`, saying what the page shows and what the step expected. Do not read data from a page you did not ask for.

Many sites load bot-defence and analytics scripts on every page for every visitor, for example requests to protechts.net (PerimeterX) or google-analytics.com. Their requests and parameters do not show that the site has flagged your session. Only the site's own response shows that: a captcha, a sign-in page, an HTTP 429, or a page saying access is blocked.

Do not decide what the page is from one word found anywhere in its text: a post can mention a captcha on a normal page, and a readable public page can have a "Sign in" link. Look for the elements that only a sign-in page or a captcha page has, and take the site into account. When a sign-in page, a captcha, a "too many requests" message or an access-blocked message sits on top of the page, that decides what the page is, even if content is still visible underneath it.

## When the user has to act

Signing in is done by the workflow's sign-in step (see the "browser-login" guide). If a later step finds the user signed out, it stops and says the session has expired. It never changes the workflow to work around it.

If the page shows something the user can deal with in the window, such as a verification check, the step can call `confirm_with_user(message, page=page)`. The window comes to the front, the user is asked to do it, and the call returns when they confirm. If the user closes the window or nobody answers, the run pauses: that is not a fault in the code. Do not ask the user again and again about a page that refuses access or says you are sending too many requests: asking does not fix either.

After the user confirms, check again that the page is the one the step asked for and that the element you are waiting for is there. If it is still missing, stop and say what the page shows. Never reload a captcha page, or loop past it, to get round it.

## Slow pages

`browser_goto` already tries a page up to three times before it gives up. For any other wait in your code, catch only Playwright's timeout error, check again what the page is showing, and stop after a set time. Do not catch every error and call it a slow page: a closed window, a refused permission or a mistake in the code each need their own message.

## Tests

Test the code that decides what the page is showing against saved examples of each case you have: a page with content, an empty page, a sign-in page. A try only shows the cases it actually met. Do not sign the user out or provoke a captcha on purpose to see one. Say which cases the tries did not meet, so it is clear they are untested.
