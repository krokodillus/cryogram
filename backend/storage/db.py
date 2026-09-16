# All structured app state is SQLite: one global app.db plus one workflow.db per workflow bundle
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from typing import Any, Optional

import config

_APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
  id INTEGER PRIMARY KEY CHECK(id = 1),
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS secret_values(
  owner TEXT NOT NULL,
  name TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY(owner, name)
);
CREATE TABLE IF NOT EXISTS environments(
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  ts REAL
);
CREATE TABLE IF NOT EXISTS runstate(
  run_id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  ts REAL
);
CREATE TABLE IF NOT EXISTS blob_meta(
  owner TEXT NOT NULL,
  hash TEXT NOT NULL,
  mime TEXT,
  size INTEGER,
  ts REAL,
  meta TEXT,
  PRIMARY KEY(owner, hash)
);
CREATE TABLE IF NOT EXISTS ai_usage(
  ts REAL NOT NULL,
  provider_id TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  workflow_id TEXT NOT NULL DEFAULT '',
  run_id TEXT NOT NULL DEFAULT '',
  node_id TEXT NOT NULL DEFAULT '',
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'run'
);
CREATE INDEX IF NOT EXISTS ai_usage_ts ON ai_usage(ts);
"""

_WORKFLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow(
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  ts REAL
);
CREATE TABLE IF NOT EXISTS versions(
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  hash TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL,
  number INTEGER
);
CREATE TABLE IF NOT EXISTS trail(
  id INTEGER PRIMARY KEY,
  turn_id TEXT NOT NULL,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trail_turn ON trail(turn_id);
"""

# Opens a database with its schema declared on open; there is no separate migration framework
def _open(path, schema: str, wal: bool = True) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    conn = sqlite3.connect(str(path), timeout=10)
    if fresh:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    conn.execute(f"PRAGMA journal_mode={'WAL' if wal else 'DELETE'}")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(schema)
    return conn

def app_conn() -> sqlite3.Connection:
    return _open(config.APP_DB, _APP_SCHEMA)

# The per-workflow database inside its bundle folder
def workflow_conn(workflow_id: str) -> sqlite3.Connection:
    conn = _open(config.workflow_db(workflow_id), _WORKFLOW_SCHEMA, wal=False)
    _heal_versions(conn)
    return conn

