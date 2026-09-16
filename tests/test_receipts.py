# Tests: the deterministic build core: the assembler (freeze + wire from evidence, pure code), the receipts (connector rehearsal through the run's own seams with the send intercepted; ai request assembly; the seam walk over recorded values), the sample ledger view, and the cells blob-offload for large recorded values
from __future__ import annotations

import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from agent import assembler
from agent import cells
from agent import evidence
import step_types
from agent import receipts
from storage import store

class RehearsalTest(unittest.TestCase):
    _OLD = "def send(message):\n    return 'superseded version'\n"

    def _connector(self, code):
        return {"id": "n_send", "name": "Send it", "type": "connector",
                "read_only": False, "config": {"code": code},
                "inputs": [{"name": "message", "type": "text"}],
                "outputs": [{"name": "posted", "type": "boolean"}],
                "tests": []}

    def test_matching_recorded_run_skips_execution(self):
        code = "def send(message):\n    raise RuntimeError('must not run')\n"
        node = self._connector(code)
        p = _workflow([node], pid="p_rehearse0")
        cells.record("p_rehearse0", "Send it", code, {"message": "hi"},
                     {"posted": True}, True, 0.1, [])
        plan = {"nodes": [{"name": "Send it", "type": "connector"}]}
        r = receipts.check(p, plan)
        self.assertTrue(r["ok"], r)

        node["config"]["code"] = code.replace("\n", "  \n", 1)
        self.assertTrue(receipts.check(p, plan)["ok"])

    def test_reaching_the_send_passes_and_nothing_fires(self):
        from storage import secrets_store
        secrets_store.set_secret("hook_r1", "https://x.example/h", secrets_store.workflow_owner("p_rehearse1"))
        code = ("def send(message):\n"
                "    import urllib.request\n"
                "    url = get_secret('hook_r1')\n"
                "    urllib.request.urlopen(url, data=message.encode(), timeout=5)\n"
                "    return True\n")
        node = self._connector(code)
        p = _workflow([node], pid="p_rehearse1")
        cells.record("p_rehearse1", "Send it", self._OLD, {"message": "hi"},
                     {"posted": True}, True, 0.1, [])
        plan = {"nodes": [{"name": "Send it", "type": "connector"}]}
        r = receipts.check(p, plan)
        self.assertTrue(r["ok"], r)

    def test_broken_code_no_longer_blocks_the_build_but_is_noted(self):
        code = ("def send(message):\n"
                "    boom = {}['missing-key']\n"
                "    return True\n")
        node = self._connector(code)
        p = _workflow([node], pid="p_rehearse2")
        cells.record("p_rehearse2", "Send it", self._OLD, {"message": "hi"},
                     {"posted": True}, True, 0.1, [])
        plan = {"nodes": [{"name": "Send it", "type": "connector"}]}
        r = receipts.check(p, plan)
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("changed since its trial run" in n
                            for n in r["notes"]), r["notes"])

    def test_a_build_never_executes_step_code(self):
        code = ("def send(message):\n"
                "    raise RuntimeError('a build must never run me')\n")
        node = self._connector(code)
        p = _workflow([node], pid="p_rehearse_noexec")
        cells.record("p_rehearse_noexec", "Send it", self._OLD,
                     {"message": "hi"}, {"posted": True}, True, 0.1, [])
        plan = {"nodes": [{"name": "Send it", "type": "connector"}]}
        self.assertTrue(receipts.check(p, plan)["ok"])

    def test_unset_secret_gets_a_placeholder_never_a_block(self):
        code = ("def send(message):\n"
                "    import urllib.request\n"
                "    url = get_secret('hook_never_set_r3')\n"
                "    urllib.request.urlopen('https://x.example/' + url, timeout=5)\n"
                "    return True\n")
        node = self._connector(code)
        p = _workflow([node], pid="p_rehearse3")
        cells.record("p_rehearse3", "Send it", self._OLD, {"message": "hi"},
                     {"posted": True}, True, 0.1, [])
        plan = {"nodes": [{"name": "Send it", "type": "connector"}]}
        r = receipts.check(p, plan)
        self.assertTrue(r["ok"], r)

