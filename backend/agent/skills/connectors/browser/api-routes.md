# Three ways to make a request to a site, and how to make a long loop safe to resume

Choose the way the user's permissions and the site's own interface allow. If one way fails, that does not give you permission to get round a site that refuses access. The loop at the end of this guide is for a step that does something to each item in a list, not for a single request.

## Way 1: a connector step with its own credential

Use plain HTTP requests when the site's API accepts a credential the user has given for this workflow, with the hosts and the impact declared on the step. A step cannot read the cookies of the browser profile. Do not copy cookies into a step's output, and do not read the app's storage to make a credential.

## Way 2: a request from inside the signed-in browser page

Call only an address and use only a login method you have seen the page itself use, in the HAR file of a window. The step stays a browser step, with the same window and send approvals. Never put a session token in an output, and never use this way to get round a refused permission or a "too many requests" answer.

```python
browser_goto(page, "https://www.example.com/dashboard")
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

Take the exact address, headers and where the token comes from out of the HAR file (see "browser/har-capture" and "25-recordings"). Never guess them.

## Way 3: click through the site's own pages

Use Playwright to click the real buttons and fields, found by selectors that do not change between page loads (see the browser guide). This is the slowest way, and it breaks when the site changes its page, but it is the only way when the site does something on its server between pages that a direct request would skip. Do not make up mouse movements that look human: `click()` already sends a real click to the element, and extra pointer movement only makes the step less reliable.

## When a way is closed: stop and find out why

A captcha, a "too many requests" answer or a refused permission stops the step. Find out why from the response or the page. Do not switch to another way on your own, and do not change the browser's identity, use proxies or retry past it. Ask the user to renew their sign-in or agree another way that is allowed; a verification on the page is handled as "browser/page-state" describes.

## A loop that can stop and carry on

When a step does something to each item and must be able to carry on after a stop, record each item as done. Declare `per_item: {"input": "items", "key": "id"}` on the plan step, and a `requests_per_minute` input or setting with the rate the user approved. A run may carry on with only some items done only if the workflow says partial results are fine. A recorded item is skipped next time. But if the step crashes after the site accepted an item and before the step recorded it, nobody knows whether that item went: check on the site, or use the service's own protection against sending twice, before sending it again.

```python
import random, time
rpm = float(read_input("requests_per_minute"))
if rpm <= 0:
    raise ValueError("requests_per_minute must be positive")
items = read_input("items")
keys = [str(item["id"]) for item in items]
if len(keys) != len(set(keys)):
    raise ValueError("item identities are not unique; resolve duplicates before sending")
done = checkpoints()                     # what a previous run finished
for item in items:
    key = str(item["id"])                # stable per item, never the index
    if key in done:                      # already acknowledged locally
        continue
    result = send_one(item)              # one attempt; reconcile ambiguous prior sends first
    checkpoint(key, {"id": key, **result})   # durable, immediately after
    done = checkpoints()
    heartbeat(f"done {len(done)} of {len(items)}")
    time.sleep(60.0 / rpm + random.uniform(0, 0.3))  # never faster than approved rate
write_output("sent", checkpoint_values(keys))   # this input's recorded results, as a list
```

- `checkpoints()` returns a record of the items already done, keyed by item, for skipping them. A list output takes `checkpoint_values(keys)`: the recorded results for this input's keys, as a list in the same order.
- Call `checkpoint(key, ...)` straight after the site has confirmed an item. It cannot cover the moment between the site accepting an item and your code recording it, so an item whose outcome is unknown must be checked, never simply sent again.
- The key is the item's own identity (a row id, an address, a recipient), never its position in the list, which changes when the list changes.
- Send no faster than the approved rate. The small random pause keeps requests from arriving in bursts. Never use timing to hide from a site's bot detection.
- Send one item at a time. Never send several at once.
- The plan step declares `requests_per_minute` (and `route`), and the pause in the code matches what it declares.
- Do not catch and retry a "too many requests" answer: let the error stop the step. The run stops and says how many items of how many are done. Trying again carries on from the recorded items, or the user can go on with the results saved so far.

## How many and how fast is the user's decision

Use the number of items and the rate the user agreed, within the site's limits and the user's permissions. If a choice makes the step clearly less reliable, explain that once, and never change the user's setting without telling them. A user's preference cannot override a site's limits or what the user is allowed to do on it.