# A bundle from before versions were numbered gets the column and its rows numbered in order, on open
def _heal_versions(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(versions)")}
    if "number" in cols:
        return
    with conn:
        conn.execute("ALTER TABLE versions ADD COLUMN number INTEGER")
        conn.execute("UPDATE versions SET number = (SELECT COUNT(*) FROM versions v2 WHERE v2.id <= versions.id)")

def get_settings_row() -> Optional[dict]:
    with closing(app_conn()) as conn:
        row = conn.execute("SELECT data FROM settings WHERE id = 1").fetchone()
    return json.loads(row[0]) if row else None

def put_settings_row(data: dict) -> None:
    with closing(app_conn()) as conn, conn:
        conn.execute("INSERT INTO settings(id, data) VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                     (json.dumps(data),))

# Every secret row belongs to one owner (a workflow, an environment or the app); reads never cross owners
def secret_get(owner: str, name: str) -> Optional[str]:
    with closing(app_conn()) as conn:
        row = conn.execute("SELECT value FROM secret_values WHERE owner = ? AND name = ?",
                           (owner, name)).fetchone()
    return row[0] if row else None

def secret_set(owner: str, name: str, value: str) -> None:
    with closing(app_conn()) as conn, conn:
        conn.execute("INSERT INTO secret_values(owner, name, value) VALUES(?, ?, ?) ON CONFLICT(owner, name) DO UPDATE SET value = excluded.value",
                     (owner, name, value))

def secret_delete(owner: str, name: str) -> None:
    with closing(app_conn()) as conn, conn:
        conn.execute("DELETE FROM secret_values WHERE owner = ? AND name = ?",
                     (owner, name))

def secret_names(owner: str) -> list[str]:
    with closing(app_conn()) as conn:
        rows = conn.execute("SELECT name FROM secret_values WHERE owner = ? ORDER BY name",
                            (owner,)).fetchall()
    return [r[0] for r in rows]

# Every row across every owner - for the encrypt lift and the value scrubbers only
def secret_rows() -> list[tuple[str, str, str]]:
    with closing(app_conn()) as conn:
        rows = conn.execute("SELECT owner, name, value FROM secret_values ORDER BY owner, name").fetchall()
    return [(r[0], r[1], r[2]) for r in rows]

def env_get(env_id: str) -> Optional[dict]:
    with closing(app_conn()) as conn:
        row = conn.execute("SELECT data FROM environments WHERE id = ?", (env_id,)).fetchone()
    return json.loads(row[0]) if row else None

def env_put(env: dict) -> None:
    with closing(app_conn()) as conn, conn:
        conn.execute("INSERT INTO environments(id, data, ts) VALUES(?, ?, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data, ts = excluded.ts",
                     (env["id"], json.dumps(env), _now()))

def env_delete(env_id: str) -> None:
    with closing(app_conn()) as conn, conn:
        conn.execute("DELETE FROM environments WHERE id = ?", (env_id,))

def env_all() -> list[dict]:
    with closing(app_conn()) as conn:
        rows = conn.execute("SELECT data FROM environments").fetchall()
    return [json.loads(r[0]) for r in rows]

def runstate_get(run_id: str) -> Optional[dict]:
    with closing(app_conn()) as conn:
        row = conn.execute("SELECT data FROM runstate WHERE run_id = ?",
                           (run_id,)).fetchone()
    return json.loads(row[0]) if row else None

def runstate_put(rec: dict) -> None:
    with closing(app_conn()) as conn, conn:
        conn.execute("INSERT INTO runstate(run_id, data, ts) VALUES(?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET data = excluded.data, ts = excluded.ts",
                     (rec["run_id"], json.dumps(rec, default=str), _now()))

def runstate_delete(run_id: str) -> None:
    with closing(app_conn()) as conn, conn:
        conn.execute("DELETE FROM runstate WHERE run_id = ?", (run_id,))

def runstate_all() -> list[dict]:
    with closing(app_conn()) as conn:
        rows = conn.execute("SELECT data FROM runstate").fetchall()
    return [json.loads(r[0]) for r in rows]

# Blob metadata belongs to one owner per hash; the bytes stay one content-addressed file
def blob_meta_put(owner: str, hash_: str, mime: str, size: int,
                  meta: Optional[dict] = None) -> None:
    with closing(app_conn()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO blob_meta(owner, hash, mime, size, ts, meta) VALUES(?, ?, ?, ?, ?, ?)",
                     (owner, hash_, mime, size, _now(), json.dumps(meta or {})))

def _blob_row(row) -> Optional[dict]:
    if not row:
        return None
    return {"mime": row[0], "size": row[1], "ts": row[2],
            "meta": json.loads(row[3] or "{}")}

def blob_meta_get(owner: str, hash_: str) -> Optional[dict]:
    with closing(app_conn()) as conn:
        row = conn.execute("SELECT mime, size, ts, meta FROM blob_meta WHERE owner = ? AND hash = ?", (owner, hash_)).fetchone()
    return _blob_row(row)

# The oldest owner's row for a hash - for a reader with no owner context (a legacy download link)
def blob_meta_any(hash_: str) -> Optional[dict]:
    with closing(app_conn()) as conn:
        row = conn.execute("SELECT mime, size, ts, meta FROM blob_meta WHERE hash = ? ORDER BY ts LIMIT 1", (hash_,)).fetchone()
    return _blob_row(row)

# Drops one owner's claim on one blob and says whether anybody still holds it
def blob_meta_drop(owner: str, hash_: str) -> int:
    with closing(app_conn()) as conn, conn:
        conn.execute("DELETE FROM blob_meta WHERE owner = ? AND hash = ?",
                     (owner, hash_))
        row = conn.execute("SELECT COUNT(*) FROM blob_meta WHERE hash = ?",
                           (hash_,)).fetchone()
    return int(row[0] if row else 0)

def blob_meta_delete_owner(owner: str) -> int:
    with closing(app_conn()) as conn, conn:
        cur = conn.execute("DELETE FROM blob_meta WHERE owner = ?", (owner,))
    return cur.rowcount

# Every hash one owner holds a row for
def blob_meta_hashes(owner: str) -> list[str]:
    with closing(app_conn()) as conn:
        rows = conn.execute("SELECT hash FROM blob_meta WHERE owner = ?", (owner,)).fetchall()
    return [r[0] for r in rows]

# Every hash any owner still holds a row for
def blob_meta_known_hashes() -> set:
    with closing(app_conn()) as conn:
        rows = conn.execute("SELECT DISTINCT hash FROM blob_meta").fetchall()
    return {r[0] for r in rows}

def workflow_get(workflow_id: str) -> Optional[dict]:
    if not config.workflow_db(workflow_id).exists():
        return None
    with closing(workflow_conn(workflow_id)) as conn:
        row = conn.execute("SELECT data FROM workflow WHERE id = ?", (workflow_id,)).fetchone()
    return json.loads(row[0]) if row else None

def workflow_put(workflow: dict) -> None:
    with closing(workflow_conn(workflow["id"])) as conn, conn:
        conn.execute("INSERT INTO workflow(id, data, ts) VALUES(?, ?, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data, ts = excluded.ts",
                     (workflow["id"], json.dumps(workflow), _now()))

def _now() -> float:
    import time
    return time.time()

def workflow_versions(workflow_id: str) -> list[dict]:
    if not config.workflow_db(workflow_id).exists():
        return []
    with closing(workflow_conn(workflow_id)) as conn:
        rows = conn.execute("SELECT id, ts, hash, reason, number FROM versions ORDER BY id DESC").fetchall()
    return [{"id": r[0], "ts": r[1], "hash": r[2], "reason": r[3], "number": r[4]}
            for r in rows]

def workflow_version_get(workflow_id: str, version_id: int) -> Optional[dict]:
    if not config.workflow_db(workflow_id).exists():
        return None
    with closing(workflow_conn(workflow_id)) as conn:
        row = conn.execute("SELECT id, ts, hash, reason, data, number FROM versions WHERE id = ?", (int(version_id),)).fetchone()
    if not row:
        return None
    return {"id": row[0], "ts": row[1], "hash": row[2], "reason": row[3],
            "data": json.loads(row[4]), "number": row[5]}

# Appends one version, deduped against the newest row's hash
def workflow_version_add(workflow_id: str, hash_: str, reason: str,
                        data: dict, number: Optional[int] = None) -> Optional[int]:
    with closing(workflow_conn(workflow_id)) as conn, conn:
        row = conn.execute(
            "SELECT hash FROM versions ORDER BY id DESC LIMIT 1").fetchone()
        if row and row[0] == hash_:
            return None
        if number is None:
            number = (conn.execute("SELECT COALESCE(MAX(number), 0) FROM versions")
                      .fetchone()[0] or 0) + 1
        cur = conn.execute(
            "INSERT INTO versions(ts, hash, reason, data, number) VALUES(?, ?, ?, ?, ?)",
            (_now(), hash_, reason, json.dumps(data, default=str), int(number)))
        return cur.lastrowid

# The token ledger: one row per AI call, counts only, never money
def ai_usage_add(*, provider_id: str, model: str, workflow_id: str,
                 run_id: str, node_id: str, tokens_in: int, tokens_out: int,
                 source: str) -> None:
    with closing(app_conn()) as conn, conn:
        conn.execute(
            "INSERT INTO ai_usage(ts, provider_id, model, workflow_id, run_id, node_id, tokens_in, tokens_out, source) VALUES(?,?,?,?,?,?,?,?,?)",
            (_now(), provider_id or "", model or "", workflow_id or "",
             run_id or "", node_id or "", int(tokens_in or 0),
             int(tokens_out or 0), source or "run"))

# Token totals per provider since a timestamp - the provider cards' volume view
def ai_usage_by_provider(since_ts: float) -> dict[str, dict]:
    with closing(app_conn()) as conn:
        rows = conn.execute(
            "SELECT provider_id, SUM(tokens_in), SUM(tokens_out) FROM ai_usage WHERE ts >= ? AND provider_id != '' GROUP BY provider_id",
            (since_ts,)).fetchall()
    return {r[0]: {"in": int(r[1] or 0), "out": int(r[2] or 0)} for r in rows}

# Token totals per source for one workflow
def ai_usage_workflow(workflow_id: str) -> dict[str, dict]:
    with closing(app_conn()) as conn:
        rows = conn.execute(
            "SELECT source, SUM(tokens_in), SUM(tokens_out), COUNT(*) FROM ai_usage WHERE workflow_id = ? GROUP BY source",
            (workflow_id,)).fetchall()
    return {r[0]: {"in": int(r[1] or 0), "out": int(r[2] or 0),
                   "calls": int(r[3] or 0)} for r in rows}

# Appends one item of a builder turn's working trail
def trail_add(workflow_id: str, turn_id: str, kind: str, payload: dict) -> int:
    with closing(workflow_conn(workflow_id)) as conn:
        cur = conn.execute("INSERT INTO trail(turn_id, ts, kind, payload) VALUES (?,?,?,?)",
                           (turn_id, _now(), kind, json.dumps(payload, default=str)))
        conn.commit()
        return int(cur.lastrowid)

# Rewrites one trail item's payload, for the id stamped into a stored result
def trail_set_payload(workflow_id: str, item_id: int, payload: dict) -> None:
    with closing(workflow_conn(workflow_id)) as conn:
        conn.execute("UPDATE trail SET payload=? WHERE id=?",
                     (json.dumps(payload, default=str), int(item_id)))
        conn.commit()

# The turns in the trail, oldest first, each with its first item's time
def trail_turns(workflow_id: str) -> list[dict]:
    if not config.workflow_db(workflow_id).exists():
        return []
    with closing(workflow_conn(workflow_id)) as conn:
        rows = conn.execute("SELECT turn_id, MIN(ts) FROM trail GROUP BY turn_id ORDER BY MIN(id)").fetchall()
    return [{"turn_id": r[0], "ts": r[1]} for r in rows]

# Every item of one turn, in the order it happened
def trail_items(workflow_id: str, turn_id: str) -> list[dict]:
    if not config.workflow_db(workflow_id).exists():
        return []
    with closing(workflow_conn(workflow_id)) as conn:
        rows = conn.execute("SELECT id, ts, kind, payload FROM trail WHERE turn_id=? ORDER BY id", (turn_id,)).fetchall()
    return [{"id": r[0], "ts": r[1], "kind": r[2], **json.loads(r[3])} for r in rows]

# One trail item by its id, or None
def trail_item(workflow_id: str, item_id: int):
    if not config.workflow_db(workflow_id).exists():
        return None
    with closing(workflow_conn(workflow_id)) as conn:
        row = conn.execute("SELECT id, turn_id, ts, kind, payload FROM trail WHERE id=?",
                           (int(item_id),)).fetchone()
    if not row:
        return None
    return {"id": row[0], "turn_id": row[1], "ts": row[2], "kind": row[3], **json.loads(row[4])}

# Rewrites every stored item that carries a value, in both raw and JSON-escaped forms
def trail_redact(workflow_id: str, value: str, replacement: str) -> int:
    if not config.workflow_db(workflow_id).exists():
        return 0
    esc_v, esc_r = json.dumps(value)[1:-1], json.dumps(replacement)[1:-1]
    n = 0
    with closing(workflow_conn(workflow_id)) as conn:
        rows = conn.execute("SELECT id, payload FROM trail").fetchall()
        for rid, payload in rows:
            if value in payload or esc_v in payload:
                conn.execute("UPDATE trail SET payload=? WHERE id=?",
                             (payload.replace(esc_v, esc_r).replace(value, replacement), rid))
                n += 1
        conn.commit()
    return n