class AiRequestReceiptTest(unittest.TestCase):
    def test_blank_model_is_a_note_not_a_finding(self):
        node = {"id": "n_ai", "name": "Judge it", "type": "ai",
                "config": {"prompt": "p", "model": {"model": "", "temperature": 0}},
                "inputs": [], "outputs": [{"name": "out", "type": "text"}],
                "tests": []}
        p = _workflow([node], pid="p_aireq")
        r = receipts.check(p, {"nodes": [{"name": "Judge it", "type": "ai"}]})
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("no AI model set up yet" in n for n in r["notes"]),
                        r["notes"])

class SeamWalkTest(unittest.TestCase):
    def test_recorded_output_must_feed_the_wired_input(self):
        a = {"id": "n_a", "name": "Scan", "type": "code",
             "config": {"code": "x=1"}, "inputs": [],
             "outputs": [{"name": "networks", "type": "list"}], "tests": []}
        b = {"id": "n_b", "name": "Pick", "type": "code",
             "config": {"code": "x=1"},
             "inputs": [{"name": "networks", "type": "list"}],
             "outputs": [], "tests": []}
        p = _workflow([a, b], pid="p_seam1")
        cells.record("p_seam1", "Scan", "c", {}, {"wrong_name": [1]}, True, 0.1, [])
        plan = {"nodes": [{"name": "Scan"}, {"name": "Pick"}],
                "edges": [{"src": "Scan", "dst": "Pick"}]}
        r = receipts.check(p, plan)
        self.assertFalse(r["ok"])
        self.assertIn('no "networks"', r["findings"][0])

    def test_when_condition_must_evaluate_on_the_recorded_output(self):
        a = {"id": "n_a", "name": "Scan", "type": "code",
             "config": {"code": "x=1"}, "inputs": [],
             "outputs": [{"name": "kind", "type": "text"}], "tests": []}
        b = {"id": "n_b", "name": "Route", "type": "code",
             "config": {"code": "x=1"},
             "inputs": [{"name": "kind", "type": "text"}],
             "outputs": [], "tests": []}
        p = _workflow([a, b], pid="p_seam2")
        cells.record("p_seam2", "Scan", "c", {}, {"kind": "A"}, True, 0.1, [])
        plan = {"nodes": [{"name": "Scan"}, {"name": "Route"}],
                "edges": [{"src": "Scan", "dst": "Route",
                           "when": "missing_field == 'A'"}]}
        r = receipts.check(p, plan)
        self.assertFalse(r["ok"])
        self.assertIn("routing condition", r["findings"][0])

        plan["edges"][0]["when"] = "kind == 'A'"
        self.assertTrue(receipts.check(p, plan)["ok"])

class SettingsFitTest(unittest.TestCase):
    def _p(self, pid, value):
        node = {"id": "n_f", "name": "Fetch", "type": "code",
                "config": {"code": "x=1"},
                "inputs": [{"name": "posts_per_scan", "type": "number",
                            "label": "Posts per scan"}],
                "outputs": [{"name": "posts", "type": "list"}], "tests": []}
        p = _workflow([node], pid=pid)
        p["variables"] = [{"name": "posts_per_scan", "value": value,
                           "secret": False, "persistent": True}]
        return p, {"nodes": [{"name": "Fetch"}], "edges": []}

    def test_a_setting_the_step_cannot_accept_refuses_the_build(self):
        p, plan = self._p("p_fit1", "lots")
        r = receipts.check(p, plan)
        self.assertFalse(r["ok"])
        self.assertIn("Posts per scan", r["findings"][0])
        self.assertIn("number", r["findings"][0])
        self.assertIn("Inputs tab", r["findings"][0])

    def test_a_legacy_text_value_is_read_in_its_type_not_refused(self):
        p, plan = self._p("p_fit1b", "100")
        self.assertTrue(receipts.check(p, plan)["ok"])
        from storage import environments
        self.assertEqual(environments.resolve(p)["posts_per_scan"]["value"], 100)

    def test_a_correctly_typed_setting_passes(self):
        p, plan = self._p("p_fit2", 100)
        self.assertTrue(receipts.check(p, plan)["ok"])

    def test_unset_is_not_a_defect(self):
        p, plan = self._p("p_fit3", "")
        self.assertTrue(receipts.check(p, plan)["ok"])

    def test_a_secret_is_never_type_checked_here(self):
        p, plan = self._p("p_fit4", "")
        p["variables"] = [{"name": "posts_per_scan", "value": None,
                           "secret": True, "persistent": True}]
        self.assertTrue(receipts.check(p, plan)["ok"])

