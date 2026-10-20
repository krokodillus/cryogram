# HTTP requests with a time limit, and when a retry is safe

Use this helper for HTTP requests in Python. For a service's own Python library, or for SMTP or IMAP, use that library's time limit and error types with the same rules for retrying. A request that only reads may be retried when the service is briefly unavailable. A request that changes something is sent once by default. Retry it only when the service promises to ignore a repeated request sent with the SAME idempotency key, or when its documentation shows that repeating the operation is safe. Recording an item with `checkpoint` does not make a retry safe.

```python
import random, time, urllib.error, urllib.request
from email.utils import parsedate_to_datetime

TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _retry_after(value, fallback):
    if not value:
        return fallback
    try:
        return max(0.0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return fallback


def request_with_retry(req, *, tries=None, retry_safe=False, timeout=30,
                       label="the service", max_wait=60):
    """Return response bytes, or raise. No silent repeat of an unsafe write."""
    method = req.get_method().upper()
    safe = method in {"GET", "HEAD", "OPTIONS"} or retry_safe
    tries = (3 if safe else 1) if tries is None else tries
    if not isinstance(tries, int) or not 1 <= tries <= 5 or timeout <= 0 or max_wait <= 0:
        raise ValueError("tries must be 1..5; timeout and max_wait must be positive")
    if tries > 1 and not safe:
        raise ValueError("retrying this write requires a verified idempotency guarantee")
    delay = 1.0
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            # Do not echo response bodies or URLs: either may contain secrets.
            if e.code not in TRANSIENT_STATUS or attempt == tries:
                raise RuntimeError(f"{label} refused the request (HTTP {e.code}); check the recorded response") from e
            wait = _retry_after(e.headers.get("Retry-After"), delay)
        except urllib.error.URLError as e:
            if "egress blocked" in str(e).lower() or attempt == tries:
                raise
            wait = delay
        except TimeoutError:
            if attempt == tries:
                raise TimeoutError(f"{label} did not answer within {timeout}s; outcome may be unknown")
            wait = delay
        if wait > max_wait:
            raise RuntimeError(f"{label} requests a {wait:.0f}s wait; stop and retry later")
        # Respect the requested delay; never cap it then retry early.
        remaining = wait + random.uniform(0, 0.3)
        while remaining > 0:
            heartbeat(f"{label}: retry {attempt + 1}/{tries} in {remaining:.0f}s")
            pause = min(remaining, 10)
            time.sleep(pause)
            remaining -= pause
        delay *= 3
    raise RuntimeError(f"{label} could not be reached")
```

To read: `raw = request_with_retry(urllib.request.Request(url), label="the list service")`.

To send:

```python
req = urllib.request.Request(url, data=body, headers=headers, method="POST")
raw = request_with_retry(req, tries=1, label="the write service")
response = json.loads(raw)
# Validate the documented success field, not just HTTP 200. Output the
# service's confirmed row/item count, not the number of keys in its response.
```

When you cannot tell whether a send arrived, that is never a reason to send it again. Check it with the id the service gave the operation or the item, or ask the user to check. For a step that sends something for each item, load "browser/api-routes": its records of finished items keep the items that are done, but they cannot make a send on another service all-or-nothing.
