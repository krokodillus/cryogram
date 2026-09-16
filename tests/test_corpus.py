# Tests: sQLite corpus invariants: shape hashing, dedup, diagnosis merge, append-only triggers, purge guard and the read-only SQL surface
from __future__ import annotations

import os
import unittest

from tests import _bootstrap
from tests._bootstrap import TMP as _TMP

from runtime import corpus

_PID = "p_corpus_test"

class CorpusTest(unittest.TestCase):
    def test_a_pre_rename_corpus_db_heals_its_run_paths_column(self):
        import sqlite3
        pid = "p_corpus_old_cols"
        path = corpus._db_path(pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.executescript(corpus._SCHEMA.replace("workflow_id TEXT NOT NULL", "project_id TEXT NOT NULL")
                           .replace("run_paths(workflow_id)", "run_paths(project_id)"))
        conn.execute("INSERT INTO run_paths(project_id, run_id, ts, path) VALUES(?,?,?,?)",
                     (pid, "r0", 1.0, "[]"))
        conn.commit(); conn.close()
        corpus.record_run_path(pid, ["n_a", "n_b"], run_id="r1")
        conn = sqlite3.connect(str(path))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(run_paths)")}
        rows = conn.execute("SELECT workflow_id, run_id FROM run_paths ORDER BY ts").fetchall()
        conn.close()
        self.assertIn("workflow_id", cols)
        self.assertNotIn("project_id", cols)
        self.assertEqual(rows, [(pid, "r0"), (pid, "r1")])

    def test_dedup_and_diagnosis_merge(self):
        corpus.record_success(_PID, "n_c", {"a": 1}, {"y": 2})
        corpus.record_success(_PID, "n_c", {"a": 1}, {"y": 2})
        self.assertEqual(len(corpus.cases(_PID, "n_c", "success")), 1)
        cid = corpus.record_failure(_PID, "n_c", {"a": "bad"}, verdict={"thrown": "X"})
        corpus.diagnose(_PID, "n_c", cid, "a was text", cls="type-mismatch")
        merged = corpus.cases(_PID, "n_c", "failure")[-1]
        self.assertEqual(merged["cls"], "type-mismatch")

    def test_append_only_and_purge_guard(self):
        import sqlite3
        with self.assertRaises(ValueError):
            corpus.purge_node(_PID, "n_c")
        corpus.record_success(_PID, "tmp_n_c", {"q": 1}, {"y": 1})
        corpus.purge_node(_PID, "tmp_n_c")
        self.assertEqual(corpus.cases(_PID, "tmp_n_c"), [])
        corpus.record_success(_PID, "n_keep", {"q": 1}, {"y": 1})
        conn = sqlite3.connect(os.path.join(_TMP, "workflows", _PID, "corpus.db"))
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE cases SET cls='hax'")
        finally:
            conn.close()

    def test_sweep_tmp_rows_clears_crash_leftovers_only(self):
        corpus.record_success(_PID, "tmp_crashed", {"q": 1}, {"y": 1})
        corpus.record_success(_PID, "n_real", {"q": 2}, {"y": 2})
        self.assertGreaterEqual(corpus.sweep_tmp_rows(), 1)
        self.assertEqual(corpus.cases(_PID, "tmp_crashed"), [])
        self.assertEqual(len(corpus.cases(_PID, "n_real", "success")), 1)

    def test_query_is_read_only_single_select(self):
        self.assertIn("error", corpus.query(_PID, "DELETE FROM cases"))
        self.assertIn("error", corpus.query(_PID, "SELECT 1; SELECT 2"))
        q = corpus.query(_PID, "SELECT COUNT(*) FROM cases")
        self.assertEqual(q["columns"], ["COUNT(*)"])

class SchemaSelfHealTest(unittest.TestCase):
    def test_a_pre_removal_db_heals_at_open(self):
        import sqlite3
        import config
        pid = "p_heal_test"
        config.workflow_dir(pid).mkdir(parents=True, exist_ok=True)
        import shutil
        self.addCleanup(shutil.rmtree, config.workflow_dir(pid), True)
        db = config.workflow_dir(pid) / "corpus.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
CREATE TABLE cases(
  id INTEGER PRIMARY KEY,
  node_id TEXT NOT NULL, case_id TEXT NOT NULL, run_id TEXT,
  kind TEXT NOT NULL, ts REAL NOT NULL, input_hash TEXT NOT NULL,
  structure_hash TEXT NOT NULL,
  cls TEXT, cause TEXT, inputs TEXT NOT NULL, output TEXT, verdict TEXT,
  observed TEXT, corrected TEXT, meta TEXT NOT NULL
) STRICT;
CREATE INDEX idx_cases_structure ON cases(structure_hash);
""")
        conn.execute(
            "INSERT INTO cases(node_id,case_id,run_id,kind,ts,input_hash,structure_hash,inputs,meta) VALUES('n1','c1','r1','success',1.0,'h','sh','{}','{}')")
        conn.commit()
        conn.close()

        corpus.record_success(pid, "n1", {"a": 1}, {"y": 2}, run_id="r2")
        rows = corpus.cases(pid, "n1", "all")
        self.assertEqual(len(rows), 2)
        self.assertTrue(any(c.get("case_id") == "c1" for c in rows))
        cols = {r[1] for r in sqlite3.connect(str(db)).execute(
            "PRAGMA table_info(cases)")}
        self.assertNotIn("structure_hash", cols)
