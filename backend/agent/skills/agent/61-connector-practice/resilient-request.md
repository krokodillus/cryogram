# The resilient request: copy this, change only the request it wraps

Every step that calls an outside service makes the call through this helper. Copy it in whole; the request it wraps is the only part that changes.

```python
import random, time, urllib.error, urllib.request

TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


def request_with_retry(req, *, tries=3, timeout=30, label="the service"):
    """Send `req`, retrying only what is worth retrying.

    Returns the response BODY (bytes). Raises with the service's own
    message when it will not work - never silently.
    """
    # The timeout is always explicit: without one a hung connection
    # waits on the socket default; too tight, and an ordinarily slow
    # service becomes a failed run.
    delay = 1.0
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = e.read()[:400].decode("utf-8", "replace")
            # 4xx = this request is wrong. Retrying cannot fix it, and
            # 429 is the one exception (the service is asking for time,
            # and Retry-After says how much).
            if e.code not in TRANSIENT_STATUS or attempt == tries:
                raise RuntimeError(
                    f"{label} refused the request ({e.code}): {body}")
            wait = float(e.headers.get("Retry-After") or 0) or delay
        except urllib.error.URLError as e:
            # An egress block is NOT transient - let it through untouched
            # so the app can offer to add the host to `domains`; catching
            # it hides the one thing that would fix the step.
            if "egress blocked" in str(e).lower() or attempt == tries:
                raise
            wait = delay
        except TimeoutError:
            if attempt == tries:
                raise TimeoutError(
                    f"{label} did not answer within {timeout}s "
                    f"after {tries} tries")
            wait = delay
        # The sandbox's limit is silence, not runtime: a retrying step
        # that pings keeps going, and the user sees why it is slow.
        heartbeat(f"{label} was slow - retrying in {wait:.0f}s "
                  f"({attempt} of {tries})")
        # Jitter: steps retrying in lockstep hit the service together
        # and are throttled together.
        time.sleep(wait + random.uniform(0, 0.3))
        delay *= 3
    raise RuntimeError(f"{label} could not be reached")
```

Using it:

```python
req = urllib.request.Request(url, data=body, headers=headers,
                             method="POST")
raw = request_with_retry(req, label="Google Sheets")
write_output("rows_written", len(json.loads(raw).get("updates", {})))
```

## Writes get one more rule

A write is one attempt per item. If the send times out you do not know whether it landed, so the step never sends again quietly: let the error out, and the run pauses to ask the user to check. Retrying a read is free; retrying a write can duplicate it.

For per-item sending loops (pacing, checkpoints, resuming half-way), load browser/api-routes.
