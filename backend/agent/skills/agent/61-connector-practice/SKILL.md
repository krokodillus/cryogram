---
name: 61-connector-practice
description: WHEN you design or fix a connector step: how it reaches another service, how it checks the service did what was asked, and how it sends safely
tier: 2
kind: action
depends_on: [07-what-a-plan-contains]
version: 3.0.0
last_updated: 2026-10-13
---

# Connector steps

A connector step talks to one outside service or app. Declare the exact hosts it reaches and whether it only reads (`read_only`); a step that changes something also declares its `external_impact` in one plain sentence. A step that sends a request only to get a login token, and then only reads, is still a step that reads.

## What to write

Give every request a time limit. Check that the service did what was asked, by reading its answer, not only the HTTP status. An HTTP 202 can mean the service accepted the request and will do it later, not that it is done: report only what the answer shows. Write the declared outputs under their names, and keep the id of the operation or of each item when the service gives one.

Read secrets with `get_secret` by their names. Never write a credential (a password, a key or a token), a signed link or a session cookie into a log or an output. Keep a value that is needed but not secret, such as an account's email address, as a setting of its own. Use a credential that belongs to the right account, with access to the right data, for the operation the step does.

Load "61-connector-practice/resilient-request" when the step makes HTTP requests in Python. A request that only reads may be retried a few times when the service is briefly unavailable. A request that changes something is sent once, unless the service's documentation says it is safe to repeat, or the step first checks whether the earlier attempt went through. Let a refusal of the address or the login stop the step. A time limit that runs out after a send may mean the service accepted it: check that before trying again, also when a stopped run is continued.

## Choose one route that fits

Use credentials that are already in an environment attached to this workflow, and the route the user asked for. A service guide is where you start: it does not outrank the service's current official documentation or what you see happen. Offer the user a choice only when the routes that work really differ in what must be set up, what data is shared, what the step can do or how reliable it is. Do not make the user choose for the sake of it.

When there is no suitable connection yet, give only the setup for the route you chose. Check whether the user's account has the permissions before you give instructions for a service's admin console, and do not lead a user who is not an admin through screens they cannot open. Never make data public, forward mail to a new service, loosen access to a database or create a credential with wide access only to make setup easier. When a route shares more data than before, explain it and get the user's agreement. Check menus, login methods and usage limits against the service's official documentation when they matter, because guides go out of date.

## How to structure it

Each connector step reaches one outside service and does one thing there. The login, the pages of results, building the request and checking the answer can stay in that step. Move reading and business rules into a code step of their own when that saves sending the request again each time you change them during the build, but do not move every small conversion into a step of its own. Browser work is a different kind of step, with its own guide.

Identify each row you write, and each item you record as done, by a value that does not change, such as the row's id. Find a column by its header name at the time the step runs, and find the row again before you write back to it. Check that the value identifies exactly one row before you change anything. Two rows with the same content are not necessarily one item.

A step that does something for each item declares `per_item`, `route` and the `requests_per_minute` the user approved. Load "browser/api-routes" for the loop that sends at a set pace and records each item. The step's result says what happened to each item, with a status and a reason. An item the service refused because of the item itself (for example an invalid address) is reported as not sent, with the service's reason, and the loop goes on. When the service fails, says too many requests, or the connection drops, the step stops: raise the error before the item's `checkpoint`, never catch it and carry on, so the item is not recorded as done and the next attempt sends it again. An item recorded as done is skipped next time. Carry on with only part of the items only if the task allows partial results.

## What to test

The approval card asks the user for one small real test send. It is not permission to send anything else. Make the data of the test send, and where it goes, clear in the try's `reason`. Deleting the test data afterwards is another change on the service: offer it if it helps, and never do it by yourself.

Keep the recorded try of the send that worked. The tests you write, and rehearsal, check how the step builds the request and reads the answer, without sending again. Reuse a recorded try while the step's code has not changed. A change to what the step does needs a new try, as the app says.

Load "61-connector-practice/unproven-steps" when the user says no to the test send, or when what starts the send cannot safely be made to happen during the build. Keep the complete code and tests, and say that the step has not been tried for real. Everything else the build checks still applies.

Checks confirm what the service reports it did, and the business rules that must hold, never counts copied from the sample. Refused, accepted for later and confirmed as done are three different outcomes: never report all three as success.
