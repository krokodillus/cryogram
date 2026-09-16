---
name: database
description: WHEN a workflow step needs to read or write rows in a SQL database the user already has (Postgres, MySQL, SQLite; Supabase/Neon/RDS/PlanetScale) - driver vs REST routes, connection-string handling, transient-vs-config errors
tier: 2
kind: action
depends_on: []
version: 1.2.0
last_updated: 2026-09-06
---

# Reading and writing a SQL database

## The route follows one question: where does the database live?

A SQLite file: the stdlib `sqlite3`, no credential, no egress, done. Supabase: the REST route by default, the driver only for joins or aggregates the REST filters cannot express. Anything else (Neon, RDS, PlanetScale, self-hosted Postgres or MySQL): the driver route.

## Route 1: the driver

Packages: Postgres uses `psycopg[binary]` (self-contained wheels, no compiler); MySQL uses `pymysql` (pure Python), never mysqlclient, which needs a compiler and headers.

One secret: the full connection string. Every provider's dashboard hands the user exactly one copyable URL, while separate host, user and password secrets triple the setup and breed recombination bugs. Collect it through the masked ask, as a secret named for what it is (the database URL). `psycopg.connect(url)` takes it directly; for PyMySQL parse it with `urllib.parse.urlsplit` in the step code.

Where the user copies it:

- Supabase: dashboard, Connect at the top, the "Session pooler" string, then replace [YOUR-PASSWORD]. Use the session pooler on port 5432: the direct `db.<ref>.supabase.co` host is IPv6-only without a paid add-on, and the 6543 transaction pooler breaks prepared statements. The pooler username is `postgres.<workflow-ref>`, not `postgres`.
- Neon: dashboard, Connect, copy; the string already carries the password and `sslmode=require`.
- RDS: console, Databases, the instance, Connectivity & security, Endpoint and Port. It needs Public accessibility set to Yes and a security-group inbound rule, which is genuinely IT territory; a persistent connect timeout on RDS means that configuration, not the network.
- PlanetScale (paid only, the free tier ended in 2024; MySQL and now Postgres too): database, Connect, credentials, shown once.

The pattern:

- Connect, one transaction, close, per run (`with psycopg.connect(url) as conn:` commits or rolls back). No pooling; the step lives seconds.
- Parameterised SQL only: `%s` placeholders and a params tuple (both drivers use `%s`). Never format values into the SQL string.
- TLS: always append `sslmode=require` for hosted Postgres; hosted MySQL needs `ssl_ca=certifi.where()` (PlanetScale refuses plaintext). A LAN MySQL may need TLS off, the one setup fork to ask about.
- Append proof: `RETURNING id` on Postgres, or `cursor.rowcount`, the recordable success shape.
- The allowlist gates by hostname (5432 and 3306 pass like 443), so use the hostname in the string, never a raw IP: an IP bypasses the resolver and stays blocked.

## Route 2: Supabase REST, no driver, port 443

Workflow Settings, API Keys, the secret key (`sb_secret_...`, or the legacy service_role; legacy JWT keys still work but retire at the end of 2026) plus the workflow URL `https://<ref>.supabase.co`.

- Read: GET `/rest/v1/<table>?select=*&col=eq.<val>`. The headers depend on the key kind: an `sb_secret_` key goes in `apikey: <key>` only (sent as a Bearer it fails with "Invalid JWT"); a legacy JWT key goes in both `apikey` and `Authorization: Bearer`.
- Append: POST JSON with `Prefer: return=representation`, which gives a 201 and the created row, the proof to record.
- Use the secret key for a backend workflow: the publishable or anon key hits row-level security and reads as empty results or a 401, a silent failure that looks like no data.
- The domain is `<ref>.supabase.co`.

## Transient against configuration, never mixed up

Transient, retried once or twice and then surfaced: a connect timeout right after idle on Neon (a scale-to-zero cold start); "too many connections" or connection-slot errors; REST 5xx and 429.

Configuration, never retried, the fix named instead: password authentication failed or Access denied (a wrong credential; on Supabase check the pooler username); "SSL connection is required" or "client must use SSL/TLS" (add the TLS bits); "permission denied for table" (grants); "relation does not exist" (a wrong table, schema or database segment); a DNS failure (a mistyped host). An egress block names the host to add and is never swallowed as transient.

## Official docs

Check here if a route fails: https://supabase.com/docs/guides/database/connecting-to-postgres
