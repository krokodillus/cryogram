---
name: browser
description: WHEN a step needs a browser window on a site: how to open pages, read them, scroll lists, check you are on the right page, and reuse what earlier windows saved
tier: 2
kind: action
depends_on: []
version: 3.2.0
last_updated: 2026-10-16
---

# Browser work

If the site has an official API or data export that does what the task needs, use that instead of a browser. An HTTP 403 does not mean you need a browser: first check whether the request was missing a login or a permission. Use a browser for a task that can only be done in a browser, on a site the user approved. Do not use a browser to get round a site that refused access.

## Other guides to load

- Load "browser/page-state" before a step reads a page, unless it was already given to you. It says how to check the page has the content you expect, and what to do on a sign-in page, a captcha or an empty page.
- Load "browser-login" when the workflow needs the user to be signed in to the site.
- Load "browser/har-capture" when you look through the HAR file for the requests that carry the task's data.
- Load "browser/demo-recording" when the user has to show you the steps on a site because they are not documented.
- Load "browser/api-routes" when a step calls the site's own API from inside the page, or does something to each item in a list and must be able to carry on after a failure.

## The app opens and closes the browser window

A browser step in the plan declares `type: browser`, the `url` it opens, and `read_only` (true if it only reads, false if it changes anything on the site). Signing in changes nothing on the site, so a step that only signs in, or signs in and then reads, is `read_only: true`. It does not declare `domains`. A step that changes something on the site also declares `external_impact`: one plain sentence saying what it changes, which the user reads before they approve it.

A try that is not one of the plan's steps, for example a try to look at a page, passes `browser=true` and `url` to run_cell. Every try that opens a window must have a `reason`: one sentence saying why the window needs to open. The user is asked on a card before a window opens, unless they already said "don't ask again" for that site for a step that only reads, and before a step sends anything for real.

The app opens a Chrome window that uses this workflow's own browser profile, so earlier logins are still there. Your code gets a Playwright `page` that is already open. While your code runs, the app saves every request and response the page makes in a HAR file, and saves the page's HTML. When your code ends, the app closes the window.

Do not start Chrome yourself, install a browser, change the browser profile, start other programs or read the browser's cookie files. Use `browser_goto(page, url)` to go to a page. `browser_options` can set the window size, the language and the time zone, and nothing else.

With the `page`, your code can click, type, scroll, wait, and read frames and downloads. Do not open extra tabs.

One browser step can open many pages if they are the same kind of page, for example every profile in a list. Work on a different kind of page, or with a different service, goes in a separate step. So collecting a list and opening each item in it are two steps. The first step does everything needed to collect the whole list, whether that is scrolling, clicking "next", going to the next page of results, or several of these one after another, and saves each item's link as it goes; it never opens an item. The second step opens each saved link. Opening an item needs different code and fails in different ways from collecting the list, so keeping them apart makes each easier to check and to fix. Each step has its own maximum setting: how many links to collect, and how many to open. When the list runs over several pages, try the first step with a maximum just above the number of items on the first page, so the try shows it moves on to the next page.

The browser profile stays signed in between runs, but a site can sign the user out at any time. On a site that needs the user to be signed in, the step's code must check it is signed in before it reads anything.

## Collect the content the user asked for

Before reading or scrolling, check that the page you expected has loaded, by looking for an element that only that page has.

Open a link the user gave you exactly as given, and do not change its query parameters yourself, except the one you change on purpose, such as the page number. Some links redirect to another address, for example a shared link, and the site may drop or change parameters on the way. After the page opens, check that it shows the content the link was meant to show, for example the right search results or the right profile, rather than checking the address.

Wait for the element that holds the content you need to appear. Do not only wait a fixed number of seconds.

To read a list from a page: count the items, scroll one screen, wait for the page to load, and count again. Stop when two scrolls in a row add no new items, or when you have as many items as the maximum setting. Do this whether or not you think the page loads more when scrolled, and whether or not you know how many items there are. Report the count after each scroll with `heartbeat()`, so a try shows whether the page loaded more.

Scroll the way a person does. Many lists scroll inside their own panel, not the whole page, and the mouse wheel scrolls whatever is under the pointer, which starts at the window's top-left corner. Move the pointer to the middle of the element that holds the list (`page.mouse.move`), then turn the wheel (`page.mouse.wheel`), or scroll the last item into view. Never set a scroll position directly: a person cannot do that, and many pages load their next batch only when they are really scrolled.

