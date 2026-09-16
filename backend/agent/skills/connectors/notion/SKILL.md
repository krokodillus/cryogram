---
name: notion
description: WHEN a workflow step needs to read or write rows in a Notion database - the internal-integration route, the connect-the-page step everyone forgets, exact property shapes and known gotchas
tier: 2
kind: action
depends_on: []
version: 1.2.0
last_updated: 2026-09-06
---

# Reading and writing Notion database rows

## The route: an internal integration token

An internal integration, not an OAuth or public one, which is the wrong branch for a personal tool. No admin is needed in most workspaces.

## User setup

Give it complete:

1. https://app.notion.com/developers/connections (notion.so/my-integrations is the legacy address and redirects), then a new integration: name it, pick the workspace, type Internal, and copy the secret (it starts with `ntn_`; older `secret_` tokens still work). Collect it through the masked ask, as a secret named for what it is (the Notion token).
2. Capabilities on the integration: Read content for reading, plus Insert and Update content when the step writes.
3. The step everyone forgets: open the target database in Notion, the `...` menu at the top right, Connections, add the integration. Without this every call returns 404 object_not_found, which looks like a wrong id but means the database is not shared with the integration. Connections cascade to child pages.
4. The database id comes from the URL, with the database open as a full page: an inline database must be opened through "Open as page" first, because copying the outer page's URL is the classic mistake. The id is the 32-character hex segment before `?v=`, and the `?v=` confirms it is a database URL.

## The request pattern

- Headers on every call: `Authorization: Bearer <token>`, `Notion-Version: 2026-03-11` and Content-Type application/json. The version header is required, and pinning it is what keeps a built workflow's behaviour from shifting: on this version "delete" is `in_trash` and list position is a position object; older pins such as 2025-09-03 still work but differ there.
- Resolve the data source once at setup: GET `https://api.notion.com/v1/databases/{database_id}` and take `data_sources[0].id`. Databases are containers of data sources since the 2025-09 API, and a normal database has exactly one. Cache it, so the per-run path is one call.
- Read rows: POST `/v1/data_sources/{data_source_id}/query` with optional `filter` and `sorts`; paginate with `start_cursor` and `page_size` (at most 100); the response is `{results, has_more, next_cursor}`.
- Append a row: POST `/v1/pages` with `{"parent": {"type": "data_source_id", "data_source_id": "..."}, "properties": {...}}`.
- Update a row: PATCH `/v1/pages/{page_id}` with `properties`.
- timeout=15; retry timeouts, connection errors, 5xx and 429 (a 429 carries Retry-After in seconds, so honour it); never retry any other 4xx.
- The success signal is a 200 on everything, create included. The error body is `{"object": "error", "code", "message"}`.

## Property shapes

Exact shapes matter; the usual 400s are shape mistakes.

- title, the required "Name" column, exactly one per database: `{"title": [{"type": "text", "text": {"content": "Row name"}}]}`.
- rich_text: the same array shape under `"rich_text"` (2,000 characters per request).
- select: `{"select": {"name": "Option"}}`. An unknown name silently creates the option, the opposite of Airtable, so validate against the known list first when the vocabulary is fixed.
- status: `{"status": {"name": "In progress"}}`, which must already exist.
- multi_select: `[{"name": "A"}, {"name": "B"}]`. number, checkbox, url and email are plain values. date: `{"date": {"start": "2026-07-27"}}`.
- Computed properties (formula, rollup, created and edited time or by, unique_id) are read-only; writing one gives a 400.
- Reading text back: join the array's `plain_text` fields.

## What goes wrong

- A 404 means not connected (step 3), or a page id where a database id belongs; say which by checking whether the URL had `?v=`.
- A 403 restricted_resource means a capability toggle is missing on the integration (step 2), not a sharing problem.
- The rate limit is about 3 requests a second per integration, and since June 2026 also workspace-level limits shared across all connections, scaled by plan: pace bulk writes and honour Retry-After.
- Rows are pages, so "delete" is moving to the trash (`"in_trash": true` on newer versions, `"archived": true` on older). Trashed rows still appear in queries unless filtered out.
- The domain is api.notion.com.

## If company IT blocks it

When the workspace disallows member integrations, the new-integration page refuses. The one-line ask for the workspace owner: please create an internal integration with read and insert on the database, connect it to that database, and share the token with me.

## Official docs

Check here if a route fails: https://developers.notion.com/reference/intro
