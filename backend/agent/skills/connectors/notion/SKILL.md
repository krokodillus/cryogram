---
name: notion
description: WHEN a step reads or writes rows in a Notion database: which connection to use, how to find the database's data source, and the exact shape of each property
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Reading and writing Notion database rows

## The route: an internal integration token

Reuse a suitable connection that already exists. An internal integration is a simple choice for a workspace and a database the user owns. A connection that is already authorised through OAuth is also a good route. Check the user's permission in the workspace before you create another integration.

## Steps for the user

Give the user all four steps together:

1. Go to https://app.notion.com/developers/connections (notion.so/my-integrations is the old address and redirects there). Create a new integration: give it a name, pick the workspace, choose the type Internal, and copy the secret. It starts with `ntn_`; older `secret_` tokens still work. Ask for the token with the masked ask, and save it as a secret named for what it is (the Notion token).
2. Set the integration's capabilities: Read content for a step that reads, and Insert content and Update content as well for a step that writes.
3. The step everyone forgets: in Notion, open the target database, open the `...` menu at the top right, choose Connections and add the integration. Without this, every call returns 404 object_not_found. That looks like a wrong id, but it means the database is not shared with the integration. A connection on a page or database also applies to the pages inside it.
4. The database id comes from the URL while the database is open as a full page. An inline database (one shown inside another page) must be opened with "Open as page" first, because copying the URL of the outer page is the classic mistake. The id is the 32-character hex segment before `?v=`, and `?v=` in the URL confirms that it is a database URL.

## How to make requests

- Send these headers on every call: `Authorization: Bearer <token>`, `Notion-Version: 2026-03-11` and Content-Type application/json. The version header is required. Pin it, so that a built workflow's behaviour does not change later. On this version "delete" is `in_trash` and a list position is a position object. Older pinned versions such as 2025-09-03 still work, but differ in these two things.
- Find the data source when you set up the step: GET `https://api.notion.com/v1/databases/{database_id}`. If `data_sources` has exactly one entry, use that data source. If it has several, pick the one that matches the table the user asked for, by name or schema, or ask the user. Never take the first entry without checking. Save the checked id and use it in the queries of each run.
- Read rows: POST `/v1/data_sources/{data_source_id}/query` with an optional `filter` and `sorts`. To read more than one page of results, send `start_cursor` and `page_size` (at most 100). The response is `{results, has_more, next_cursor}`.
- Append a row: POST `/v1/pages` with `{"parent": {"type": "data_source_id", "data_source_id": "..."}, "properties": {...}}`.
- Update a row: PATCH `/v1/pages/{page_id}` with `properties`.
- Use timeout=15. Retry a read a limited number of times when the failure is temporary, and wait for the time in Retry-After. This includes a read-only query POST that is explicitly marked retry_safe. Send a write that changes something once, unless you have checked that the service itself removes duplicates.
- Success is a 200 on every call, including creating a page. An error body is `{"object": "error", "code", "message"}`.

## Property shapes

The exact shapes matter. Most 400 errors are shape mistakes.

- title, the required "Name" column, exactly one per database: `{"title": [{"type": "text", "text": {"content": "Row name"}}]}`.
- rich_text: the same array shape under `"rich_text"` (2,000 characters per request).
- select: `{"select": {"name": "Option"}}`. An unknown name creates the option without any warning, the opposite of Airtable. When the options are fixed, check the name against the list of known options first.
- status: `{"status": {"name": "In progress"}}`. The option must already exist.
- multi_select: `[{"name": "A"}, {"name": "B"}]`. number, checkbox, url and email are plain values. date: `{"date": {"start": "2026-07-27"}}`.
- Computed properties (formula, rollup, created and edited time or by, unique_id) are read-only. Writing one gives a 400.
- To read text back, join the `plain_text` fields of the array.

## What goes wrong

- A 404 can mean the integration has no access, or the object id is wrong, or the object is not available. Check the actual object type and id, and whether the connection is shared with the integration. `?v=` in the URL alone does not tell you which of these it is.
- A 403 restricted_resource means a capability switch is missing on the integration (step 2). It is not a sharing problem.
- The rate limit is about 3 requests a second per integration. Since June 2026 there are also limits for the whole workspace, shared by all connections and scaled by plan. Pace bulk writes and wait for Retry-After.
- Rows are pages, so "delete" means moving the row to the trash (`"in_trash": true` on newer versions, `"archived": true` on older ones). Queries still return trashed rows unless you filter them out.
- The host to declare as the step's domain is api.notion.com.

## If company IT blocks it

If the workspace does not allow members to create integrations, the new-integration page refuses. Give the workspace owner this one line: please create an internal integration with read and insert on the database, connect it to that database, and share the token with me.

## Official docs

If a route fails, check here: https://developers.notion.com/reference/intro
