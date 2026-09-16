# The route ladder: how a site call is made, and how a long loop survives

Three ways to make the same call. Climb only as far as the site forces: tier 1 is fastest and most fragile, tier 3 slowest and most faithful. Whatever the user chooses, the loop pattern at the end is not optional for anything that sends.

## Tier 1: plain HTTP with the session from the jar

`requests` plus the profile's cookies (browser/har-capture). Fast, cheap, no window. Fragile where a site fingerprints clients: the TLS signature and header order are Python's, not Chrome's, so a strict site can answer a 403 or a challenge on a perfectly valid session. A good default for tolerant sites and for reads.

## Tier 2: the call from inside the page, recommended when a site is strict

The request leaves the real Chrome: the right TLS, the right headers, the right cookies, the right `Referer`, and the page's own CSRF and session tokens are in scope. Nearly tier-1 speed, browser-faithful.

```python
page.goto("https://www.example.com/dashboard")   # be on a real page first
res = page.evaluate("""async ({path, body}) => {
    const r = await fetch(path, {
      method: "POST",
      headers: {"content-type": "application/json",
                "csrf-token": window.__CSRF__ || ""},
      body: JSON.stringify(body),
      credentials: "include",
    });
    return {status: r.status, body: await r.text()};
}""", {"path": "/api/v2/invitations", "body": {"id": item_id}})
```

Read the exact endpoint, headers and token source from the recording (browser/har-capture and 25-recordings); never guess them.

## Tier 3: drive the UI

Playwright clicking the real controls by stable selectors (the browser guide's scraping rules). Slowest, and it breaks when the DOM changes, but it is the only route when the flow has server-side steps the API path skips. Do not synthesise human-looking mouse paths: `click()` already dispatches trusted events at the element, and hand-rolled cursor drift only adds flakiness.

## When a route is closed: stop, do not adapt

A route that starts returning challenges, captchas, sudden 403s on calls that worked, or "unusual activity" is closed. Say so and move up the ladder, or reduce volume. Never react by looking more like a browser: rotating agents, proxies or fingerprints is an arms race that loses, and it puts the user's account at risk rather than just the run. A rate-limit page is a full stop, always.

## The loop pattern: paced, checkpointed, resumable

Anything that works through a list per item uses this shape, sends, writes and fetches alike. It is what makes a half-finished run safe: the step declares `per_item: {"input": "items", "key": "id"}` on its plan step, and its outputs come from the record, so it finishes on any subset of the list and the user can proceed with the saved results after a stop.

```python
import random, time
items = read_input("items")
done = checkpoints()                     # what a previous run finished
for item in items:
    key = str(item["id"])                # stable per item, never the index
    if key in done:                      # resume: never re-send
        continue
    result = send_one(item)              # the tier-1/2/3 call
    checkpoint(key, {"id": key, **result})   # durable, immediately after
    done = checkpoints()
    heartbeat(f"done {len(done)} of {len(items)}")
    time.sleep(random.uniform(4, 9))     # pace with jitter, never a fixed beat
write_output("sent", list(checkpoints().values()))
```

- `checkpoint(key, ...)` fires immediately after the side effect, never at the end of the batch: the crash you are guarding against happens between the two.
- The key is the item's own identity (a row id, a URL, a recipient). An index breaks the moment the input list changes.
- Pace with jitter: a fixed interval is itself a signature, and bursts are what actually burden a service.
- One send per iteration; never fire a batch concurrently.
- The step declares `requests_per_minute` (and `route`) on its plan step, and the pacing above matches what was declared.
- A rate-limit response is not caught and retried: let it raise. The run stops saying how many of how many are done; trying again continues from the record, and the user may proceed with the saved results instead.

## Volume is the user's call

If the user asks for more per run, or faster, than is wise: say once, plainly, what the trade is (account risk, and that spacing is what keeps a site friendly), recommend the robust shape, then build what they chose. The opening card carries the setting, so their approval is their informed yes. Never re-litigate it afterwards.
