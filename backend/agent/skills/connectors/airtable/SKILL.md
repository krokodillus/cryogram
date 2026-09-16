---
name: airtable
description: WHEN a workflow step needs to read or write records in Airtable - the personal-access-token route, exact user setup steps, the request pattern, batching and known gotchas
tier: 2
kind: action
depends_on: []
version: 1.1.0
last_updated: 2026-09-06
---

# Reading and writing Airtable records

## The route: a personal access token

Legacy API keys are dead (removed in February 2024): a personal access token plus the REST API is the only live route. No OAuth app, no admin.

## User setup

Give it complete:

1. https://airtable.com/create/tokens, then "Create new token", and name it.
2. Scopes: add `data.records:read` for reading and `data.records:write` for writing, only what the step needs.
3. Access: add the specific base the workflow touches. One question to the user: just this base, or the whole workspace? The token can never exceed the user's own permission, so writes need editor or better.
4. Copy the token (shown once, starts with `pat`) and collect it through the masked ask, as a secret named for what it is (the Airtable token).
5. The ids come from the URL: open the table in the browser, airtable.com/appXXXX/tblYYYY/..., where the base id is the `app...` segment and the table id the `tbl...` segment. Prefer the `tbl` id over the table name, since it survives a rename; both work in the URL.

## The request pattern

- Base URL `https://api.airtable.com/v0/{baseId}/{tableId}` with the header `Authorization: Bearer <token>`.
- Read: GET; `pageSize` at most 100; a response with more pages carries an opaque `offset`, which you pass back verbatim to continue. `filterByFormula` narrows server-side (URL-encode it).
- Create: POST `{"records": [{"fields": {...}}, ...]}`, at most 10 records per request (a hard limit), so loop in batches of 10.
- Update: PATCH merges only the named fields. PUT is destructive, clearing every field not sent, so use PATCH.
- Upsert: PATCH with `"performUpsert": {"fieldsToMergeOn": ["Email"]}`. Zero matches creates, one updates, and more than one fails the whole request, so merge on a field that is genuinely unique.
- timeout=15; retry timeouts, connection errors and 5xx with a short backoff; on a 429 wait 30 seconds (no Retry-After is documented); never retry any other 4xx.
- The success signal is a 200 with `{"records": [...]}`, each record carrying `id`, `createdTime` and `fields`. Errors come as `{"error": {"type", "message"}}`.

## What goes wrong

- The rate limit is 5 requests a second per base: batch 10 per request and pace. A bulk load is batches with a small sleep, never a request per record.
- Computed fields are read-only (formula, rollup, lookup, count, autonumber, created and modified times): writing one gives a 422. Send only the fields a human could type.
- `typecast: true` converts strings best-effort and silently creates new select options, so a typo becomes a new category. For a controlled list leave typecast off and surface the 422 (INVALID_MULTIPLE_CHOICE_OPTIONS) so the user fixes the value.
- Fields are keyed by name by default, so a rename in Airtable breaks the step. For a long-lived workflow key by field id and set `returnFieldsByFieldId: true`.
- A 403 means the token was not given this base (fix it under the token's Access). A 404 means a wrong base or table id (re-copy it from the URL). A 422 means a value does not fit the field, and the body names it.
- The domain is api.airtable.com.

## If company IT blocks it

When token creation is disabled by workspace policy, the create-token page says so. The one-line ask for the workspace admin: please create a scoped personal access token limited to the base, with data.records read and write, and share it with me. A personal access token is bounded by the user's own permission, the least-privileged Airtable integration.

## Official docs

Check here if a route fails: https://airtable.com/developers/web/api/introduction