After each scroll, check the count went up. If the first scroll adds nothing, check the list actually moved, and look in the HAR file for the request the page makes to load more, before you decide you have reached the end of the list.

When you try a step that reads a list from one page, pass a maximum just above the number of items the page shows before any scrolling. The try then shows that the step reads the first items and that scrolling loads more. A real run collects up to the maximum the user set.

After such a try, the app compares the number of items with the maximum you passed. If the step returned exactly the maximum, you get a note: check that maximum was above the number the page shows before scrolling. If it returned fewer than the maximum, the page gave no more, and the user is asked whether that number is about what they expect. If it returned none at all, you get a note that the step is not reading the list yet.

When you try a step that works through several items (conversations, profiles, rows), first try it on 1 item and check the result has every field the step needs. When that works, try it on 2 items to check the loop. Pass these numbers as the step's maximum setting. Write the loop so the maximum counts every item the step works through, including items that gave nothing, so a try on 2 items never opens more than 2.

For every item, write what happened to it into the step's output: the data the step found, or the reason it found nothing (the item was empty, the profile was private, a sign-in page or captcha appeared, or the element the code looked for was not on the page). A try's result then shows why an item came back empty, and so does the summary of a real run. A real run works through every item and never stops early because some items gave nothing.

Open only the sections and controls the task needs. Use selectors that do not change between page loads, and read several attributes in one call instead of one call per field.

## When to stop

Stop collecting when you have what the user asked for (never more than the maximum setting), when you have reached the end of the list and checked it is the end, when two scrolls in a row add no new items, or when a time limit you set runs out with nothing new. Do not keep scrolling to reach a number, and do not keep going after you have enough items just to show there are no more.

If the list stops growing before it reaches the maximum, do not assume it is complete: look in the HAR file for the request the page makes to load more.

Call `heartbeat()` inside a loop to report progress, for example "opened 3 of 10 profiles". Every loop must still have an end.

Check the items you collected against what the user asked for, by their ids and their number. Each saved HTML file comes with how far down the page the window had scrolled (`seen`) and the page's full height (`height`). A list that only puts the rows on screen into the page can look complete when it is not, so treat these numbers as a hint, not as proof.

If the step could not collect everything asked for, return what it collected only if the task allows partial results, and name what is missing. Otherwise stop the step with an error that says what it collected and what is missing.

An empty result is valid when the page shows its own message for having nothing, for example "No messages". A sign-in page, a captcha or an error page is never an empty result.

## Use what earlier windows saved

Questions about page structure should be answered from data already collected. Only open a new window if the live page is needed or data is missing (such as what happens on a click, scroll, fresh load etc.).

Each window during the build saves three files, numbered by window: the HAR file (every request and response), the page's HTML, and a list of every request the page made. The try's result names them under `$capture`. Read them before you open another window.

To work out how to read the data, write a code try that opens no window: pass "$recorded:page" or "$recorded:har" as one of its inputs (add `#2` to use window 2's files, for example "$recorded:page#2"), read it with `read_input`, and change the reading code until it works. If you need several attempts to read the data correctly, make them in code tries like this, not by opening the site again each time. The same applies when you change a browser step that already works: if the change is only to how it reads what the page returned (an id comparison, a regular expression, which keys hold the next batch), try the new reading code in a code try on the last window's files first, and run the browser step again once, at the end, to confirm it on the live page.

Reading that needs the live page, for example content that only appears after a click, stays in the browser step. A browser try does not have to return only raw HTML, and one window does not have to answer every later question.

After the build, a step's results are only the outputs its code writes with `write_output`. The HAR files and HTML from the build's windows are deleted when the build finishes successfully. If a later step needs the raw HTML or JSON, the browser step must write it as one of its declared outputs. When a real run fails, the app saves that run's HAR file and HTML.

There is no limit on how many windows you open. When you open another one because something you need is not in the saved files, say in the `reason` what is missing.

## When a step fails, and testing

When a browser step fails, say what it saw: the last page's address and title, whether the element it waited for was there, and how many items it had collected. Do not include cookies, signed links or private content that has nothing to do with the task.

Do not reload or retry a page that shows a captcha or says you are sending too many requests. Follow "browser/page-state", which says when to ask the user to deal with it in the window.

Keep the real result of each browser try: the app records it. Test the reading code against the saved HTML or HAR file in code tries. A test you write for a step never opens a browser.

Playwright's own documentation: https://playwright.dev/python/docs/library
