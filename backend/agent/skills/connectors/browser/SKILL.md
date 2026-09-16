---
name: browser
description: WHEN a workflow step needs data from a website that blocks automated requests (a 403 or bot check on a plain fetch) or has no usable API - Reddit always, go straight here - drive the user's real Chrome with Playwright
tier: 2
kind: action
depends_on: []
version: 2.8.0
last_updated: 2026-09-15
---

# Reading websites with a real browser

The fallback ladder: an official API or export that works comes first, and this route takes over when the official one is difficult (approval-gated, admin-only), fails, or the user prefers not to use it. Reddit defaults here, because its API is approval-gated and its listings are bot-checked. Anywhere else, one 403 or bot check on a plain fetch means switch; never iterate headers against a bot wall.

A step that needs a logged-in session loads `browser-login`.

The reference documents, loaded the moment their condition holds:

- Load "browser/har-capture" when you mine a recording. The window records every call by itself, and reading that recording is how a site's own data calls are found, which often turns the step into a plain connector. Load it when you have a recording to read, never to make one happen. It also carries jar-auth, calling the mined API with the session's cookies.
- Load "browser/page-state" before writing any unattended page-reading step: the content-marker gate, and the run-time human handoff when a captcha, login or block page appears.
- Load "browser/demo-recording" when the user must show a flow (an undocumented portal): the app records their demonstration, with their explicit yes first.
- Load "browser/api-routes" when a step sends or writes per item, or a site starts refusing the direct call: the three routes, when a route is closed, and the paced, checkpointed, resumable loop every sending step uses.

## The window is the app's; you write the page work

```python
browser_goto(page, "https://...")        # `page` is already open, on the step's url
...whatever this step does on the page...
write_output("rows", rows)
```

`page` is handed to you. The app opens the window before your code runs, on this workflow's own profile (so a login sticks), with the flags a logged-in site needs, recording every request and response, copying each page it settles on, and it closes the window afterwards - after your declared outputs have been checked, so a step that produced the wrong shape still has the page and the traffic behind its issue. Opening and closing are not yours to write and there is no `with` block: a step that declares itself `type: browser` gets a window, and no other type may open one. `confirm_with_user` shows the window when the person must see it.

`page` is the real Playwright page: click, type, scroll, wait, read frames, accept downloads, and loop over as many pages of this step's kind as the work needs.

- `browser_goto(page, url)` is how you navigate: it retries, and it remembers where the step means to be, so a login that lands you on a feed is undone before the step carries on. It gives up with `unexpected_input` naming the address rather than reading whatever loaded.
- A page that loads as you go is yours to scroll, with `page` like any other page work, and a copy taken before that scrolling holds only what had loaded, with nothing in the html to say it is short. Every page copy says how much of the page it had seen against the page's height, so check that before you parse a list, feed or search result, and when there was more, say you have the first part rather than reporting the count as the total.
- A BUILT step keeps only what its code writes out - except when it fails, and then the window's recording is kept as the issue's evidence. A BUILD CELL keeps everything either way, because looking at the page is the point of opening it, and the cell's own result names what came back (`captured`, and a line saying how to read it) in the same turn you decide what to do next. The result carries `$capture`: a line for every call the page made, and a line for every page copy with how much of it had loaded. Read that listing and you know what the window saw; chain the pieces you want into the next cell. One window answers every later question about that page.
- The app puts `playwright` in the step's packages itself, because the step is `type: browser`; never `playwright install`. The plan step declares the `url` it opens, which is what the person is asked about. The step's own code reaches no address: the page's traffic is the browser's, and a call to a service outside the window is its own connector step.
- A step that needs a particular page size, language or time zone declares `browser_options` beside its `url`, for example `{"viewport": {"width": 1400, "height": 900}, "locale": "en-GB"}`. There are none by default, and the profile, the recording and the launch flags are the app's.
- When only Edge is installed, say so in the plan; the app opens Chrome, Chrome Beta, Chromium or Edge, whichever is there. "profile is already in use" means a leftover Chrome is open on that profile: the run pauses and asks the person to close it.

## Everything the page holds, in the first window

A copy holds only what had loaded when it was taken, and a second window for what the first one missed is another visit to the site. So before the first window opens, work out how this page puts its content on screen, and handle each of these in that same cell:

