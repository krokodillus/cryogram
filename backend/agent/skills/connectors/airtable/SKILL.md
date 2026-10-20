---
name: airtable
description: WHEN a step reads or writes Airtable records: how to get a personal access token, how to page through records, how to write them, and what makes a request fail
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Reading and writing Airtable records

## Use a personal access token

If the user already has a working connection to Airtable and it suits this step, use it. Otherwise a personal access token with limited permissions is a simple way to reach the user's own base, though it is not the only way to connect. Before you ask the user to create a token, check that their Airtable workspace lets them create one. Never use a legacy API key.

## What to tell the user to do

Give all five steps at once:

1. Go to https://airtable.com/create/tokens, choose "Create new token", and give it a name.
2. Under scopes, add `data.records:read` if the step reads and `data.records:write` if it writes. Add only the scopes the step needs.
3. Under access, add only the base the workflow needs. Give the token access to the whole workspace only when the workflow really works across several bases; do not ask the user about it as a matter of course. A token can never do more than the user who made it can.
4. Copy the token. Airtable shows it once, and it starts with `pat`. Ask for it with the masked ask (the card that hides what the user types), and store it as a secret with a name that says what it is, such as the Airtable token.
5. Read the ids from the table's address: open the table in the browser, and the address looks like airtable.com/appXXXX/tblYYYY/... The base id is the part that starts with `app`, and the table id is the part that starts with `tbl`. Use the `tbl` id in the request rather than the table's name, because the id still works after the user renames the table. The table's name also works in the request address, but the id is safer.

## How to write the requests

- The base address is `https://api.airtable.com/v0/{baseId}/{tableId}`, and every request carries the header `Authorization: Bearer <token>`.
- To read, send GET. Set `pageSize` to 100 at most. When a response has more pages, it contains an `offset` value: pass it back exactly as you received it to get the next page. Put a formula in `filterByFormula` to make Airtable filter the records before it sends them (URL-encode the formula).
- To create records, send POST with `{"records": [{"fields": {...}}, ...]}`. A request can hold 10 records at most, and Airtable enforces this, so send the records in batches of 10.
- To update records, send PATCH, which changes only the fields you name. Do not send PUT: it clears every field you leave out. Use PATCH.
- To update a record if it exists and create it if not, send PATCH with `"performUpsert": {"fieldsToMergeOn": ["Email"]}`. If no record matches, Airtable creates one. If one matches, it updates it. If more than one matches, the whole request fails, so merge on a field that has a different value in every record.
- Set `timeout=15`. A request that only reads may be retried a few times when the service is briefly unavailable, and you must keep the number of retries small. Wait as long as Airtable's documentation says to wait after a rate-limit response. A request that writes is sent once, unless a mechanism that you have checked makes it safe to repeat.
- A request worked when Airtable answers 200 with `{"records": [...]}`, where each record has `id`, `createdTime` and `fields`. An error comes back as `{"error": {"type", "message"}}`.

## What goes wrong

- Airtable allows 5 requests a second for each base. Send 10 records per request and wait between requests. Load many records in batches with a short pause between them, and never send one request per record.
- A computed field cannot be written: formula, rollup, lookup, count, autonumber, created time and modified time fields. Writing one gives a 422 error. Send only the fields a person could type into.
- `typecast: true` makes Airtable convert strings as well as it can, and it creates a new select option for any value it does not know, so a typo becomes a new category. When the field has a fixed list of options, leave `typecast` off and show the user the 422 error (INVALID_MULTIPLE_CHOICE_OPTIONS), so they correct the value.
- Airtable identifies fields by their names unless you ask for ids, so a field the user renames in Airtable breaks the step. For a workflow that will run for a long time, identify the fields by id and set `returnFieldsByFieldId: true`.
- For a 403 or 404 error, check the token's access and check the base id and table id you used. The status code does not tell you which of them is wrong. A 422 error usually says in its body what is wrong with the fields or values.
- The host to declare is api.airtable.com.

## If the company's IT department blocks it

When the workspace's rules stop the user creating tokens, the create-token page says so. Give the user this one sentence to send to the workspace admin: please create a personal access token limited to the base, with data.records read and write, and share it with me. A personal access token can only do what the user can do, so it is the Airtable connection with the fewest permissions.

## Official docs

If a request fails, check Airtable's documentation: https://airtable.com/developers/web/api/introduction
