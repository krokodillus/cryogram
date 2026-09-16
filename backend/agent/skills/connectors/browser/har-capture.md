# HAR capture: mine a site for its direct API

One browsing pass records every network call the page makes, and the scrubbed recording reveals the site's internal XHR endpoints. A page that loads its data over JSON calls can usually become a plain HTTP connector: no windows, faster, far fewer rate-limit walls. Offer this whenever a site's direct API is unknown and a browser session is open anyway.

## The recording happens by itself

The window records every request and response while you browse, and a build cell keeps that recording. So the browsing pass is just the pass: browse the task's two or so items, then read what the window kept.

```python
browser_goto(page, "https://www.example.com/things")
write_output("items", parsed)
```

The result carries `$capture`: one line per page copy with how much of it had loaded, and the page's calls folded, never cut. A data call (an XHR or fetch, or anything answering JSON) has its own line; repeats of one address, the same method and path with only numbers or paging differing, are one row with their count, first and last time, the interval and the ids of every call in it; every other kind (images, scripts, styles, fonts, media) is one row with its count and ids. Every call keeps its id, its place in the calls file, so any row unfolds in full: read_input("calls", kind="image") is the whole inventory of images, ids=[...] the calls behind one folded row, and url=, status= and method= narrow the same way, on the traffic recording too. Nothing is left out, because sometimes the pictures are the task.

Each call line says when it happened (`at`, in seconds since the window opened), what the thing it returned is called, the address, the kind, the content type, the status and the size. A server that sends its answer in pieces declares no size, so some lines carry none.

The times are how you tell the data apart from the noise. The same address coming back every few seconds, at 0.4, 5.4, 10.4, is the page keeping itself alive, and a call that fires once when the content appears is the one to mine. A page that has nothing left but its repeating calls has finished loading: take what you came for and let the window close, because everything after that is the same call again.

Read the listing, pick what you want, and chain it into a plain code cell:

- `"$recorded:har"` is the recording itself, holding what every call sent back. Read it as a file and pull the bodies of the calls you picked.
- `"$recorded:page"` is the page as the step left it, with its links, ids and attributes.
- `"$recorded:calls"` is the whole listing as a file, one line per call with its id, for a cell that works through the calls in code or unfolds a row with the filters.

None of that needs another window, and none of it needs to pass through the conversation: read the file in a cell and write out the part that matters. A second window for something the first one already loaded is the mistake this listing exists to prevent - if the listing shows the call, the answer is in the recording.

## What to read in a HAR, and what never to

Most entries are noise. Read in this order:

1. XHR and fetch requests answering JSON (`content-type: application/json`), the site's own data calls. `graphql`, `api` and versioned paths (`/v1/`, `/v2/`) are the strong hints.
2. Rank the remainder by response size and by whether the body contains a value you saw on the page (a price, a name). The payload that carries the screen's data is the one to mine.
3. The main document request matters only when the page is server-rendered, with the data arriving as HTML and no JSON call carrying it; that is the parse-the-DOM case.

Skip outright: static assets (images, fonts, CSS, JavaScript) and analytics or tracking hosts (google-analytics, googletagmanager, doubleclick, facebook, hotjar, segment, sentry and their kin). Nothing about the task ever lives there.

## Jar-auth: test the mined API without ever seeing the token

The mined endpoint usually needs the session. The value is already on disk in the profile's cookie jar, so use it in the cell, never in your context, on the same principle as get_secret: names and shapes for you, values only inside code.

```python
import requests
jar = {c["name"]: c["value"]
       for c in page.context.cookies("https://www.example.com")}
s = requests.Session()
s.cookies.update(jar)
r = s.get("https://www.example.com/api/v2/items?limit=2",
      headers={"accept": "application/json"})
write_output("sample_items", r.json())
```

Once the person has signed in, the session is in the profile's jar, so a code cell reads the cookie it needs from the profile's saved session rather than launching a window. The built connector uses the same shape at run time, so the proven cell is the step. Never read a cookie value into an output, a note or the chat, and never hardcode one into step code: it expires, and it is a credential.

## When the mined route stops working, it is closed

A direct call that starts answering with a challenge, a captcha, "unusual activity", or a sudden 403 on a call that worked is not a bug to debug: the site has decided this client is not a browser. Move up the ladder (load "browser/api-routes"), to the in-page fetch or the UI route, or reduce volume. Never respond by imitating a browser harder (spoofed agents, header shuffling, proxies): it does not hold, and it escalates a run-level problem into an account-level one.

## The session authorises the declared task only

A jar cookie lets code call any endpoint as the user. The contract: only the endpoints the proven task needs, on the declared domains, at exploration volume of about two items. Touching anything beyond the declared task with the user's session, other endpoints, their account pages, bulk reads, needs their explicit yes first, with the endpoint named.
