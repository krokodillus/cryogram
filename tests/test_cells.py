# Tests: the cell ledger's SQLite storage (cells.db): atomic appends, the drop-oldest cap, rename migration, and the blob offload round-trip
from __future__ import annotations

import unittest

from tests import _bootstrap

import config
from agent import cells

class CellsDbTest(unittest.TestCase):
    def test_record_appends_with_ts_and_latest_ok_picks_newest(self):
        pid = "p_cdb_basic"
        cells.record(pid, "Fetch rows", "v1", {"a": 1}, {"out": 1}, True, 0.1)
        cells.record(pid, "Fetch rows", "v2", {"a": 2}, {"out": 2}, True, 0.2)
        cells.record(pid, "Fetch rows", "v3", {"a": 3}, {"out": 3}, False, 0.3)
        runs = cells._load_raw(pid)
        self.assertEqual([r["code"] for r in runs], ["v1", "v2", "v3"])
        self.assertTrue(all(r["ts"] > 0 for r in runs))
        best = cells.latest_ok(pid, "Fetch rows")
        self.assertEqual(best["code"], "v2")
        self.assertTrue((config.workflow_dir(pid) / "cells.db").exists())

    def test_kind_filter_keeps_ai_out_of_code_fill(self):
        pid = "p_cdb_kind"
        cells.record(pid, "Judge posts", "the prompt", {}, {"v": 1}, True, 0.1,
                     kind="ai", model="m-1")
        self.assertIsNone(cells.latest_ok(pid, "Judge posts", kind="code"))
        got = cells.latest_ok(pid, "Judge posts", kind="ai")
        self.assertEqual(got["model"], "m-1")

    def test_a_step_id_outlives_the_name(self):
        pid = "p_cdb_ren"
        cells.record(pid, "Old name", "c", {}, {"v": 1}, True, 0.1)
        self.assertEqual(cells.claim(pid, "Old name", "node_abc"), 1)
        cells.record(pid, "Old name", "c", {}, {"v": 2}, True, 0.1, step_id="node_abc")
        got = cells.latest_ok(pid, "New name", step_id="node_abc")
        self.assertEqual(got["output"], {"v": 2})
        self.assertIsNone(cells.latest_ok(pid, "New name"))
        inv = cells.inventory(pid)
        self.assertEqual([(e["name"], e.get("id")) for e in inv], [("Old name", "node_abc")])

    def test_every_recorded_run_is_kept(self):
        pid = "p_cdb_cap"
        for i in range(5):
            cells.record(pid, f"s{i}", "c", {}, {"o": i}, True, 0.1)
        runs = cells._load_raw(pid)
        self.assertEqual([r["name"] for r in runs], ["s0", "s1", "s2", "s3", "s4"])

    def test_offload_round_trip(self):
        pid = "p_cdb_blob"
        big = {"rows": ["x" * 500 for _ in range(200)]}
        cells.record(pid, "Big step", "c", {}, big, True, 0.1)
        stored = cells._load_raw(pid)[-1]["output"]
        self.assertIn("$evidence_blob", stored)
        back = cells.latest_ok(pid, "Big step")["output"]
        self.assertEqual(back, big)

    def test_gone_blob_surfaces_the_loud_marker(self):
        pid = "p_cdb_gone"
        cells.record(pid, "Step b", "prompt", {},
                     {"$evidence_blob": "blob:" + "0" * 64, "sketch": "..."},
                     True, 0.2, [], kind="ai", model="m-2")

        self.assertIn("$evidence_blob", cells._load_raw(pid)[-1]["output"])

        got = cells.latest_ok(pid, "Step b", kind="ai")
        self.assertIn("$missing_evidence", got["output"])

    def test_missing_workflow_reads_empty_without_creating(self):
        self.assertEqual(cells._load_raw("p_cdb_nope"), [])
        self.assertFalse(config.workflow_dir("p_cdb_nope").exists())

