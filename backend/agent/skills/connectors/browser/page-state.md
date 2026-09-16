# Page state: the content gate, and the human handoff at run time

A page is only worth scraping once you have confirmed it is the page you meant. A captcha, a login wall or a block page ("whoa there", "verify you are human") scrolls and parses just like a feed; it simply contains nothing, so a step that starts extracting without checking returns a short or empty list that reads as a quiet day.

The rule: the degraded-list rule (30 of 50 is a result) applies only when the page is the expected one. On any other page state, never return a partial list; hand off or raise, naming the state.

## The gate, before any scrolling or parsing

Check one positive marker of the expected content, and the known walls:

```python
STATE_JS = """
() => ({
  content: !!document.querySelector('shreddit-post'),   // the site's item marker
  login: !!document.querySelector('a[href*="login"], form[action*="login"]'),
  challenge: /verify|captcha|unusual traffic|whoa there/i.test(
      document.title + ' ' + (document.body ? document.body.innerText.slice(0, 2000) : '')),
})
"""
state = page.evaluate(STATE_JS)
```

Pick the item marker from the recorded exploration dump (the one probe cell); it is the selector the parse already relies on.

Three outcomes, not two:

- `content`: proceed.
- a wall (`challenge` or `login`): the human handoff below at run time, or stop and tell in exploration.
- neither: this is simply the wrong page (a pasted link that is not a search page, a redirect somewhere else). Call `unexpected_input("this does not look like a <what the step expects> page", found=f"title {title!r}, url {url!r}", expected="...")` immediately, never the human wait (there is nothing for a person to solve) and never scraping onward. The run halts at this step showing what arrived against what it needs, and the user is asked, which is exactly the diagnosis a wrong link deserves; fixing downstream steps before checking the pasted link wastes the whole repair.

## At run time, a wall means a human, not a retry

The step runs unattended with the window off screen. When the gate says challenge or login instead of content, ask the person: `confirm_with_user` brings the window on screen, reaches them wherever they are (a notification, a card on the page) and returns the moment they confirm, hides the window again and takes the page back to where the step meant to be. Only a browser step may call it, and only for something that can be done in the window; a value the run lacks pauses with its own form, and a choice partway is a user-input step.

```python
def ask_the_person(page, what="a verification check"):
    confirm_with_user(f"Please complete {what} in the browser window, "
                      "then confirm.", page=page)   # shows the window, returns when they do
    return page.evaluate(STATE_JS)["content"]
```

- Confirmed: the window is off screen again and the step continues normally; the run never knows the difference.
- With `page=` passed, the wait also ends the moment the window is closed: the run pauses saying so, and that step runs again when they are ready. Nobody answering pauses the same way. Neither is a defect and neither raises an issue; they are the run waiting on a person.
- Still not content after they confirm: `unexpected_input` naming the observed state, as anywhere else.
- Never retry or reload through a challenge (it digs deeper, and the account is what is at risk), and never leave the window on screen after the gate passes.

## In exploration cells

The same gate runs first, but there is no unattended user to wait for; the person is already in the chat. Stop and tell them what the page showed (the final URL, the title, which marker), per the guide's failure rule.

## What to test

The recorded exploration run proves the content path. The wall path cannot be triggered on demand, so it is untested by choice: state that, keep the gate in the code, and the first real wall proves it.

## Timeouts are handled in the step

A page that is slow is not a reason to stop the run, and not the harness's problem: `browser_goto` already tries three times with a pause and then names the page state through `unexpected_input`, an ordinary stop the user can resume from later, with the page kept as evidence. A wait for a specific element inside the page follows the same shape:

```python
def wait_for_content(page, selector="main", tries=3, wait_ms=15000):
    for attempt in range(tries):
        try:
            page.wait_for_selector(selector, timeout=wait_ms)   # the content marker
            return
        except Exception:                             # noqa: BLE001
            heartbeat(f"page slow, try {attempt + 1} of {tries}")
            page.wait_for_timeout(2000 * (attempt + 1))
    unexpected_input(f"the page did not show its content after {tries} tries",
                     found=page.url, expected=selector)
```