- Content that loads as you scroll. Scroll it yourself, one screen at a time with a short wait after each, until the page stops growing and the part you need is there: a list that keeps adding rows and a section that only loads when it comes into view (the experience on a profile, the reviews on a product page) both need it, and one jump to the bottom can pass a section by.
- Content that arrives after the page looks ready. Wait for the part you need to appear (`page.wait_for_selector` on its marker), not for a fixed time.
- Content behind a control. A "see more" link, a "show all" button, a collapsed section, a tab or an accordion holds text that is not in the document until it is opened. Open each one the step needs before the copy.
- Content on its own page. When the page shows a shortened list and links to the full one, the full list is the page this step opens.

Then check what came back before deciding it is complete: every page copy says how much of the page it had seen against the page's height, and the listing names every call the page made, folded by address and kind with the ids to unfold any row. When a part you need is missing, read the recording to find out why before asking for another window, and ask for one only with a change that fixes that reason.

## One step is one kind of page

A browser step works on one kind of page: a list with its further pages, the profiles that list names, a thread and the reply in it. It may loop over as many pages of that kind as the work needs and act on each, and it opens no tabs. Getting something from one kind of page and acting on another is two steps: the first reads the list of contacts, the second opens each contact's profile from that list. The browser profile persists between steps, so the session carries over and the second step does not sign in again. Split that way, each step is small enough to re-test on its own, and a change to one never risks the other.

## Fetch once, parse separately: the browser is a live send

Opening the browser is a live send: the user's real Chrome and their real rate-limit budget. The browser cell's only job is bringing back the raw payload in one recorded run; all parsing and shaping iterates in a plain code cell chained through "$recorded". A window opens again when the next question needs the live page, such as whether scrolling loads more or how the next page of a list is fetched, and only after reading what the earlier windows kept. Each window's recording is numbered and kept beside the reason you gave for it until the build is done, so `"$recorded:har#2"` against `"$recorded:har#3"` compares the page scrolled and not scrolled. A probe cell, which belongs to no step yet, passes the address it opens as `url`. The plan step is `type: browser` (never a connector with a flag); it carries read_only, always true or false (true when it only reads the site, false when it sends anything there), the `url` its window opens, external_impact and no domains. A window will not open for a browser step that has not declared both read_only and url. Every window records everything by itself, in the cell and in the built step, every request and response and each page it settles on. Nothing about recording is yours to write.

The recording is the page: every later check, parse or count reads the recorded HTML or HAR through "$recorded", and you open the page again only for something the recordings cannot show.

One probe cell, not four: work out the page's structure in a single pass, dumping the container, the repeated item, every candidate field and the link in one `evaluate`, then answer every follow-up question off that recorded dump in plain code cells.

## Scraping the page

- The page you asked for is the page you scrape: after every goto, confirm it (the page or offset parameter survived, or the pagination marker matches) before reading. A mismatch is `unexpected_input`, never a quiet scrape of whatever loaded. A step given a link never rewrites its query parameters except the one it advances; replacing a deep page number with pages 1 to 4 re-harvests old results every run.
- Prefer stable element attributes over text or CSS-path selectors, and read the whole map in one `evaluate`, never a locator per field.
- Infinite feeds: scroll with stall detection, stopping after a number of rounds with no new items, never until a target is reached.
- `heartbeat()` every iteration, saying how far you are: it shows live on the activity line, and a step dies only after 120 seconds of silence.
- Exploration works on 2 items, passed as 2 for the step's maximum setting in the cell's inputs; the code reads that setting and never has the number written in.
- Transient error pages exist: detect the marker, wait, reload, retry twice. A rate-limit or challenge page is not transient: stop, per page-state.

## Partial results: a list degrades, it does not fail

Only on the expected page, after the page-state gate: 30 of 50 is a result, and you raise only when there is nothing, after retries. A short list from a captcha, login or block page is never a quiet day. Many pages is per-item work: `per_item` and `checkpoint()` per page.

## Failures name what the page showed

Every raise reports the observed state: the final URL, the title, which known marker was present (a login link, a challenge, an error banner), the items found. A bare "could not load" is undiagnosable.

## What to test

The recorded run is the evidence, plus authored parsing tests (this saved attribute map gives these fields). Never author a test that opens the browser.

Docs: https://playwright.dev/python/docs/library