class WhatTheWindowKeptTest(unittest.TestCase):
    def _recorded(self, pid, name="Probe the list"):
        from storage import blobstore
        owner = blobstore.workflow_owner(pid)
        har = blobstore.put(b'{"log": {"entries": []}}', owner=owner)
        page = blobstore.put(b"<html><a href='/thread/1'>one</a></html>",
                             owner=owner)
        cells.record(pid, name, "code", {},
                     {"rows": [1, 2],
                      "$capture": {"har": har, "snapshot": page,
                                   "pages": [{"url": "u", "ref": page}],
                                   "calls": [{"url": "u", "kind": "xhr"}],
                                   "calls_total": 1}},
                     True, 0.1)
        return har, page

    def test_the_recording_and_the_page_can_be_chained_by_name(self):
        pid = "p_cap_chain"
        har, page = self._recorded(pid)
        pool = cells.latest_outputs(pid)
        self.assertEqual(pool["har"], har)
        self.assertEqual(pool["page"], page)
        self.assertEqual(pool["rows"], [1, 2])
        self.assertNotIn("$capture", pool)

    def test_the_inventory_says_what_the_window_kept(self):
        pid = "p_cap_inv"
        self._recorded(pid)
        entry = cells.inventory(pid)[0]
        self.assertEqual(entry["outputs"], ["rows"])
        self.assertEqual(entry["captured"], ["har", "page"])

    def test_a_window_that_kept_only_the_page_is_listed_with_no_outputs(self):
        from storage import blobstore
        pid = "p_cap_bare"
        owner = blobstore.workflow_owner(pid)
        page = blobstore.put(b"<html></html>", owner=owner)
        cells.record(pid, "Look", "code", {}, {"$capture": {"snapshot": page}},
                     True, 0.1)
        self.assertEqual(cells.inventory(pid)[0]["captured"], ["page"])

    def test_the_build_lets_go_of_the_bytes_and_keeps_every_run(self):
        from storage import blobstore
        pid = "p_cap_forget"
        har, page = self._recorded(pid)
        self.assertEqual(cells.forget_captures(pid), 2)
        self.assertFalse(blobstore.exists(har))
        self.assertFalse(blobstore.exists(page))

        runs = cells._load_raw(pid)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["output"]["rows"], [1, 2])

        self.assertNotIn("har", cells.latest_outputs(pid))
        self.assertEqual(cells.forget_captures(pid), 0)

    def test_bytes_another_workflow_still_holds_are_not_removed(self):
        from storage import blobstore
        mine, theirs = "p_cap_mine", "p_cap_theirs"
        shared = b"<html>the same bytes</html>"
        ref = blobstore.put(shared, owner=blobstore.workflow_owner(mine))
        blobstore.put(shared, owner=blobstore.workflow_owner(theirs))
        cells.record(mine, "Look", "code", {},
                     {"$capture": {"snapshot": ref}}, True, 0.1)
        self.assertEqual(cells.forget_captures(mine), 0)
        self.assertTrue(blobstore.exists(ref))
        self.assertEqual(blobstore.get(ref), shared)

if __name__ == "__main__":
    unittest.main()

class BlobsGoWithTheirOwnerTest(unittest.TestCase):
    def test_forgetting_an_owner_removes_the_files_it_alone_held(self):
        from storage import blobstore
        mine, theirs = "p_own_mine", "p_own_theirs"
        only_mine = blobstore.put(b"mine alone", owner=blobstore.workflow_owner(mine))
        shared = blobstore.put(b"shared bytes", owner=blobstore.workflow_owner(mine))
        blobstore.put(b"shared bytes", owner=blobstore.workflow_owner(theirs))
        self.assertEqual(blobstore.forget_owner(blobstore.workflow_owner(mine)), 1)
        self.assertFalse(blobstore.exists(only_mine))
        self.assertTrue(blobstore.exists(shared))
        self.assertEqual(blobstore.get(shared), b"shared bytes")

    def test_the_sweep_removes_files_no_owner_holds(self):
        import config
        from storage import blobstore
        held = blobstore.put(b"held by someone", owner=blobstore.workflow_owner("p_sweep"))
        orphan = config.BLOBS_DIR / ("f" * 64)
        orphan.write_bytes(b"left behind")
        (config.BLOBS_DIR / "not-a-blob.txt").write_text("ignored")
        self.assertEqual(blobstore.sweep_orphans(), 1)
        self.assertFalse(orphan.exists())
        self.assertTrue(blobstore.exists(held))
        self.assertTrue((config.BLOBS_DIR / "not-a-blob.txt").exists())

    def test_only_the_newest_windows_recordings_are_kept(self):
        from storage import blobstore
        pid = "p_trim"
        owner = blobstore.workflow_owner(pid)
        refs = []
        for i in range(cells.CAPTURES_KEPT + 2):
            har = blobstore.put(f"traffic {i}".encode(), owner=owner)
            page = blobstore.put(f"<html>{i}</html>".encode(), owner=owner)
            refs.append((har, page))
            cells.record(pid, f"Look {i}", "code", {},
                         {"$capture": {"har": har, "snapshot": page}}, True, 0.1)

        for har, page in refs[:2]:
            self.assertFalse(blobstore.exists(har))
            self.assertFalse(blobstore.exists(page))
        for har, page in refs[2:]:
            self.assertTrue(blobstore.exists(har))

        self.assertEqual(len(cells._load_raw(pid)), cells.CAPTURES_KEPT + 2)