class AssemblerTest(unittest.TestCase):
    def test_proven_plan_assembles_without_a_model(self):
        p = _workflow([], pid="p_asm1")
        code = "def scan():\n    return ['a', 'b']\n"
        cells.record("p_asm1", "Scan networks", code, {},
                     {"networks": ["a", "b"]}, True, 0.1, [])
        plan = {"summary": "s", "status": "draft",
                "nodes": [{"name": "Scan networks", "type": "code",
                           "code": code, "inputs": [],
                           "outputs": [{"name": "networks", "type": "list"}],
                           "tests": [{"name": "recorded run", "inputs": {},
                                      "expect": "ok"}]}],
                "edges": []}
        p["plan"] = plan
        store.save(p)
        events = []
        errors = assembler.assemble(p, plan, events.append)
        self.assertEqual(errors, [], errors)
        self.assertEqual(len(p["nodes"]), 1)
        self.assertEqual(p["nodes"][0]["name"], "Scan networks")
        kinds = [e["type"] for e in events]
        self.assertIn("phase", kinds)
        self.assertIn("node-built", kinds)

    def test_missing_evidence_is_a_named_refusal_not_authorship(self):
        p = _workflow([], pid="p_asm2")
        plan = {"summary": "s", "status": "draft",
                "nodes": [{"name": "Ghost step", "type": "code",
                           "inputs": [], "outputs": []}], "edges": []}
        p["plan"] = plan
        store.save(p)
        errors = assembler.assemble(p, plan, lambda ev: None)
        self.assertTrue(errors)
        self.assertIn("Ghost step", errors[0])

    def _three(self, pid):
        p = _workflow([], pid=pid)
        steps = {}
        for nm, out in (("A", "x"), ("B", "y"), ("C", "z")):
            code = f"write_output('{out}', 1)\n"
            cells.record(pid, nm, code, {}, {out: 1}, True, 0.1, [])
            steps[nm] = {"name": nm, "type": "code", "code": code, "inputs": [],
                         "outputs": [{"name": out, "type": "number"}],
                         "tests": [{"name": "recorded run", "inputs": {},
                                    "expect": "ok"}]}
        return p, steps

    def _edges(self, p):
        names = {n["id"]: n["name"] for n in p["nodes"]}
        return sorted((names[e["src"]], names[e["dst"]], e.get("when") or "")
                      for e in p["edges"])

    def test_the_plans_edges_are_the_order(self):
        p, st = self._three("p_asm_edges")
        plan1 = {"summary": "s", "nodes": [st["A"], st["C"]],
                 "edges": [{"src": "A", "dst": "C"}]}
        p["plan"] = plan1
        store.save(p)
        self.assertEqual(assembler.assemble(p, plan1, lambda ev: None), [])
        self.assertEqual(self._edges(p), [("A", "C", "")])
        plan2 = {"summary": "s", "nodes": [st["A"], st["B"], st["C"]],
                 "edges": [{"src": "A", "dst": "B"}, {"src": "B", "dst": "C"}]}
        p["plan"] = plan2
        events = []
        self.assertEqual(assembler.assemble(p, plan2, events.append), [])
        self.assertEqual(self._edges(p), [("A", "B", ""), ("B", "C", "")])
        self.assertTrue(any("no longer in the plan" in (e.get("text") or "")
                            for e in events))

        plan3 = {"summary": "s", "nodes": [st["A"], st["B"], st["C"]],
                 "edges": [{"src": "A", "dst": "B", "when": "x == 1"},
                           {"src": "B", "dst": "C"}]}
        p["plan"] = plan3
        self.assertEqual(assembler.assemble(p, plan3, lambda ev: None), [])
        self.assertEqual(self._edges(p), [("A", "B", "x == 1"), ("B", "C", "")])

    def test_an_edgeless_plan_leaves_edges_alone(self):
        p, st = self._three("p_asm_noedge")
        plan1 = {"summary": "s", "nodes": [st["A"], st["C"]],
                 "edges": [{"src": "A", "dst": "C"}]}
        p["plan"] = plan1
        store.save(p)
        assembler.assemble(p, plan1, lambda ev: None)
        plan2 = {"summary": "s", "nodes": [st["A"], st["C"]], "edges": []}
        p["plan"] = plan2
        self.assertEqual(assembler.assemble(p, plan2, lambda ev: None), [])
        self.assertEqual(self._edges(p), [("A", "C", "")])

