# The per-workflow record of real runs: successes guard against regressions, failures feed fixes
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
import time
import uuid
from typing import Any, Optional

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases(
  id INTEGER PRIMARY KEY,
  node_id TEXT NOT NULL,
  case_id TEXT NOT NULL,
  run_id TEXT,
  kind TEXT NOT NULL,
  ts REAL NOT NULL,
  input_hash TEXT NOT NULL,
  cls TEXT,
  cause TEXT,
  inputs TEXT NOT NULL,
  output TEXT,
  verdict TEXT,
  observed TEXT,
  meta TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_cases_node_kind ON cases(node_id, kind);
CREATE INDEX IF NOT EXISTS idx_cases_case ON cases(case_id);
CREATE INDEX IF NOT EXISTS idx_cases_cls ON cases(cls);
CREATE TABLE IF NOT EXISTS diagnoses(
  id INTEGER PRIMARY KEY,
  node_id TEXT NOT NULL,
  case_id TEXT NOT NULL,
  ts REAL NOT NULL,
  cause TEXT NOT NULL,
  cls TEXT,
  fix TEXT
) STRICT;
CREATE INDEX IF NOT EXISTS idx_diag_case ON diagnoses(case_id);
CREATE TABLE IF NOT EXISTS run_paths(
  id INTEGER PRIMARY KEY,
  workflow_id TEXT NOT NULL,
  run_id TEXT,
  ts REAL NOT NULL,
  path TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_runs_project ON run_paths(workflow_id);
CREATE TRIGGER IF NOT EXISTS cases_no_update BEFORE UPDATE ON cases
  BEGIN SELECT RAISE(ABORT, 'corpus is append-only'); END;
CREATE TRIGGER IF NOT EXISTS diagnoses_no_update BEFORE UPDATE ON diagnoses
  BEGIN SELECT RAISE(ABORT, 'corpus is append-only'); END;
CREATE TRIGGER IF NOT EXISTS run_paths_no_update BEFORE UPDATE ON run_paths
  BEGIN SELECT RAISE(ABORT, 'corpus is append-only'); END;
"""

def _db_path(workflow_id: str):
    return config.workflow_dir(workflow_id) / "corpus.db"

# A fresh connection per call; the schema is created on first open
def _conn(workflow_id: str) -> sqlite3.Connection:
    config.workflow_dir(workflow_id).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path(workflow_id)), timeout=10)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(_SCHEMA)
    _heal_schema(conn)
    return conn

# Aligns a pre-existing database with the current schema, since create-if-not-exists never alters an existing table
def _heal_schema(conn: sqlite3.Connection) -> None:
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cases)")}
        if "structure_hash" in cols:
            conn.execute("DROP INDEX IF EXISTS idx_cases_structure")
            conn.execute("ALTER TABLE cases DROP COLUMN structure_hash")
            conn.commit()
        if "corrected" in cols:
            conn.execute("ALTER TABLE cases DROP COLUMN corrected")
            conn.commit()

        cols = {r[1] for r in conn.execute("PRAGMA table_info(run_paths)")}
        if "project_id" in cols and "workflow_id" not in cols:
            conn.execute("ALTER TABLE run_paths RENAME COLUMN project_id TO workflow_id")
            conn.commit()
    except sqlite3.OperationalError:
        pass

SHAPE_DEPTH_CAP = 6
SHAPE_KEYS_CAP = 40
SHAPE_ITEMS_CAP = 20

# Keys and types only, never values, with recursion capped
def _shape(v: Any, depth: int = 0):
    if depth > SHAPE_DEPTH_CAP:
        return "deep"
    if isinstance(v, dict):
        return {k: _shape(v[k], depth + 1) for k in sorted(map(str, v))[:SHAPE_KEYS_CAP]}
    if isinstance(v, (list, tuple)):
        inner = sorted({json.dumps(_shape(x, depth + 1), sort_keys=True, default=str)
                        for x in list(v)[:SHAPE_ITEMS_CAP]})
        return ["list"] + inner
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if v is None:
        return "null"
    if isinstance(v, str):
        return "blob" if v.startswith("blob:") else "text"
    return type(v).__name__

def _input_hash(inputs: Any) -> str:
    return hashlib.sha1(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()

def _j(v: Any) -> str:
    return json.dumps(v, default=str)

# A fingerprint of the step as built, so run history counts only runs of the current version
def config_hash(node: dict) -> str:
    cfg = node.get("config") or {}
    code = "\n".join(ln.rstrip() for ln in
                     str(cfg.get("code") or "").strip().splitlines())
    model = cfg.get("model") or {}
    basis = json.dumps([code, str(cfg.get("prompt") or "").strip(),
                        model.get("provider"), model.get("model")],
                       sort_keys=True)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

# Saves a completed step's inputs and output, deduplicated by input, with secret values removed
def record_success(workflow_id: str, node_id: str, inputs: dict, output: Any,
                   run_id: Optional[str] = None, meta: Optional[dict] = None) -> None:
    h = _input_hash(inputs)
    with closing(_conn(workflow_id)) as conn, conn:
        dup = conn.execute("SELECT 1 FROM cases WHERE node_id=? AND kind='success' AND input_hash=? LIMIT 1", (node_id, h)).fetchone()
        if dup:
            return
        conn.execute(
            "INSERT INTO cases(node_id, case_id, run_id, kind, ts, input_hash, inputs, output, meta) VALUES(?,?,?,?,?,?,?,?,?)",
            (node_id, _id("case"), run_id, "success", time.time(), h,
             _j(inputs), _j(output), _j(meta or {})))

# Saves a failed case with its verdict; fixes are checked against these later
def record_failure(workflow_id: str, node_id: str, inputs: dict, run_id: Optional[str] = None,
                   verdict: Optional[dict] = None, observed: Optional[dict] = None,
                   cause: Optional[str] = None, cls: Optional[str] = None,
                   meta: Optional[dict] = None,
                   output: Any = None) -> str:
    case_id = _id("case")
    with closing(_conn(workflow_id)) as conn, conn:
        conn.execute(
            "INSERT INTO cases(node_id, case_id, run_id, kind, ts, input_hash, cls, cause, inputs, output, verdict, observed, meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (node_id, case_id, run_id, "failure", time.time(), _input_hash(inputs),
             cls, cause, _j(inputs),
             _j(output) if output is not None else None, _j(verdict or {}),
             _j(observed or {}), _j(meta or {})))
    return case_id

# A diagnosis is appended as its own row; the raw record is never mutated
def diagnose(workflow_id: str, node_id: str, case_id: str, cause: str,
             cls: Optional[str] = None, fix: Optional[str] = None) -> None:
    with closing(_conn(workflow_id)) as conn, conn:
        conn.execute("INSERT INTO diagnoses(node_id, case_id, ts, cause, cls, fix) VALUES(?,?,?,?,?,?)",
                     (node_id, case_id, time.time(), cause, cls, fix))

# How many runs finished, for the workflow card
def run_count(workflow_id: str) -> int:
    if not _db_path(workflow_id).exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{_db_path(workflow_id)}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return 0
    try:
        return int(conn.execute("SELECT count(*) FROM run_paths").fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()

# Which steps fired, in order, tied to the run
def record_run_path(workflow_id: str, path: list[str], run_id: Optional[str] = None) -> None:
    with closing(_conn(workflow_id)) as conn, conn:
        conn.execute("INSERT INTO run_paths(workflow_id, run_id, ts, path) VALUES(?,?,?,?)", (workflow_id, run_id, time.time(), _j(path)))

# The one sanctioned delete - temp ids only; real run history is append-only
def purge_node(workflow_id: str, node_id: str) -> None:
    if not node_id.startswith(("tmp_", "slice_")):
        raise ValueError("purge_node is for tmp_/slice_ ids only - the corpus is append-only")
    with closing(_conn(workflow_id)) as conn, conn:
        conn.execute("DELETE FROM cases WHERE node_id=?", (node_id,))
        conn.execute("DELETE FROM diagnoses WHERE node_id=?", (node_id,))

# Startup sweep for temp rows a dying process left behind; real rows never qualify
def sweep_tmp_rows() -> int:
    n = 0
    if not config.WORKFLOWS_DIR.is_dir():
        return 0
    where = ("WHERE node_id LIKE 'tmp\\_%' ESCAPE '\\' OR node_id LIKE 'slice\\_%' ESCAPE '\\'")
    for dbf in config.WORKFLOWS_DIR.glob("*/corpus.db"):
        pid = dbf.parent.name

        try:
            with closing(sqlite3.connect(f"file:{dbf}?mode=ro",
                                         uri=True, timeout=10)) as ro:
                leftovers = sum(
                    ro.execute(f"SELECT count(*) FROM {t} {where}").fetchone()[0]
                    for t in ("cases", "diagnoses"))
        except sqlite3.Error:
            leftovers = 0
        if not leftovers:
            continue
        try:
            with closing(_conn(pid)) as conn, conn:
                for tbl in ("cases", "diagnoses"):
                    n += conn.execute(f"DELETE FROM {tbl} {where}").rowcount
        except sqlite3.Error:
            continue
    return n

# Reads a step's recorded cases back
def cases(workflow_id: str, node_id: str, kind: str = "all") -> list[dict]:
    q = "SELECT * FROM cases WHERE node_id=?"
    args: list = [node_id]
    if kind in ("success", "failure"):
        q += " AND kind=?"
        args.append(kind)
    q += " ORDER BY ts, id"
    with closing(_conn(workflow_id)) as conn, conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(q, args).fetchall()]
        diag = {r["case_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM diagnoses WHERE node_id=? ORDER BY ts, id",
            (node_id,)).fetchall()}
    out = []
    for r in rows:
        c = {"kind": r["kind"], "case_id": r["case_id"], "run_id": r["run_id"],
             "ts": r["ts"], "input_hash": r["input_hash"],
             "inputs": json.loads(r["inputs"]), "meta": json.loads(r["meta"])}

        c["output"] = json.loads(r["output"]) if r["output"] else None
        if r["kind"] != "success":
            c["verdict"] = json.loads(r["verdict"]) if r["verdict"] else {}
            c["observed"] = json.loads(r["observed"]) if r["observed"] else {}
            c["cause"], c["cls"] = r["cause"], r["cls"]
        d = diag.get(r["case_id"])
        if d:
            c["cause"], c["cls"], c["fix"] = d["cause"], d["cls"], d["fix"]
        out.append(c)
    return out

# Light per-step tallies for the drawer; only the current version of a step counts
def node_counts(workflow_id: str, node_id: str,
                cfg_hash: Optional[str] = None) -> dict:
    def _same(meta_json: str) -> bool:
        if not cfg_hash:
            return True
        try:
            return (json.loads(meta_json or "{}") or {}).get(
                "config_hash") == cfg_hash
        except Exception:
            return False
    with closing(_conn(workflow_id)) as conn, conn:
        succ = sum(1 for (m,) in conn.execute(
            "SELECT meta FROM cases WHERE node_id=? AND kind='success'",
            (node_id,)).fetchall() if _same(m))
        fails = [
            {"cause": cause, "verdict_keys": list(json.loads(verdict or "{}").keys())}
            for cause, verdict, m in conn.execute(
                "SELECT cause, verdict, meta FROM cases WHERE node_id=? AND kind='failure'", (node_id,)).fetchall() if _same(m)]
    return {"successes": succ, "failures": fails}

# Bounded per-step overview - counts and failure classes, no payloads
def summary(workflow_id: str, node_ids: list[str]) -> dict:
    out: dict[str, dict] = {}
    if not node_ids:
        return out
    ph = ",".join("?" for _ in node_ids)
    with closing(_conn(workflow_id)) as conn, conn:
        for nid, kind, n, last in conn.execute(
                f"SELECT node_id, kind, COUNT(*), MAX(ts) FROM cases "
                f"WHERE node_id IN ({ph}) GROUP BY node_id, kind", node_ids):
            e = out.setdefault(nid, _empty_summary())
            e[kind + "es" if kind == "success" else "failures"] = n
            if kind == "failure":
                e["last_failure_ts"] = last
        for nid, cls, n in conn.execute(
                f"SELECT c.node_id, COALESCE(d.cls, c.cls) AS k, COUNT(*) FROM cases c "
                f"LEFT JOIN diagnoses d ON d.case_id = c.case_id "
                f"WHERE c.node_id IN ({ph}) AND c.kind='failure' "
                f"GROUP BY c.node_id, k", node_ids):
            e = out.setdefault(nid, _empty_summary())
            if cls:
                e["classes"][cls] = n
            else:
                e["undiagnosed"] = n
    return out

def _empty_summary() -> dict:
    return {"successes": 0, "failures": 0, "undiagnosed": 0, "classes": {},
            "last_failure_ts": None}

_QUERY_ROW_CAP = 200

# One read-only SELECT on a read-only connection, row- and time-capped
def query(workflow_id: str, sql: str, max_rows: int = _QUERY_ROW_CAP) -> dict:
    s = (sql or "").strip().rstrip(";").strip()
    if ";" in s:
        return {"error": "one statement only"}
    if not s.lower().startswith(("select", "with")):
        return {"error": "SELECT queries only - writes go through the run pipeline"}
    if not _db_path(workflow_id).exists():
        return {"columns": [], "rows": [], "note": "the corpus is empty (no runs yet)"}
    try:
        conn = sqlite3.connect(f"file:{_db_path(workflow_id)}?mode=ro", uri=True, timeout=10)
    except sqlite3.OperationalError as e:
        return {"error": str(e)}
    try:
        conn.execute("PRAGMA query_only=ON")
        started = time.time()
        conn.set_progress_handler(lambda: 1 if time.time() - started > 3 else 0, 50_000)
        cur = conn.execute(s)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(max(1, min(int(max_rows or _QUERY_ROW_CAP), _QUERY_ROW_CAP)))
        more = bool(cur.fetchone())
        return {"columns": cols, "rows": [list(r) for r in rows],
                **({"truncated": True} if more else {})}
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()

def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"
