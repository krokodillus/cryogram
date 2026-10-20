---
name: database
description: WHEN a step reads or writes a SQL database: how to choose between a driver and Supabase's REST address, how to store the connection string, and how to tell a temporary failure from a wrong setting
tier: 2
kind: action
depends_on: []
version: 2.0.0
last_updated: 2026-10-13
---

# Reading and writing a SQL database

## Choose the route by where the database is

If the database is a SQLite file on the user's computer, use Python's built-in `sqlite3` module, with the file's folder declared on the step. If the database is hosted, use a connection the user has already set up for it when one fits. Choose between a driver (route 1) and Supabase's REST address (route 2) from the queries you need, the credentials the user has and the network access you have. Ask the user only for what you are missing.

## Route 1: the driver

Packages: for Postgres use `psycopg[binary]`, which comes ready to run and needs no compiler. For MySQL use `pymysql`, which is written in pure Python. Never use mysqlclient, because it needs a compiler and system header files.

Store one secret: the database's full connection string. Every provider's dashboard gives the user one address to copy. Separate secrets for host, user and password make setup three times longer and cause mistakes when the parts are put back together. Ask for the string with the masked ask (the card that hides what the user types), and store it as a secret with a name that says what it is, such as the database URL. `psycopg.connect(url)` accepts the string as it is. For PyMySQL, split the string in the step's code with `urllib.parse.urlsplit`.

Tell the user where to copy the string from:

- Supabase: in the dashboard, choose Connect at the top, copy the "Session pooler" string, and replace [YOUR-PASSWORD] with the real password. Use the session pooler on port 5432. The direct host `db.<ref>.supabase.co` works only over IPv6 unless the user pays for an add-on, and the transaction pooler on port 6543 breaks prepared statements. The pooler's username is `postgres.<workflow-ref>`, not `postgres`.
- Neon: in the dashboard, choose Connect and copy the string. It already contains the password and `sslmode=require`.
- RDS: get the endpoint and the port from the connection the user has already set up. The database may only be reachable through a network the user has approved. Never make a database public, and never widen its security groups to make a test pass: ask the user's IT department for an approved way in. A timeout does not tell you why the connection failed.
- PlanetScale: it is paid only, because the free tier ended in 2024. It supports MySQL and now Postgres. Go to the database, then Connect, then credentials. PlanetScale shows them once.

How to write the step's code:

- On every run, connect, do the work in one transaction and close the connection. `with psycopg.connect(url) as conn:` commits the transaction when the block ends normally and rolls it back on an error. Do not use a connection pool: the step runs for seconds.
- Write every query with placeholders. Both drivers use `%s`, so pass the values in a tuple. Never put values into the SQL text yourself.
- Keep the TLS and certificate checks the provider requires. If a verified connection already works, do not weaken it, and do not turn TLS off to find out why something fails. When you do not know which driver parameters the provider needs, find out the provider's actual parameters.
- When you add a row, record proof that it was added: use `RETURNING id` on Postgres, or read `cursor.rowcount`.
- The step's list of allowed hosts checks host names, and it lets ports 5432 and 3306 through just as it does 443. Put the host name in the connection string, never a raw IP address: an IP address skips the name lookup, and the step stays blocked.

## Route 2: Supabase's REST address, no driver, port 443

Use the project's API address from Supabase's documentation, and a key that is allowed to do this operation. Look in the current dashboard and documentation for which kind of key to use and which headers it needs, and do not rely on a retirement date you remember.

- To read, send GET to `/rest/v1/<table>?select=*&col=eq.<val>`. The headers depend on the kind of key. A key that starts with `sb_secret_` goes in the `apikey: <key>` header only: if you also send it as a Bearer token, Supabase answers "Invalid JWT". An older JWT key goes in both the `apikey` header and `Authorization: Bearer`.
- To add a row, send POST with a JSON body and the header `Prefer: return=representation`. Supabase answers 201 and returns the new row. Record that row as the proof.
- Follow the table's row-level security rules. An empty result, or a 401 or 403 answer, may be what the rules are meant to do, or may mean the request has no allowed identity. It is not a reason to ask the user for a key that skips the rules, such as the service-role key. Use a key with wider access only when the task explicitly requires it and the user has agreed to it.
- The host to declare is `<ref>.supabase.co`.

## Temporary failures and wrong settings are different

A request that only reads may be retried a few times with a limit when the failure is temporary and you have checked that it is temporary. A write or a commit that times out may still have gone through: find out whether it did, using the id of the operation, or use a transaction or a repeat-safe write that you know to be safe, before you send it again. Do not retry inserts on every REST 5xx or 429 answer.

These failures come from a wrong setting. Never retry them. Tell the user what to correct:

- "password authentication failed" or "Access denied": the credential is wrong. On Supabase, also check the pooler username.
- "SSL connection is required" or "client must use SSL/TLS": add the TLS settings.
- "permission denied for table": the user's database account needs the right grants.
- "relation does not exist": the table or schema name in the query is wrong, or the connection string points at the wrong database.
- A DNS failure: the host name is mistyped.

When the step's list of allowed hosts blocks a request, the error names the host to add. Report it to the user, and never treat it as a temporary failure.

## Official docs

If a request fails, check Supabase's documentation: https://supabase.com/docs/guides/database/connecting-to-postgres