class CellsOffloadTest(unittest.TestCase):
    def test_big_output_offloads_and_dereferences(self):
        big = {"rows": ["x" * 100 for _ in range(2000)]}
        cells.record("p_blob1", "Fetch", "c", {}, big, True, 0.1, [])
        raw = cells._load_raw("p_blob1")
        self.assertIn("$evidence_blob", raw[-1]["output"])
        self.assertIn("sketch", raw[-1]["output"])
        back = cells.latest_ok("p_blob1", "Fetch")
        self.assertEqual(back["output"], big)

        self.assertLess(cells._db_path("p_blob1").stat().st_size, 20_000)

    def test_small_values_stay_inline(self):
        cells.record("p_blob2", "Small", "c", {}, {"v": 1}, True, 0.1, [])
        raw = cells._load_raw("p_blob2")
        self.assertEqual(raw[-1]["output"], {"v": 1})

class EvidenceLedgerTest(unittest.TestCase):
    def test_structural_summary_never_carries_values(self):
        v = {"customer": "Ola Nordmann", "amount": 1234,
             "lines": [{"sku": "A-1", "qty": 2}]}
        s = evidence.structural_summary(v, error_class="bad_sku",
                                        failed_check="sku format")
        flat = str(s)
        self.assertNotIn("Ola", flat)
        self.assertNotIn("A-1", flat)
        self.assertEqual(s["redaction"], "structural")
        self.assertEqual(s["error_class"], "bad_sku")
        self.assertEqual(s["shape"]["customer"], "text(12)")

    def test_step_contract_names_neighbours(self):
        a = {"id": "n_up", "name": "Fetch", "type": "code",
             "config": {"code": "c"}, "inputs": [],
             "outputs": [{"name": "rows", "type": "list"}], "tests": []}
        b = {"id": "n_mid", "name": "Shape", "type": "code",
             "config": {"code": "c"},
             "inputs": [{"name": "rows", "type": "list"}],
             "outputs": [{"name": "message", "type": "text"}], "tests": []}
        c = {"id": "n_dn", "name": "Send", "type": "connector",
             "config": {"code": "m = read_input('message')\nx = m['title']"},
             "inputs": [{"name": "message", "type": "text"}],
             "outputs": [], "tests": []}
        p = _workflow([a, b, c], pid="p_evi2")
        p["edges"] = [{"src": "n_up", "dst": "n_mid", "when": ""},
                      {"src": "n_mid", "dst": "n_dn", "when": ""}]
        cells.record("p_evi2", "Fetch", "c", {}, {"rows": [1, 2]}, True, 0.1, [])
        contract = evidence.step_contract(p, b)
        self.assertIn("UPSTREAM \"Fetch\"", contract)
        self.assertIn("DOWNSTREAM \"Send\"", contract)
        self.assertIn("title", contract)

class TypeContractTest(unittest.TestCase):
    def test_every_node_type_declares_a_contract(self):
        for t in ("user-input", "code", "connector", "browser", "ai"):
            self.assertIn(t, step_types.TYPES)
            self.assertIn("receipt", step_types.contract(t))

class StaleEvidenceTest(unittest.TestCase):
    def test_changed_code_makes_a_step_unproven_again(self):
        from agent import node_tools
        p = _workflow([], pid="p_stale1")
        cells.record("p_stale1", "Parse it", "def f():\n    return 1\n",
                     {}, {"v": 1}, True, 0.1, [])
        fresh = [{"name": "Parse it", "type": "code",
                  "code": "def f():\n    return 1\n"}]
        self.assertEqual(node_tools.untested_steps(p, fresh), [])
        edited = [{"name": "Parse it", "type": "code",
                   "code": "def f():\n    return 2  # edited, never re-proven\n"}]
        out = node_tools.untested_steps(p, edited)
        self.assertEqual(len(out), 1)
        self.assertIn(node_tools.CHANGED_MARK.strip(), out[0])

    def test_tested_on_current_text_or_declined_is_the_bar(self):
        from agent import node_tools
        p = _workflow([], pid="p_connstale")
        cells.record("p_connstale", "Post it", "send_v1(read_input('x'))",
                     {}, {"posted": True}, True, 0.1, [])
        revised = [{"name": "Post it", "type": "connector",
                    "external_impact": "posts a message",
                    "code": "send_v2(read_input('people'))"}]
        out = node_tools.untested_steps(p, revised)
        self.assertEqual(len(out), 1)
        self.assertIn(node_tools.CHANGED_MARK.strip(), out[0])

        minor = [dict(revised[0], minor_revision="only the flags changed")]
        self.assertEqual(node_tools.untested_steps(p, minor), [])
        never = [{"name": "Never sent", "type": "connector",
                  "external_impact": "posts",
                  "code": "x = 1", "minor_revision": "tiny"}]
        self.assertEqual(node_tools.untested_steps(p, never), ["Never sent"])

        p["nodes"] = [{"id": "n_post", "name": "Post it", "type": "connector",
                       "config": {"code": "old"}, "inputs": [],
                       "outputs": [], "tests": []}]
        p["tickets"] = [{"id": "tkt_min1", "node_id": "n_post",
                         "status": "open", "reason": "node threw"}]
        p["plan"] = {"ticket_id": "tkt_min1"}
        out = node_tools.untested_steps(p, minor)
        self.assertEqual(len(out), 1)
        self.assertIn(node_tools.CHANGED_MARK.strip(), out[0])

    def test_the_plan_lag_heals_to_the_newest_recording(self):
        from agent import node_tools
        old = "write_output('logged_in', probe_v1())"
        new = "write_output('logged_in', probe_v2())"
        cells.record("p_lag1", "Check login", old, {}, {"logged_in": True},
                     True, 0.1, [])
        cells.record("p_lag1", "Check login", new, {"a": 1},
                     {"logged_in": True}, True, 0.1, [])
        carried = [{"name": "Check login", "type": "connector",
                    "read_only": True, "external_impact": "reads",
                    "code": old,
                    "outputs": [{"name": "logged_in", "type": "boolean"}]}]
        node_tools.fill_from_cells("p_lag1", carried)
        self.assertEqual(carried[0]["code"], new)
        authored = [{"name": "Check login", "type": "connector",
                     "read_only": True, "external_impact": "reads",
                     "code": "write_output('logged_in', my_own_version())",
                     "outputs": [{"name": "logged_in", "type": "boolean"}]}]
        node_tools.fill_from_cells("p_lag1", authored)
        self.assertIn("my_own_version", authored[0]["code"])

    def test_connector_needs_a_real_send_not_just_code(self):
        from agent import node_tools
        p = _workflow([], pid="p_conn1")
        node = [{"name": "Post it", "type": "connector",
                 "external_impact": "posts a message",
                 "code": "send(read_input('x'))"}]
        self.assertEqual(node_tools.untested_steps(p, node), ["Post it"])
        cells.record("p_conn1", "Post it", "send(read_input('x'))",
                     {"x": 1}, {"ok": True}, True, 0.1, [])
        self.assertEqual(node_tools.untested_steps(p, node), [])

    def test_changed_step_is_not_announced_as_never_tried(self):
        from agent import node_tools
        p = _workflow([], pid="p_stalewarn")
        node_tools.tool_save_intent(p, "Do the thing.")
        cells.record("p_stalewarn", "Parse", "write_output('y', 1)",
                     {}, {"y": 1}, True, 0.1, [])
        p["plan"] = None
        res = node_tools.tool_save_plan(p, {"summary": "Parse and keep.", "nodes": [
            {"name": "Parse", "type": "code",
             "code": "write_output('y', 2)  # edited since the proven run",
             "outputs": [{"name": "y", "type": "number"}]}]})

        notes = " ".join(res.get("notes") or [])
        self.assertIn("code changed since the last recorded run", notes)
        self.assertNotIn("not yet tried for real: \"Parse\"", notes)
        self.assertNotIn("Parse", " ".join(p["plan"].get("warnings") or []))

if __name__ == "__main__":
    unittest.main()

class HonestyNotesTest(unittest.TestCase):
    def test_forced_connector_without_a_recorded_run_notes_it(self):
        node = {"id": "n_f", "name": "Send it", "type": "connector",
                "read_only": False, "config": {"code": "def f():\n    return 1"},
                "inputs": [], "outputs": [{"name": "posted", "type": "boolean"}],
                "tests": []}
        p = _workflow([node], pid="p_note_force")
        plan = {"nodes": [{"name": "Send it", "type": "connector"}]}
        r = receipts.check(p, plan)
        self.assertTrue(r["ok"])
        self.assertTrue(any("without a trial run" in n
                            for n in r["notes"]), r)

    def test_forced_code_step_without_a_recorded_run_notes_it(self):
        node = {"id": "n_fc", "name": "Shape it", "type": "code",
                "config": {"code": "write_output('y', 1)"},
                "inputs": [], "outputs": [{"name": "y", "type": "number"}],
                "tests": []}
        p = _workflow([node], pid="p_note_fcode")
        plan = {"nodes": [{"name": "Shape it", "type": "code"}]}
        r = receipts.check(p, plan)
        self.assertTrue(r["ok"])
        self.assertTrue(any("without a trial run" in n
                            for n in r["notes"]), r)

    def test_code_step_with_a_recorded_run_stays_silent(self):
        from agent import cells
        node = {"id": "n_okc", "name": "Shape it", "type": "code",
                "config": {"code": "write_output('y', 1)"},
                "inputs": [], "outputs": [{"name": "y", "type": "number"}],
                "tests": []}
        p = _workflow([node], pid="p_note_okcode")
        cells.record("p_note_okcode", "Shape it", "write_output('y', 1)",
                     {}, {"y": 1}, True, 0.1, [])
        plan = {"nodes": [{"name": "Shape it", "type": "code"}]}
        r = receipts.check(p, plan)
        self.assertEqual([n for n in r["notes"] if "Shape it" in n], [])

    def test_seam_with_no_recorded_producer_notes_it(self):
        src = {"id": "n_a", "name": "Make it", "type": "code",
               "config": {"code": "x"}, "inputs": [],
               "outputs": [{"name": "value", "type": "text"}], "tests": []}
        dst = {"id": "n_b", "name": "Use it", "type": "code",
               "config": {"code": "y"},
               "inputs": [{"name": "value", "type": "text"}],
               "outputs": [{"name": "out", "type": "text"}], "tests": []}
        p = _workflow([src, dst], pid="p_note_seam")
        plan = {"nodes": [], "edges": [{"src": "Make it", "dst": "Use it"}]}
        r = receipts.check(p, plan)
        self.assertTrue(any("could not be checked" in n for n in r["notes"]), r)

    def test_user_input_producer_is_expected_and_silent(self):
        src = {"id": "n_u", "name": "Ask", "type": "user-input",
               "config": {}, "inputs": [],
               "outputs": [{"name": "choice", "type": "text"}], "tests": []}
        dst = {"id": "n_c", "name": "Use it", "type": "code",
               "config": {"code": "y"},
               "inputs": [{"name": "choice", "type": "text"}],
               "outputs": [{"name": "out", "type": "text"}], "tests": []}
        p = _workflow([src, dst], pid="p_note_ui")
        plan = {"nodes": [], "edges": [{"src": "Ask", "dst": "Use it"}]}
        r = receipts.check(p, plan)
        self.assertEqual(r["notes"], [])

class MissingEvidenceMarkerTest(unittest.TestCase):
    def test_deref_marks_a_missing_blob(self):
        v = cells._deref({"$evidence_blob": "0" * 64, "sketch": {"shape": 1}})
        self.assertIn("$missing_evidence", v)
        self.assertEqual(v["sketch"], {"shape": 1})

    def test_latest_outputs_skips_marker_values(self):
        pid = "p_marker_chain"
        _workflow([], pid=pid)
        cells.record(pid, "Good", "c", {}, {"kept": "yes"}, True, 0.1, [])

        cells.record(pid, "Gone", "c", {},
                     {"$evidence_blob": "1" * 64, "sketch": {}}, True, 0.1, [])
        out = cells.latest_outputs(pid)
        self.assertEqual(out.get("kept"), "yes")
        self.assertNotIn("$missing_evidence", out)
        self.assertNotIn("sketch", out)

class CredentialScanTest(unittest.TestCase):
    def _p(self, code="", prompt="", tests=None):
        return {"id": "p_cred", "nodes": [
            {"id": "n1", "name": "Send it", "type": "connector",
             "config": {"code": code, "prompt": prompt,
                        "tests": tests or []}}]}

    def test_clean_build_records_that_it_looked(self):
        r = receipts._credential_scan(
            self._p(code='key = get_secret("API_KEY")\nsend(key)'))
        self.assertTrue(r["ok"])
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["steps"], 1)
        self.assertTrue(r["ts"])

    def test_a_pasted_key_is_found_and_never_quoted_back(self):
        secret = "sk-ant-" + "a1b2c3d4" * 5
        r = receipts._credential_scan(self._p(code=f'key = "{secret}"'))
        self.assertFalse(r["ok"])
        joined = " ".join(r["findings"])
        self.assertIn("Send it", joined)
        self.assertIn("get_secret", joined)

        self.assertNotIn(secret, joined)

    def test_a_stored_value_pasted_into_a_prompt_is_found(self):
        from storage import secrets_store
        secrets_store.set_secret("CRED_SCAN_KEY", "hunter2-very-secret-value", secrets_store.OWNER_APP)
        r = receipts._credential_scan(
            self._p(prompt="Use hunter2-very-secret-value to sign in"))
        self.assertFalse(r["ok"])
        self.assertNotIn("hunter2-very-secret-value", " ".join(r["findings"]))

    def test_a_saved_example_is_checked_too(self):
        from storage import secrets_store
        secrets_store.set_secret("CRED_SCAN_KEY2", "topsecret-example-value", secrets_store.OWNER_APP)
        r = receipts._credential_scan(self._p(
            code='x = read_input("a")',
            tests=[{"name": "t", "inputs": {"a": "topsecret-example-value"}}]))
        self.assertFalse(r["ok"])
        self.assertIn("saved example", " ".join(r["findings"]))

    def test_ordinary_content_never_trips_it(self):
        r = receipts._credential_scan(self._p(
            code='ref = "blob:" + "0123456789abcdef" * 4\n'
                 'note = "Bearer with us while this loads"'))
        self.assertTrue(r["ok"], r["findings"])

    def test_the_flag_rides_the_receipt(self):
        p = self._p(code='k = get_secret("K")')
        r = receipts.check(p, {"nodes": []})
        self.assertTrue((r.get("credential_scan") or {}).get("ok"))
