# Tests: values at run time
from __future__ import annotations

import json
import unittest

from tests import _bootstrap
from tests._bootstrap import workflow as _workflow

from runtime import executor
from agent import node_tools
from agent import turnstate
from runtime import run_state
from storage import store

def _greet_node():
    return {"id": "n_g", "name": "greet", "type": "code",
            "config": {"code": "def greet(region):\n    return 'hello ' + region\n"},
            "inputs": [{"name": "region", "type": "text"}],
            "outputs": [{"name": "greeting", "type": "text"}], "tests": []}

class BareGetSecretParityTest(unittest.TestCase):
    def _node(self, secret_name="hook_url_lit"):
        return {"id": "n_s", "name": "send", "type": "code",
                "config": {"code": "def send(message):\n"
                                   f"    url = get_secret('{secret_name}')\n"
                                   "    return message + ':' + str(len(url))\n"},
                "inputs": [{"name": "message", "type": "text"}],
                "outputs": [{"name": "sent", "type": "text"}], "tests": []}

    def test_bare_literal_resolves_from_store(self):
        from storage import secrets_store
        secrets_store.set_secret("hook_url_lit", "https://x.example/h", secrets_store.workflow_owner("p_seclit1"))
        p = _workflow([self._node()], pid="p_seclit1")
        res = executor.run_workflow(p, {"message": "hi"})
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(res["outputs"]["n_s"]["sent"], "hi:19")

    def test_unset_literal_halts_resumably_not_a_ticket(self):
        p = _workflow([self._node("hook_url_missing")], pid="p_seclit2")
        res = executor.run_workflow(p, {"message": "hi"})
        self.assertEqual((res["status"], res["reason"]),
                         ("halted", "missing-value"), res)
        self.assertIn("hook_url_missing", res["verdict"]["missing_secrets"])

        res2 = executor.run_workflow(p, {"message": "hi"}, run_id=res["run_id"],
                                     secret_inputs={"hook_url_missing": "12345"})
        self.assertEqual(res2["status"], "completed", res2)
        self.assertEqual(res2["outputs"]["n_s"]["sent"], "hi:5")

    def test_secret_value_never_reaches_outputs(self):
        from storage import secrets_store
        secrets_store.set_secret("hook_url_leak", "SECRETVALUE99", secrets_store.workflow_owner("p_seclit3"))
        node = {"id": "n_l", "name": "leak", "type": "code",
                "config": {"code": "def leak(message):\n"
                                   "    return get_secret('hook_url_leak')\n"},
                "inputs": [{"name": "message", "type": "text"}],
                "outputs": [{"name": "sent", "type": "text"}], "tests": []}
        p = _workflow([node], pid="p_seclit3")
        res = executor.run_workflow(p, {"message": "hi"})
        self.assertNotIn("SECRETVALUE99", str(res))

class VariableResolutionTest(unittest.TestCase):
    def test_unwired_port_resolves_from_set_variable(self):
        p = _workflow([_greet_node()], pid="p_var1")
        p["variables"] = [{"name": "region", "value": "EMEA", "secret": False,
                           "kind": "config"}]
        store.save(p)
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(res["outputs"]["n_g"]["greeting"], "hello EMEA")

    def test_entry_input_beats_variable(self):
        p = _workflow([_greet_node()], pid="p_var2")
        p["variables"] = [{"name": "region", "value": "EMEA", "secret": False,
                           "kind": "config"}]
        store.save(p)
        res = executor.run_workflow(p, {"region": "APAC"})
        self.assertEqual(res["outputs"]["n_g"]["greeting"], "hello APAC")

    def test_unset_variable_pauses_then_resumes_with_value(self):
        p = _workflow([_greet_node()], pid="p_var3")
        p["variables"] = [{"name": "region", "value": "", "secret": False,
                           "kind": "config"}]
        store.save(p)
        res = executor.run_workflow(p, {})
        self.assertEqual((res["status"], res["reason"]), ("halted", "missing-value"))
        self.assertEqual(res["verdict"]["missing_values"], ["region"])
        self.assertNotIn("case_id", res)

        res2 = executor.run_workflow(p, {"region": "Nordics"}, run_id=res["run_id"])
        self.assertEqual(res2["status"], "completed", res2)
        self.assertEqual(res2["outputs"]["n_g"]["greeting"], "hello Nordics")
        self.assertIsNone(run_state.load(res["run_id"]))

    def test_no_variable_entry_halts_honestly(self):
        p = _workflow([_greet_node()], pid="p_var4")
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "halted")
        self.assertEqual(res["reason"], "input-unconnected")
        self.assertIn("never received", " ".join(res["verdict"]["environment"]))

    def test_untaken_branch_still_skips(self):
        nodes = [
            {"id": "n_in", "name": "in", "type": "user-input", "config": {},
             "inputs": [], "outputs": [{"name": "kind", "type": "text"}], "tests": []},
            {"id": "n_a", "name": "a", "type": "code",
             "config": {"code": "def a(kind):\n    return {'payload': 'A'}\n"},
             "inputs": [{"name": "kind", "type": "text"}],
             "outputs": [{"name": "payload", "type": "text"}], "tests": []},
            {"id": "n_b", "name": "b", "type": "code",
             "config": {"code": "def b(payload):\n    return payload\n"},
             "inputs": [{"name": "payload", "type": "text"}],
             "outputs": [{"name": "out", "type": "text"}], "tests": []},
        ]
        p = _workflow(nodes, pid="p_var5")
        p["edges"] = [{"src": "n_in", "dst": "n_a", "when": "kind == 'x'"},
                      {"src": "n_in", "dst": "n_b", "when": "kind == 'other'"}]
        p["variables"] = [{"name": "payload", "value": "FROM_VARIABLE",
                           "secret": False, "kind": "config"}]
        store.save(p)
        res = executor.run_workflow(p, {"kind": "x"})
        self.assertEqual(res["status"], "completed", res)
        self.assertNotIn("n_b", res["outputs"])

        from storage import deliverables
        m = next(r for r in deliverables.list_runs("p_var5")
                 if r["run_id"] == res["run_id"])
        skipped = next(e for e in m["steps"] if e.get("node") == "n_b")
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(skipped["reason"], "branch-not-taken")
        done = next(e for e in m["steps"] if e.get("node") == "n_a")
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["name"], "a")
        self.assertEqual(done["output"], {"payload": "A"})
        self.assertEqual(done["inputs"], {"kind": "x"})

    def test_merge_entry(self):
        run_state.start("p_var6", "run_m1", {"a": 1})
        run_state.merge_entry("run_m1", {"b": 2})
        self.assertEqual(run_state.entry_inputs("run_m1"), {"a": 1, "b": 2})
        run_state.finish("run_m1")

class MidRunInputTest(unittest.TestCase):
    def _nodes(self):
        return [
            {"id": "n_s", "name": "scan", "type": "code",
             "config": {"code": "write_output('networks', ['A', 'B', 'C'])\n"},
             "inputs": [], "outputs": [{"name": "networks", "type": "any"}], "tests": []},
            {"id": "n_p", "name": "pick", "type": "user-input", "config": {},
             "inputs": [{"name": "networks", "type": "any"}],
             "outputs": [{"name": "chosen", "type": "text", "options": []}], "tests": []},
        ]

    def test_pauses_for_choice_then_resumes(self):
        p = _workflow(self._nodes(), pid="p_pick")
        p["edges"] = [{"src": "n_s", "dst": "n_p", "when": ""}]
        store.save(p)
        res = executor.run_workflow(p, {})
        self.assertEqual((res["status"], res["reason"]), ("halted", "input-needed"))
        self.assertNotIn("case_id", res)
        fields = res["verdict"]["input_request"]["fields"]
        self.assertEqual(fields[0]["name"], "chosen")
        self.assertEqual(fields[0]["options"], ["A", "B", "C"])
        res2 = executor.run_workflow(p, {"chosen": "B"}, run_id=res["run_id"])
        self.assertEqual(res2["status"], "completed", res2)
        self.assertEqual(res2["outputs"]["n_p"]["chosen"], "B")

    def test_independent_user_input_still_up_front(self):
        p = _workflow([{"id": "n_i", "name": "in", "type": "user-input", "config": {},
                       "inputs": [], "outputs": [{"name": "x", "type": "text"}],
                       "tests": []}], pid="p_pick2")
        res = executor.run_workflow(p, {"x": "hi"})
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(res["outputs"]["n_i"]["x"], "hi")

class EntryCheckBitesTest(unittest.TestCase):
    def _nodes(self):
        return [{"id": "n_u", "name": "Get search link", "type": "user-input",
                 "config": {"criteria": [
                     {"expr": "'/people/' in search_url", "hardness": "hard",
                      "label": "The link must be a LinkedIn people search"}]},
                 "inputs": [],
                 "outputs": [{"name": "search_url", "type": "text"}],
                 "tests": []}]

    def test_wrong_value_pauses_reasks_and_resumes(self):
        p = _workflow(self._nodes(), pid="p_entrychk")
        res = executor.run_workflow(p, {"search_url": "https://x.com/company"})
        self.assertEqual((res["status"], res["reason"]),
                         ("halted", "input-needed"))
        self.assertNotIn("case_id", res)
        env = " ".join(res["verdict"]["environment"])
        self.assertIn("people search", env)
        fields = res["verdict"]["input_request"]["fields"]
        self.assertEqual(fields[0]["name"], "search_url")
        res2 = executor.run_workflow(
            p, {"search_url": "https://ln.com/search/results/people/?q=x"},
            run_id=res["run_id"])
        self.assertEqual(res2["status"], "completed", res2)

class DeclareVariablesTest(unittest.TestCase):
    def test_declares_entries_never_overwrites(self):
        p = _workflow(pid="p_var7")
        r = node_tools.tool_declare_variables(p, [
            {"name": "crm_base_url", "kind": "config"},
            {"name": "api_token", "secret": True}])
        self.assertEqual(r["added"], ["crm_base_url", "api_token"])

        from storage import store as _store
        p["variables"][0]["value"] = "https://crm.example"
        _store.save(p)
        r2 = node_tools.tool_declare_variables(p, [{"name": "crm_base_url"}])
        self.assertEqual(r2["already_present"], ["crm_base_url"])
        self.assertEqual(p["variables"][0]["value"], "https://crm.example")
        self.assertIn("error", node_tools.tool_declare_variables(p, [{}]))

    def test_persistent_default_and_per_run_flag(self):
        p = _workflow(pid="p_var8")
        node_tools.tool_declare_variables(p, [
            {"name": "kept_one"}, {"name": "each_run", "persistent": False}])
        by = {v["name"]: v for v in p["variables"]}
        self.assertTrue(by["kept_one"]["persistent"])
        self.assertFalse(by["each_run"]["persistent"])

class ValueKeepsItsTypeTest(unittest.TestCase):
    def _typed_node(self):
        return {"id": "n_fetch", "name": "Fetch", "type": "code",
                "config": {"code": "def fetch(posts_per_scan):\n"
                                   "    return {'posts': int(posts_per_scan)}\n"},
                "inputs": [{"name": "posts_per_scan", "type": "number",
                            "label": "Posts per scan"}],
                "outputs": [{"name": "posts", "type": "number"}], "tests": []}

    def test_a_number_setting_satisfies_a_number_port(self):
        p = _workflow([self._typed_node()], pid="p_type1")
        node_tools.tool_declare_variables(p, [{"name": "posts_per_scan",
                                               "value": 100}])
        self.assertEqual(p["variables"][0]["value"], 100)
        store.save(p)
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(res["outputs"]["n_fetch"]["posts"], 100)

    def test_text_from_a_setup_card_is_stored_as_the_declared_type(self):
        p = _workflow([self._typed_node()], pid="p_type2")
        node_tools.tool_declare_variables(p, [{"name": "posts_per_scan",
                                               "value": "100"}])
        self.assertEqual(p["variables"][0]["value"], 100)
        store.save(p)
        self.assertEqual(executor.run_workflow(p, {})["status"], "completed")

    def test_a_value_that_cannot_mean_the_type_is_refused_with_a_reason(self):
        p = _workflow([self._typed_node()], pid="p_type3")
        r = node_tools.tool_declare_variables(p, [{"name": "posts_per_scan",
                                                   "value": "lots"}])
        self.assertIn("error", r)
        self.assertIn("number", r["error"])
        self.assertEqual(p.get("variables") or [], [])

    def test_zero_and_false_are_values_not_unset(self):
        node = self._typed_node()
        node["inputs"].append({"name": "dry_run", "type": "boolean"})
        node["config"]["code"] = ("def fetch(posts_per_scan, dry_run):\n"
                                  "    return {'posts': int(posts_per_scan)}\n")
        p = _workflow([node], pid="p_type4")
        r = node_tools.tool_declare_variables(p, [
            {"name": "posts_per_scan", "value": 0},
            {"name": "dry_run", "value": False}])
        by = {v["name"]: v["value"] for v in p["variables"]}
        self.assertEqual(by["posts_per_scan"], 0)
        self.assertIs(by["dry_run"], False)

        self.assertEqual(sorted(r["values_stored"]),
                         ["dry_run", "posts_per_scan"])
        store.save(p)
        self.assertEqual(executor.run_workflow(p, {})["status"], "completed")

    def test_a_boolean_setting_understands_the_usual_words(self):
        node = self._typed_node()
        node["inputs"] = [{"name": "dry_run", "type": "boolean"}]
        node["config"]["code"] = ("def fetch(dry_run):\n"
                                  "    return {'posts': 1 if dry_run else 0}\n")
        p = _workflow([node], pid="p_type5")
        node_tools.tool_declare_variables(p, [{"name": "dry_run",
                                               "value": "yes"}])
        self.assertIs(p["variables"][0]["value"], True)
        store.save(p)
        self.assertEqual(executor.run_workflow(p, {})["outputs"]
                         ["n_fetch"]["posts"], 1)

    def test_an_untyped_name_keeps_whatever_it_was_given(self):
        p = _workflow([], pid="p_type6")
        node_tools.tool_declare_variables(p, [{"name": "later", "value": 7}])
        self.assertEqual(p["variables"][0]["value"], 7)

def _chain_nodes():
    return [
        {"id": "n_a", "name": "a", "type": "code",
         "config": {"code": "def a():\n    return {'out': 'A'}\n"},
         "inputs": [], "outputs": [{"name": "out", "type": "text"}], "tests": []},
        {"id": "n_b", "name": "b", "type": "code",
         "config": {"code": "def b(out):\n    return {'out2': out + 'B'}\n"},
         "inputs": [{"name": "out", "type": "text"}],
         "outputs": [{"name": "out2", "type": "text"}], "tests": []},
    ]

class SecretRunTest(unittest.TestCase):
    def _use(self):
        return {"id": "n_use", "name": "use", "type": "code",
                "config": {"code": "write_output('echo', get_secret('pw'))\n"},
                "inputs": [{"name": "pw", "type": "secret"}],
                "outputs": [{"name": "echo", "type": "text"}], "tests": []}

    def test_missing_secret_halts_resumable_then_runs(self):
        p = _workflow([self._use()], pid="p_sec1")
        res = executor.run_workflow(p, {})
        self.assertEqual((res["status"], res["reason"]), ("halted", "missing-value"))
        self.assertEqual(res["verdict"]["missing_secrets"], ["pw"])
        self.assertNotIn("case_id", res)

        res2 = executor.run_workflow(p, {}, run_id=res["run_id"],
                                     secret_inputs={"pw": "hunter2"})
        self.assertEqual(res2["status"], "completed", res2)
        self.assertEqual(res2["outputs"]["n_use"]["echo"], "***")

    def test_secret_value_never_persists_to_the_buffer(self):
        p = _workflow([
            self._use(),
            {"id": "n_gate", "name": "gate", "type": "connector",
             "external_impact": "sends", "read_only": False,
             "config": {"code": "write_output('done', True)\n"},
             "inputs": [{"name": "echo", "type": "text"}],
             "outputs": [{"name": "done", "type": "boolean"}], "tests": []}],
            pid="p_sec2")
        p["edges"] = [{"src": "n_use", "dst": "n_gate", "when": ""}]
        store.save(p)
        res = executor.run_workflow(p, {}, secret_inputs={"pw": "hunter2"})
        self.assertEqual((res["status"], res["reason"]),
                         ("halted", "awaiting-approval"))
        buf = run_state.load(res["run_id"]) or {}
        import json as _json
        self.assertNotIn("hunter2", _json.dumps(buf))
        self.assertEqual(buf["steps"]["n_use"]["output"]["echo"], "***")

class StepTraceTest(unittest.TestCase):
    def test_long_values_cap_and_progress_notes_ride(self):
        nodes = [{"id": "n_big", "name": "Fetch pages", "type": "code",
                  "config": {"code":
                             "import time\n"
                             "heartbeat('page 1: 30 cards')\n"
                             "time.sleep(0.7)\n"
                             "write_output('html', 'x' * 3000)\n"},
                  "inputs": [],
                  "outputs": [{"name": "html", "type": "text"}],
                  "tests": []}]
        p = _workflow(nodes, pid="p_trace1")
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "completed", res)
        from storage import deliverables
        m = next(r for r in deliverables.list_runs("p_trace1")
                 if r["run_id"] == res["run_id"])
        e = m["steps"][0]
        self.assertEqual(e["name"], "Fetch pages")
        self.assertIn("shortened - 3,000 characters", e["output"]["html"])
        self.assertIn("page 1: 30 cards", " ".join(e.get("notes") or []))

    def test_a_secret_value_never_reaches_the_trace(self):
        from storage import secrets_store
        secrets_store.set_secret("trace_pw", "hunter2secret", secrets_store.workflow_owner("p_trace2"))
        nodes = [{"id": "n_s", "name": "Use it", "type": "code",
                  "config": {"code": "write_output('echo', 'token=' + get_secret('trace_pw'))\n"},
                  "inputs": [],
                  "outputs": [{"name": "echo", "type": "text"}],
                  "tests": []}]
        p = _workflow(nodes, pid="p_trace2")
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "completed", res)
        from storage import deliverables
        import json as _json
        m = next(r for r in deliverables.list_runs("p_trace2")
                 if r["run_id"] == res["run_id"])
        self.assertNotIn("hunter2secret", _json.dumps(m))
        self.assertIn("token=", m["steps"][0]["output"]["echo"])

class StopMidRunTest(unittest.TestCase):
    def test_stop_halts_promptly_then_resumes(self):
        p = _workflow(_chain_nodes(), pid="p_stop1")
        p["edges"] = [{"src": "n_a", "dst": "n_b", "when": ""}]
        store.save(p)
        calls = []
        def should_stop():
            calls.append(1)
            return len(calls) > 1
        res = executor.run_workflow(p, {}, should_stop=should_stop)
        self.assertEqual((res["status"], res["reason"]),
                         ("halted", "stopped-by-user"))
        self.assertIn(res["halted_at"], ("n_a", "n_b"))
        self.assertNotIn("case_id", res)

        res2 = executor.run_workflow(p, {}, run_id=res["run_id"],
                                     should_stop=lambda: False)
        self.assertEqual(res2["status"], "completed", res2)
        self.assertEqual(res2["outputs"]["n_a"]["out"], "A")
        self.assertEqual(res2["outputs"]["n_b"]["out2"], "AB")

    def test_no_should_stop_behaves_exactly_as_before(self):
        p = _workflow(_chain_nodes(), pid="p_stop2")
        p["edges"] = [{"src": "n_a", "dst": "n_b", "when": ""}]
        store.save(p)
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(res["outputs"]["n_b"]["out2"], "AB")

if __name__ == "__main__":
    unittest.main()

class FirstRunFixesTest(unittest.TestCase):
    def test_approval_halt_records_no_corpus_case(self):
        from runtime import corpus
        p = {"id": "p_apphalt", "environment_id": None, "variables": [],
             "nodes": [{"id": "n_w", "name": "Send", "type": "connector",
                        "read_only": False, "external_impact": "sends",
                        "config": {"code": "write_output('sent', True)"},
                        "inputs": [], "outputs": [{"name": "sent", "type": "boolean"}],
                        "tests": []}],
             "edges": []}
        from storage import store as _st
        _st.save(p)
        res = executor.run_workflow(p, {})
        self.assertEqual(res["reason"], "awaiting-approval")
        self.assertIsNone(res.get("case_id"))
        self.assertEqual(corpus.cases("p_apphalt", "n_w"), [])
        run_state.finish(res["run_id"])

    def test_record_picker_offers_field_values(self):
        p = {"id": "p_picker", "environment_id": None, "variables": [],
             "nodes": [
                 {"id": "n_scan", "name": "Scan", "type": "code",
                  "config": {"code": "write_output('networks', [{'ssid': 'A', 'security': 'WPA2'},{'ssid': 'B', 'security': 'WPA2'}])"},
                  "inputs": [], "outputs": [{"name": "networks", "type": "any"}],
                  "tests": []},
                 {"id": "n_pick", "name": "Pick", "type": "user-input",
                  "inputs": [{"name": "networks", "type": "any"}],
                  "outputs": [{"name": "ssid", "type": "text",
                               "value_field": "ssid"}],
                  "tests": []}],
             "edges": [{"src": "n_scan", "dst": "n_pick", "count": 0, "when": ""}]}
        from storage import store as _st
        _st.save(p)
        res = executor.run_workflow(p, {})
        self.assertEqual(res["reason"], "input-needed")
        opts = res["verdict"]["input_request"]["fields"][0]["options"]
        self.assertEqual(opts[0], {"label": "A - WPA2", "value": "A"})
        run_state.finish(res["run_id"])

class WriteConnectorSafetyTest(unittest.TestCase):
    def test_missing_read_only_key_means_write(self):
        from step_types import writes_outside
        legacy = {"type": "connector"}
        self.assertTrue(writes_outside(legacy))
        ro = {"type": "connector", "read_only": True}
        self.assertFalse(writes_outside(ro))

    def test_fired_connector_never_refires_on_resume(self):
        import tempfile
        from pathlib import Path as _P

        marker = _P(tempfile.mkdtemp(prefix="fired_")) / "fired_count.txt"
        marker.write_text("0")

        code = (f"n = int(open({str(marker)!r}).read()) + 1\n"
                f"open({str(marker)!r}, 'w').write(str(n))\n"
                "write_output('sent', True)\n")
        node = {"id": "n_fire", "name": "Send it", "type": "connector",
                "read_only": False, "approval_suppressed": True,
                "config": {"code": code}, "inputs": [],
                "outputs": [{"name": "sent", "type": "boolean"},
                            {"name": "receipt", "type": "text"}]}
        p = _workflow([node], pid="p_refire")
        res = executor.run_workflow(p, {})
        self.assertEqual(res["reason"], "output-check-failed (standard)")
        self.assertEqual(marker.read_text(), "1")
        self.assertTrue(run_state.has_fired(res["run_id"], "n_fire"))

        res2 = executor.run_workflow(p, {}, run_id=res["run_id"])
        self.assertEqual(res2["reason"], "fired-unverified")
        self.assertEqual(marker.read_text(), "1")
        self.assertIn("send it AGAIN",
                      " ".join(res2["verdict"]["environment"]))
        run_state.finish(res2["run_id"])

class NoPreRunSweepTest(unittest.TestCase):
    def test_failing_stored_test_does_not_block_the_run(self):
        p = _workflow([{
            "id": "n_sw", "name": "Double it", "type": "code",
            "config": {"code": "write_output('out', read_input('a') * 2)"},
            "inputs": [{"name": "a", "type": "number"}],
            "outputs": [{"name": "out", "type": "number"}],
            "tests": [{"name": "stale", "inputs": {"a": 2}, "expect": "ok",
                       "asserts": ["out == 5"]}]}], pid="p_sweep1")
        res = executor.run_workflow(p, {"a": 3})
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(res["outputs"]["n_sw"]["out"], 6)

class AiFailureRoutingTest(unittest.TestCase):
    def _ai_node(self, outputs=None):
        return {"id": "n_ai", "name": "Judge posts", "type": "ai",
                "config": {"prompt": "judge", "model": {"model": "m1"}},
                "inputs": [{"name": "posts", "type": "any"}],
                "outputs": outputs or [
                    {"name": "kept", "type": "any"},
                    {"name": "note", "type": "text"}],
                "tests": []}

    def _run(self, ai_result, outputs=None, pid="p_aif"):
        from unittest import mock

        from runtime import capability, env_checks
        p = _workflow([self._ai_node(outputs)], pid=pid)
        with mock.patch.object(capability, "ai_call", return_value=ai_result), \
             mock.patch.object(env_checks, "check_all_nodes", return_value=[]), \
             mock.patch.object(env_checks, "check_node", return_value=[]):
            res = executor.run_workflow(p, {"posts": ["a", "b"]})
        if res.get("run_id"):
            run_state.finish(res["run_id"])
        return res

    def test_a_cut_off_answer_says_it_was_cut_off(self):
        res = self._run({"_unparsed": True, "error_kind": "max-tokens",
                         "_text": "the answer was cut off"}, pid="p_trunc1")
        self.assertEqual(res["reason"], "ai-answer-truncated")
        line = " ".join(res["verdict"]["ai_call"])
        self.assertIn("cut off", line)
        self.assertIn("more room", line)
        self.assertNotIn("not produced", line)

        from runtime import corpus
        case = corpus.cases("p_trunc1", "n_ai")[-1]
        self.assertEqual(case["cause"], "ai-answer-truncated")
        self.assertIsNotNone(case.get("output"))

    def test_a_failure_case_keeps_what_the_step_produced(self):
        res = self._run({"kept": ["a"]}, pid="p_outkeep")
        self.assertEqual(res["status"], "halted")
        from runtime import corpus
        case = corpus.cases("p_outkeep", "n_ai")[-1]
        self.assertEqual(case["output"], {"kept": ["a"]})

    def test_a_timeout_is_an_issue_with_its_numbers_not_weather(self):
        res = self._run({"_unparsed": True, "error_kind": "timeout",
                         "_text": "(model error) timed out waiting for the model's answer"}, pid="p_aif1")
        self.assertEqual((res["status"], res["reason"]),
                         ("halted", "ai-timed-out"), res)
        line = " ".join(res["verdict"]["ai_call"])
        self.assertIn("300 seconds", line)
        self.assertIn("asks for too much in one go", line)
        af = res.get("ai_failure")
        self.assertEqual(af["mode"], "timed-out")
        self.assertEqual(af["timeout_s"], 300)
        self.assertEqual(af["model"], "m1")
        self.assertEqual(af["batch_items"], 2)
        self.assertGreater(af["input_chars"], 0)
        from runtime import corpus
        self.assertEqual(corpus.cases("p_aif1", "n_ai")[-1]["cause"],
                         "ai-timed-out")

    def test_a_declared_timeout_extends_the_wall_never_shortens_it(self):
        import providers
        from runtime import capability
        self.assertEqual(providers.clamp_call_timeout(None), 300)
        self.assertEqual(providers.clamp_call_timeout(600), 600)
        self.assertEqual(providers.clamp_call_timeout(30), 300)
        self.assertEqual(providers.clamp_call_timeout("nope"), 300)
        node = {"config": {"timeout_seconds": 900}}
        self.assertEqual(capability.step_ai_timeout(node), 900)
        self.assertEqual(capability.step_ai_timeout({"config": {}}), 300)

    def test_failure_modes_ride_the_result_as_structured_blocks(self):
        res = self._run({"_unparsed": True, "error_kind": "max-tokens",
                         "_text": "cut off"}, pid="p_afb1")
        self.assertEqual(res["ai_failure"]["mode"], "truncated")
        res = self._run({"kept": [], "note": "  "}, pid="p_afb2")
        self.assertEqual(res["ai_failure"]["mode"], "blank")
        res = self._run({"kept": ["a"]}, pid="p_afb3")
        self.assertEqual(res["ai_failure"]["mode"], "wrong-shape")
        res = self._run({"_unparsed": True, "error_kind": "transport",
                         "_text": "boom"}, pid="p_afb4")
        self.assertNotIn("ai_failure", res)

    def test_overload_5xx_is_transient_too(self):
        res = self._run({"_unparsed": True, "error_kind": "http-529",
                         "_text": "(model error 529) overloaded"}, pid="p_aif2")
        self.assertEqual(res["reason"], "model-unavailable")

    def test_auth_points_at_admin_not_a_resume_loop(self):
        res = self._run({"_unparsed": True, "error_kind": "auth",
                         "_text": "(model error 401) invalid x-api-key"},
                        pid="p_aif3")
        self.assertEqual(res["reason"], "environment-check-failed")
        self.assertIn("Admin", " ".join(res["verdict"]["environment"]))

    def test_other_failures_carry_the_actual_message(self):
        res = self._run({"_unparsed": True, "error_kind": "http-400",
                         "_text": "(model error 400) max_tokens too large"},
                        pid="p_aif4")
        self.assertEqual(res["reason"], "ai-call-failed")
        self.assertIn("max_tokens too large",
                      " ".join(res["verdict"]["ai_call"]))

    def test_all_blank_answer_never_progresses(self):
        res = self._run({"kept": [], "note": "   "}, pid="p_aif5")
        self.assertEqual(res["reason"], "ai-answered-blank")
        self.assertIn("empty answers", " ".join(res["verdict"]["ai_call"]))

    def test_an_empty_required_text_is_a_problem_optional_may_be_empty(self):
        res = self._run({"kept": ["a"], "note": ""}, pid="p_aif6")
        self.assertEqual(res["status"], "halted", res)
        self.assertIn("empty", " ".join(res["verdict"]["standard_output_check"]))
        res = self._run({"kept": ["a"], "note": ""},
                        outputs=[{"name": "kept", "type": "any"},
                                 {"name": "note", "type": "text", "optional": True}],
                        pid="p_aif6b")
        self.assertEqual(res["status"], "completed", res)

    def test_legacy_lone_list_empty_is_still_a_quiet_day(self):
        res = self._run({"kept": []},
                        outputs=[{"name": "kept", "type": "any"}],
                        pid="p_aif7")
        self.assertEqual(res["status"], "completed", res)

class MidNodeStopTest(unittest.TestCase):
    def test_stop_interrupts_a_hanging_node_quickly(self):
        import time as _t

        from runtime import sandbox
        node = {"id": "n_hang", "name": "Hang", "type": "code",
                "config": {"code": "import time\n"
                                   "time.sleep(60)\n"
                                   "write_output('x', 1)"},
                "inputs": [], "outputs": [{"name": "x", "type": "number"}],
                "tests": []}
        p = _workflow([node], pid="p_hang")
        t0 = _t.time()
        res = executor.run_workflow(p, {}, should_stop=lambda: _t.time() - t0 > 1)
        elapsed = _t.time() - t0
        self.assertEqual(res["reason"], "stopped-by-user")
        self.assertEqual(res["halted_at"], "n_hang")
        self.assertLess(elapsed, 10, f"stop took {elapsed:.1f}s")

    def test_default_socket_timeout_is_set_in_the_child(self):
        from runtime import sandbox
        code = ("import socket\n"
                "write_output('t', socket.getdefaulttimeout())")
        res = sandbox.run(code, None, {}, {})
        self.assertEqual(res.get("t"), 30)

class NameResolutionAcrossOrderTest(unittest.TestCase):
    def _chain(self, pid):
        make = {"id": "n_make", "name": "make", "type": "code",
                "config": {"code": "def make():\n    return 'made-it'\n"},
                "inputs": [],
                "outputs": [{"name": "value", "type": "text"}], "tests": []}
        mid = {"id": "n_mid", "name": "mid", "type": "code",
               "config": {"code": "def mid():\n    return 'other'\n"},
               "inputs": [],
               "outputs": [{"name": "other", "type": "text"}], "tests": []}
        use = {"id": "n_use", "name": "use", "type": "code",
               "config": {"code": "def use(value):\n    return value + '!'\n"},
               "inputs": [{"name": "value", "type": "text"}],
               "outputs": [{"name": "shouted", "type": "text"}], "tests": []}
        p = _workflow([make, mid, use], pid=pid)
        p["edges"] = [{"src": "n_make", "dst": "n_mid", "count": 0, "when": ""},
                      {"src": "n_mid", "dst": "n_use", "count": 0, "when": ""}]
        return p

    def test_value_reaches_a_later_step_across_order_edges(self):
        p = self._chain("p_order1")
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(res["outputs"]["n_use"]["shouted"], "made-it!")

    def test_nearest_earlier_producer_wins(self):
        p = self._chain("p_order2")

        p["nodes"][1]["config"]["code"] = "def mid():\n    return 'nearer'\n"
        p["nodes"][1]["outputs"] = [{"name": "value", "type": "text"}]
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(res["outputs"]["n_use"]["shouted"], "nearer!")

    def test_untaken_branch_still_skips(self):
        p = self._chain("p_order3")
        p["edges"][1]["when"] = "other == 'never'"
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "completed", res)
        self.assertNotIn("n_use", res.get("outputs") or {})

    def test_a_parallel_branch_value_is_not_visible(self):
        start = {"id": "n_start", "name": "start", "type": "code",
                 "config": {"code": "def start():\n    return 'go'\n"},
                 "inputs": [], "outputs": [{"name": "go", "type": "text"}],
                 "tests": []}
        sibling = {"id": "n_sib", "name": "sibling", "type": "code",
                   "config": {"code": "def sibling():\n    return 'SIBLING'\n"},
                   "inputs": [], "outputs": [{"name": "value", "type": "text"}],
                   "tests": []}
        mine = {"id": "n_mine", "name": "mine", "type": "code",
                "config": {"code": "def mine():\n    return 'MINE'\n"},
                "inputs": [], "outputs": [{"name": "value", "type": "text"}],
                "tests": []}
        use = {"id": "n_use", "name": "use", "type": "code",
               "config": {"code": "def use(value):\n    return value + '!'\n"},
               "inputs": [{"name": "value", "type": "text"}],
               "outputs": [{"name": "shouted", "type": "text"}], "tests": []}

        p = _workflow([start, mine, sibling, use], pid="p_order4")
        p["edges"] = [{"src": "n_start", "dst": "n_mine", "when": ""},
                      {"src": "n_start", "dst": "n_sib", "when": ""},
                      {"src": "n_mine", "dst": "n_use", "when": ""}]
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "completed", res)
        self.assertEqual(res["outputs"]["n_use"]["shouted"], "MINE!")

class BranchRunsToItsEndTest(unittest.TestCase):
    def test_two_branches_run_one_after_the_other(self):
        def node(i):
            return {"id": i, "name": i, "type": "code", "config": {},
                    "inputs": [], "outputs": [], "tests": []}

        names = ["start", "a1", "b1", "a2", "b2", "a3", "b3", "end"]
        wf = {"nodes": [node(n) for n in names], "edges": [
            {"src": "start", "dst": "a1"}, {"src": "a1", "dst": "a2"},
            {"src": "a2", "dst": "a3"}, {"src": "a3", "dst": "end"},
            {"src": "start", "dst": "b1"}, {"src": "b1", "dst": "b2"},
            {"src": "b2", "dst": "b3"}, {"src": "b3", "dst": "end"}]}
        self.assertEqual(executor._execution_order(wf),
                         ["start", "a1", "a2", "a3", "b1", "b2", "b3", "end"])

    def test_the_topmost_ready_step_comes_next_when_a_branch_ends(self):
        def node(i):
            return {"id": i, "name": i, "type": "code", "config": {},
                    "inputs": [], "outputs": [], "tests": []}

        wf = {"nodes": [node(n) for n in ["x", "y", "z"]], "edges": []}
        self.assertEqual(executor._execution_order(wf), ["x", "y", "z"])

class MissingFieldsEnrichmentTest(unittest.TestCase):
    def _node(self, port_extra=None):
        return {"id": "n_mf", "name": "fetch", "type": "code",
                "config": {"code": "def f(feed_url):\n    return feed_url"},
                "inputs": [{"name": "feed_url", "type": "text",
                            **(port_extra or {})}],
                "outputs": [{"name": "out", "type": "text"}], "tests": []}

    def test_port_label_and_description_ride_the_verdict(self):
        p = _workflow([self._node({"label": "Reddit feed address",
                                  "description": "Your saved-posts RSS feed URL from reddit.com/prefs/feeds"})],
                     pid="p_mf_lbl")
        p["variables"] = [{"name": "feed_url", "value": "", "secret": False,
                           "persistent": True}]
        r = executor.run_workflow(p, {})
        self.assertEqual((r["status"], r["reason"]), ("halted", "missing-value"))
        f = r["verdict"]["missing_fields"][0]
        self.assertEqual(f["label"], "Reddit feed address")
        self.assertIn("reddit.com/prefs/feeds", f["description"])

    def test_humanised_fallback_when_nothing_declared(self):
        p = _workflow([self._node()], pid="p_mf_fallback")
        p["variables"] = [{"name": "feed_url", "value": "", "secret": False,
                           "persistent": True}]
        r = executor.run_workflow(p, {})
        f = r["verdict"]["missing_fields"][0]
        self.assertEqual(f["label"], "Feed url")

class MissingAtStartTest(unittest.TestCase):
    def _consumer(self, nid, name, extra=None):
        return {"id": nid, "name": nid, "type": "code",
                "config": {"code": f"def f({name}):\n    return {name}"},
                "inputs": [{"name": name, "type": "text",
                            **(extra or {})}],
                "outputs": [{"name": nid + "_out", "type": "text"}],
                "tests": []}

    def test_unset_variable_listed_with_label(self):
        p = _workflow([self._consumer("n_a", "feed_url",
                                     {"label": "Reddit feed address"})],
                     pid="p_pf1")
        p["variables"] = [{"name": "feed_url", "value": "", "secret": False,
                           "persistent": True}]
        missing = executor.missing_at_start(p)
        self.assertEqual([m["name"] for m in missing], ["feed_url"])
        self.assertEqual(missing[0]["label"], "Reddit feed address")

    def test_set_variable_and_produced_value_not_listed(self):
        maker = {"id": "n_mk", "name": "n_mk", "type": "code",
                 "config": {"code": "def f():\n    return 'x'"}, "inputs": [],
                 "outputs": [{"name": "made", "type": "text"}], "tests": []}
        p = _workflow([maker, self._consumer("n_b", "made"),
                      self._consumer("n_c", "region")], pid="p_pf2")
        p["edges"] = [{"src": "n_mk", "dst": "n_b", "count": 0, "when": ""},
                      {"src": "n_b", "dst": "n_c", "count": 0, "when": ""}]
        p["variables"] = [{"name": "region", "value": "EU", "secret": False,
                           "persistent": True}]
        self.assertEqual(executor.missing_at_start(p), [])

    def test_midrun_input_output_not_listed_but_secret_literal_is(self):
        asker = {"id": "n_ask", "name": "n_ask", "type": "user-input",
                 "config": {}, "inputs": [{"name": "trigger", "type": "text"}],
                 "outputs": [{"name": "choice", "type": "text"}], "tests": []}
        sender = {"id": "n_s", "name": "n_s", "type": "code",
                  "config": {"code": "def f(choice):\n"
                             "    k = get_secret('pf_hook')\n"
                             "    return choice"},
                  "inputs": [{"name": "choice", "type": "text"}],
                  "outputs": [{"name": "sent", "type": "text"}], "tests": []}
        p = _workflow([asker, sender], pid="p_pf3")
        p["edges"] = [{"src": "n_ask", "dst": "n_s", "count": 0, "when": ""}]
        missing = executor.missing_at_start(p)
        self.assertEqual([m["name"] for m in missing], ["pf_hook"])
        self.assertTrue(missing[0]["secret"])

class SenderSaysWhyTest(unittest.TestCase):
    def _run(self, ntype, read_only, output, outputs, pid):
        from unittest import mock

        from runtime import env_checks
        p = _workflow([{"id": "n_s", "name": "Send", "type": ntype, "read_only": read_only,
                       "approval_suppressed": True,
                       "config": {"code": "x"}, "inputs": [{"name": "people", "type": "any"}],
                       "outputs": outputs, "tests": []}], pid=pid)
        with mock.patch.object(executor, "_execute", return_value=(output, False)), \
             mock.patch.object(env_checks, "check_all_nodes", return_value=[]), \
             mock.patch.object(env_checks, "check_node", return_value=[]):
            res = executor.run_workflow(p, {"people": [{"n": 1}, {"n": 2}]})
        if res.get("run_id"):
            run_state.finish(res["run_id"])
        return res

    def test_zero_sent_no_reason_halts(self):
        outs = [{"name": "sent", "type": "any"}, {"name": "sent_count", "type": "number"}]
        res = self._run("connector", False, {"sent": [], "sent_count": 0}, outs, "p_noeff1")
        self.assertEqual(res.get("reason"), "no-effect", res)
        self.assertIn("given 2 items", " ".join(res["verdict"]["no_effect"]))

    def test_a_reason_passes_and_a_filter_stays_a_quiet_day(self):
        outs = [{"name": "sent", "type": "any"}, {"name": "note", "type": "text"}]
        res = self._run("connector", False,
                        {"sent": [], "note": "all 2 already connected - skipped"},
                        outs, "p_noeff2")
        self.assertEqual(res.get("status"), "completed", res)
        outs = [{"name": "kept", "type": "any"}, {"name": "count", "type": "number"}]
        res = self._run("code", True, {"kept": [], "count": 0}, outs, "p_noeff3")
        self.assertEqual(res.get("status"), "completed", res)

class RemoveVariablesTest(unittest.TestCase):
    def test_remove_refuses_the_only_source_and_drops_the_rest(self):
        from agent import node_tools
        from storage import environments
        p = _workflow([{"id": "n_a", "name": "Read", "type": "code", "config": {"code": "x"},
                       "inputs": [{"name": "table_link", "type": "text"}],
                       "outputs": [{"name": "sheet_id", "type": "text"}], "tests": []},
                      {"id": "n_b", "name": "Use", "type": "code", "config": {"code": "y"},
                       "inputs": [{"name": "sheet_id", "type": "text"}],
                       "outputs": [{"name": "done", "type": "boolean"}], "tests": []}],
                     pid="p_rmvar1")
        p["variables"] = [
            {"name": "table_link", "value": "https://x", "secret": False, "persistent": True},
            {"name": "sheet_id", "value": "", "secret": False, "persistent": True},
            {"name": "leftover", "value": "old", "secret": False, "persistent": True}]
        r = node_tools.tool_remove_variables(p, ["table_link", "sheet_id", "leftover", "nope"])
        self.assertEqual(r["refused"], ["table_link"])
        self.assertEqual(r["removed"], ["sheet_id"])
        self.assertEqual(r["kept_valued"], ["leftover"])
        self.assertEqual(r["unknown"], ["nope"])
        self.assertEqual(sorted(v["name"] for v in p["variables"]), ["leftover", "table_link"])

        r = node_tools.tool_remove_variables(p, ["leftover"], confirmed=True)
        self.assertEqual(r["removed"], ["leftover"])
        self.assertEqual([v["name"] for v in p["variables"]], ["table_link"])

    def test_retire_drops_a_blank_variable_a_step_output_shadows(self):
        from storage import environments
        p = _workflow([{"id": "n_a", "name": "Read", "type": "code", "config": {"code": "x"},
                       "inputs": [], "outputs": [{"name": "sheet_id", "type": "text"}], "tests": []},
                      {"id": "n_b", "name": "Use", "type": "code", "config": {"code": "y"},
                       "inputs": [{"name": "sheet_id", "type": "text"},
                                  {"name": "cap", "type": "number"}],
                       "outputs": [{"name": "done", "type": "boolean"}], "tests": []}],
                     pid="p_rmvar2")
        p["variables"] = [
            {"name": "sheet_id", "value": "", "secret": False, "persistent": True},
            {"name": "cap", "value": "", "secret": False, "persistent": True}]
        dropped = environments.retire_unused_fallbacks(p, None)
        self.assertEqual(dropped, ["sheet_id"])
        self.assertEqual([v["name"] for v in p["variables"]], ["cap"])

        from agent import plan_logic
        p["variables"].append({"name": "sheet_id", "value": "abc", "secret": False, "persistent": True})
        self.assertIn("sheet_id", plan_logic.unread_valued_variables(p, p["nodes"]))

class NothingToDoAndIssueChainTest(unittest.TestCase):
    def test_empty_inputs_stop_before_the_sender(self):
        from unittest import mock

        from runtime import env_checks
        p = _workflow([{"id": "n_f", "name": "Find contacts", "type": "code",
                       "config": {"code": "x"}, "inputs": [],
                       "outputs": [{"name": "people", "type": "any"}], "tests": []},
                      {"id": "n_s", "name": "Send", "type": "connector", "read_only": False,
                       "config": {"code": "y"}, "inputs": [{"name": "people", "type": "any"}],
                       "outputs": [{"name": "sent", "type": "any"}], "tests": []}],
                     pid="p_ntd1")
        p["edges"] = [{"src": "n_f", "dst": "n_s"}]
        with mock.patch.object(executor, "_execute", return_value=({"people": []}, False)), \
             mock.patch.object(env_checks, "check_all_nodes", return_value=[]), \
             mock.patch.object(env_checks, "check_node", return_value=[]):
            res = executor.run_workflow(p, {})
        if res.get("run_id"):
            run_state.finish(res["run_id"])
        self.assertEqual(res.get("reason"), "nothing-to-do", res)
        self.assertEqual(res.get("halted_at"), "n_s")
        line = " ".join(res["verdict"]["nothing_to_do"])
        self.assertIn('"Find contacts" found nothing', line)
        self.assertIn("dismiss this", line)

    def test_the_work_list_decides_beside_a_full_reference_list(self):
        from unittest import mock

        from runtime import env_checks
        code = ("contacts = read_input('contacts')\n"
                "rows = read_input('sheet_rows')\n"
                "for c in contacts:\n"
                "    pass\n")
        p = _workflow([{"id": "n_f", "name": "Find contacts", "type": "code",
                       "config": {"code": "x"}, "inputs": [],
                       "outputs": [{"name": "contacts", "type": "any"},
                                   {"name": "sheet_rows", "type": "any"}],
                       "tests": []},
                      {"id": "n_s", "name": "Send", "type": "connector",
                       "read_only": False, "config": {"code": code},
                       "inputs": [{"name": "contacts", "type": "any"},
                                  {"name": "sheet_rows", "type": "any"}],
                       "outputs": [{"name": "sent", "type": "any"}],
                       "tests": []}],
                     pid="p_ntd2")
        p["edges"] = [{"src": "n_f", "dst": "n_s"}]
        with mock.patch.object(executor, "_execute",
                               return_value=({"contacts": [],
                                              "sheet_rows": [{"r": 1}]}, False)), \
             mock.patch.object(env_checks, "check_all_nodes", return_value=[]), \
             mock.patch.object(env_checks, "check_node", return_value=[]):
            res = executor.run_workflow(p, {})
        if res.get("run_id"):
            run_state.finish(res["run_id"])
        self.assertEqual(res.get("reason"), "nothing-to-do", res)
        self.assertIn("contacts", " ".join(res["verdict"]["nothing_to_do"]))

    def test_iterated_input_names_is_conservative(self):
        code = ("people = read_input('people')\n"
                "ref = read_input('ref')\n"
                "for p in people:\n"
                "    pass\n"
                "kept = [x for x in read_input('direct')]\n"
                "for i, y in enumerate(sorted(people)):\n"
                "    pass\n")
        self.assertEqual(executor.iterated_input_names(code),
                         {"people", "direct"})
        self.assertEqual(executor.iterated_input_names("not python ("), set())

    def test_empty_output_halts_at_the_producer_with_the_exit(self):
        from unittest import mock

        from agent import node_tools
        from runtime import env_checks
        port = {"name": "people", "type": "any"}
        node_tools.derive_port_schema(
            port, [{"name": "a", "url": "u"}, {"name": "b", "url": "v"}])
        p = _workflow([{"id": "n_b", "name": "Browse results", "type": "code",
                       "config": {"code": "x"}, "inputs": [],
                       "outputs": [port], "tests": []}],
                     pid="p_empty1")
        with mock.patch.object(executor, "_execute",
                               return_value=({"people": []}, False)), \
             mock.patch.object(env_checks, "check_all_nodes", return_value=[]), \
             mock.patch.object(env_checks, "check_node", return_value=[]):
            res = executor.run_workflow(p, {})
        if res.get("run_id"):
            run_state.finish(res["run_id"])
        self.assertEqual(res.get("reason"), "empty-output", res)
        self.assertEqual(res.get("empty_ports"), ["people"])
        self.assertIn("found nothing at all",
                      " ".join(res["verdict"]["empty_output"]))

        port["may_be_empty"] = True
        port["schema"].pop("x-nonempty", None)
        with mock.patch.object(executor, "_execute",
                               return_value=({"people": []}, False)), \
             mock.patch.object(env_checks, "check_all_nodes", return_value=[]), \
             mock.patch.object(env_checks, "check_node", return_value=[]):
            res2 = executor.run_workflow(p, {})
        if res2.get("run_id"):
            run_state.finish(res2["run_id"])
        self.assertEqual(res2.get("status"), "completed", res2)

    def test_a_fix_turn_chains_the_failing_runs_inputs(self):
        from agent import node_tools
        from runtime import corpus
        p = _workflow([{"id": "n_w", "name": "Write back", "type": "code",
                       "config": {"code": "x"}, "inputs": [{"name": "rows", "type": "any"}],
                       "outputs": [{"name": "n", "type": "number"}], "tests": []}],
                     pid="p_issuechain")
        cid = corpus.record_failure("p_issuechain", "n_w", {"rows": [{"a": 1}, {"a": 2}]},
                                    run_id="run_x", verdict={"thrown": "KeyError"},
                                    cause="node threw: KeyError")
        p["tickets"] = [{"id": "tkt_1a2b3c", "status": "in-progress", "node_id": "n_w",
                         "run_id": "run_x", "case_id": cid, "reason": "node threw: KeyError",
                         "verdict": {}, "notes": ""}]

        p["plan"] = {"status": "built", "nodes": [{"name": "Write back", "type": "code",
                                                   "id": "n_w"}]}
        r = node_tools.tool_run_cell(p, "Write back", "write_output('n', len(read_input('rows')))",
                                     {"rows": "$issue"})
        self.assertIn("while fixing an issue", r.get("error", ""))

        turnstate.of(p).fix_issue_id = "tkt_1a2b3c"
        p["plan"]["fix_approved_ts"] = 1.0
        r = node_tools.tool_run_cell(p, "Write back", "write_output('n', len(read_input('rows')))",
                                     {"rows": "$issue"})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["output"], {"n": 2})

class SharedStateWallsTest(unittest.TestCase):
    def test_materialised_inputs_live_in_a_private_folder(self):
        import tempfile
        from pathlib import Path
        from runtime import capability
        p1 = Path(capability._materialise("data", b"one"))
        p2 = Path(capability._materialise("data", b"two"))
        self.assertEqual(p1, p2)
        self.assertEqual(p1.read_bytes(), b"two")
        self.assertNotEqual(p1.parent, Path(tempfile.gettempdir()) / "cryogram_inputs")
        self.assertTrue(p1.parent.name.startswith("cryogram_inputs_"))

    def test_the_sandbox_cwd_is_fresh_per_call_and_gone_after(self):
        import config
        from runtime import sandbox
        base = config.DATA_DIR / "sandbox_tmp"
        sandbox.run("open('leak.txt', 'w').write('x')\nwrite_output('ok', True)", None, {}, {})
        out = sandbox.run("import os\nwrite_output('seen', os.path.exists('leak.txt'))", None, {}, {})
        self.assertFalse(out["seen"])
        self.assertEqual([d.name for d in base.glob("step_*")], [])

    def test_a_resume_buffer_belongs_to_one_workflow(self):
        from runtime import run_state
        node = {"id": "n_w", "name": "W", "type": "code",
                "config": {"code": "write_output('y', 1)"}, "inputs": [],
                "outputs": [{"name": "y", "type": "number"}], "tests": []}
        p = _workflow([node], pid="p_resume_mine")
        run_state.start("p_resume_other", "run_foreign_1")
        try:
            with self.assertRaises(ValueError):
                executor.run_workflow(p, {}, run_id="run_foreign_1")
        finally:
            run_state.finish("run_foreign_1")

    def test_cell_progress_is_per_workflow(self):
        from agent import node_tools
        f, g = (lambda n: None), (lambda n: None)
        node_tools.set_cell_progress("p_prog_a", f)
        node_tools.set_cell_progress("p_prog_b", g)
        self.assertIs(node_tools.cell_progress("p_prog_a"), f)
        node_tools.set_cell_progress("p_prog_b", None)
        self.assertIs(node_tools.cell_progress("p_prog_a"), f)
        self.assertIsNone(node_tools.cell_progress("p_prog_b"))
        node_tools.set_cell_progress("p_prog_a", None)

class PathBlockedPauseTest(unittest.TestCase):
    def _node(self, folder):
        return {"id": "n_p", "name": "read it", "type": "code",
                "config": {"code": "def read_it(message):\n"
                                   "    import os\n"
                                   f"    return message + ':' + str(len(os.listdir({folder!r})))\n"},
                "inputs": [{"name": "message", "type": "text"}],
                "outputs": [{"name": "seen", "type": "text"}], "tests": []}

    def test_refused_then_allowed(self):
        import os
        import tempfile
        folder = tempfile.mkdtemp(prefix="cryo_home_")

        home = os.path.expanduser("~")
        p = _workflow([self._node(home)], pid="p_pathblk1")
        res = executor.run_workflow(p, {"message": "hi"})
        self.assertEqual((res["status"], res["reason"]), ("halted", "path-blocked"), res)
        self.assertTrue(any("path blocked" in v for v in res["verdict"]["environment"]))
        p["path_allowlist"] = [home]
        res2 = executor.run_workflow(p, {"message": "hi"})
        self.assertEqual(res2["status"], "completed", res2)
        self.assertTrue(res2["outputs"]["n_p"]["seen"].startswith("hi:"))

class PartialProgressTest(unittest.TestCase):
    CODE = "items = read_input('items')\ndone = checkpoints()\nfor it in items:\n    k = str(it['id'])\n    if k in done:\n        continue\n    if it['id'] >= 3:\n        raise RuntimeError('boom at ' + k)\n    checkpoint(k, {'id': it['id'], 'ok': True})\nwrite_output('sent', list(checkpoints().values()))\n"

    def _node(self):
        return {"id": "n_pi", "name": "Send each", "type": "code",
                "config": {"code": self.CODE,
                           "per_item": {"input": "items", "key": "id"}},
                "inputs": [{"name": "items", "type": "any"}],
                "outputs": [{"name": "sent", "type": "any"}], "tests": []}

    def test_stop_records_progress_and_proceed_forks_the_run(self):
        from storage import deliverables
        items = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
        p = _workflow([self._node()], pid="p_partial1")
        res = executor.run_workflow(p, {"items": items})
        self.assertEqual(res["status"], "halted", res)
        self.assertTrue(res["reason"].startswith("node threw"))
        prog = res["progress"]
        self.assertEqual((prog["done"], prog["of"], prog["remaining"]), (2, 4, 2))
        self.assertEqual(prog["done_keys"], ["1", "2"])
        self.assertEqual(prog["source"], {"entry": True})
        run_id = res["run_id"]
        self.assertEqual(run_state.load(run_id)["progress"]["done"], 2)

        forked = run_state.fork_partial(run_id)
        self.assertIsNotNone(forked)
        a, b = forked
        self.assertEqual(a, run_id)
        self.assertTrue(b and b != a)
        ra, rb = run_state.load(a), run_state.load(b)
        self.assertEqual([x["id"] for x in ra["entry_inputs"]["items"]], [1, 2])
        self.assertEqual([x["id"] for x in rb["entry_inputs"]["items"]], [3, 4])
        self.assertEqual(rb["status"], "halted")
        self.assertEqual(rb["halted_at"], "n_pi")
        self.assertEqual(rb["started"], ra["started"])
        self.assertEqual(ra["partial"], {"half": "done", "count": 2, "of": 4, "sibling": b})
        self.assertEqual(rb["partial"], {"half": "remaining", "count": 2, "of": 4, "sibling": a})
        self.assertNotIn("progress", ra)

        res2 = executor.run_workflow(p, {}, run_id=a)
        self.assertEqual(res2["status"], "completed", res2)
        self.assertEqual([x["id"] for x in res2["outputs"]["n_pi"]["sent"]], [1, 2])
        rows = {r["run_id"]: r for r in deliverables.list_runs("p_partial1")}
        self.assertEqual(rows[a]["status"], "completed")
        self.assertEqual(rows[a]["partial"]["half"], "done")
        self.assertEqual(rows[b]["status"], "halted")
        self.assertEqual(rows[b]["partial"]["half"], "remaining")

        self.assertIsNone(run_state.fork_partial(b))
        run_state.finish(b)

    def test_no_done_items_means_nothing_to_proceed_with(self):
        p = _workflow([self._node()], pid="p_partial2")
        res = executor.run_workflow(p, {"items": [{"id": 5}]})
        self.assertEqual(res["status"], "halted")
        self.assertEqual(res["progress"]["done"], 0)
        self.assertIsNone(run_state.fork_partial(res["run_id"]))
        run_state.finish(res["run_id"])

    def test_a_run_level_override_feeds_the_input_first(self):
        run_state.start("p_ov", "run_ov", {"items": [1, 2, 3]})
        rec = run_state.load("run_ov")
        rec["overrides"] = {"items": [3]}
        run_state._save(rec)
        node = {"id": "n", "inputs": [{"name": "items", "type": "any"}]}
        inputs, missing = executor._gather_inputs(node, "run_ov", {"items": [1, 2, 3]})
        self.assertEqual(inputs, {"items": [3]})
        run_state.finish("run_ov")

class EverySendRefusedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        cls.mode = {"status": 429}

        class H(BaseHTTPRequestHandler):
            hits = []

            def log_message(self, *a):
                pass

            def _answer(self, status):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                H.hits.append(self.path)

                if self.path in cls.mode:
                    return self._answer(cls.mode[self.path])
                status = cls.mode["status"]
                if isinstance(status, list):
                    sends = [h for h in H.hits if h not in cls.mode]
                    status = status[min(len(sends) - 1, len(status) - 1)]
                self._answer(status)

            def do_GET(self):
                H.hits.append(self.path)
                self._answer(cls.mode.get(self.path, 404))
        cls.handler = H
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _node(self, nid, before: str = ""):
        code = (
            "import json, urllib.request\n"
            + before
            + "out = []\n"
            "for row in ['a', 'b', 'c']:\n"
            f"    req = urllib.request.Request('http://127.0.0.1:{self.port}/send', data=json.dumps({{'row': row}}).encode(), headers={{'content-type': 'application/json'}}, method='POST')\n"
            "    try:\n"
            "        with urllib.request.urlopen(req, timeout=5) as r:\n"
            "            out.append({'row': row, 'status': r.status})\n"
            "    except urllib.error.HTTPError as e:\n"
            "        out.append({'row': row, 'status': e.code})\n"
            "write_output('result', out)\n")
        return {"id": nid, "name": "Send the rows", "type": "connector",
                "read_only": False, "approval_suppressed": True,
                "config": {"code": code, "domains": ["127.0.0.1"]}, "inputs": [],
                "outputs": [{"name": "result", "type": "list"}]}

    def setUp(self):
        self.handler.hits.clear()

    def test_every_send_answering_429_pauses_as_rate_limited_and_is_not_fired(self):
        self.mode["status"] = 429
        p = _workflow([self._node("n_429")], pid="p_send429")
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "halted", res)
        self.assertEqual(res["reason"], "rate-limited")
        self.assertFalse(run_state.has_fired(res["run_id"], "n_429"))

        self.mode["status"] = 200
        res2 = executor.run_workflow(p, {}, run_id=res["run_id"])
        self.assertEqual(res2["status"], "completed", res2)
        run_state.finish(res2["run_id"])

    def test_every_send_answering_500_stops_with_the_statuses_named(self):
        self.mode["status"] = 500
        p = _workflow([self._node("n_500")], pid="p_send500")
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "halted", res)
        self.assertEqual(res["reason"], "node threw: SendRefused")
        self.assertIn("HTTP 500", json.dumps(res.get("verdict")))
        self.assertIn("3 of 3", json.dumps(res.get("verdict")))
        self.assertFalse(run_state.has_fired(res["run_id"], "n_500"))
        run_state.finish(res["run_id"])

    def test_a_kind_of_send_partly_refused_pauses_and_counts_as_fired(self):
        self.mode["status"] = [200, 200, 503]
        p = _workflow([self._node("n_part")], pid="p_sendpart")
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "halted", res)
        self.assertEqual(res["reason"], "send-unverified")
        self.assertIn("1 of 3 sends", json.dumps(res.get("verdict")))
        self.assertIn("HTTP 503", json.dumps(res.get("verdict")))
        self.assertTrue(run_state.has_fired(res["run_id"], "n_part"))
        run_state.finish(res["run_id"])

    def test_a_token_call_that_succeeded_does_not_excuse_a_refused_loop(self):
        self.mode["status"] = 429
        self.mode["/token"] = 200
        try:
            token = (f"urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:{self.port}/token', "
                     "data=b'{}', headers={'content-type': 'application/json'}, method='POST'), timeout=5).read()\n")
            p = _workflow([self._node("n_tok", before=token)], pid="p_sendtok")
            res = executor.run_workflow(p, {})
            self.assertEqual(res["status"], "halted", res)
            self.assertEqual(res["reason"], "rate-limited")
            self.assertFalse(run_state.has_fired(res["run_id"], "n_tok"))
            self.assertIn("/send", json.dumps(res.get("verdict")))
            self.assertNotIn("/token was refused", json.dumps(res.get("verdict")))
            run_state.finish(res["run_id"])
        finally:
            self.mode.pop("/token", None)

    def _ledger_node(self, nid):
        code = (
            "import json, urllib.request\n"
            "rows = read_input('rows')\n"
            "done = checkpoints()\n"
            "for row in rows:\n"
            "    if row['id'] in done:\n"
            "        continue\n"
            f"    req = urllib.request.Request('http://127.0.0.1:{self.port}/send', data=json.dumps(row).encode(), headers={{'content-type': 'application/json'}}, method='POST')\n"
            "    with urllib.request.urlopen(req, timeout=5) as r:\n"
            "        checkpoint(row['id'], {'id': row['id'], 'status': r.status})\n"
            "write_output('result', list(checkpoints().values()))\n")
        return {"id": nid, "name": "Send each row", "type": "connector",
                "read_only": False, "approval_suppressed": True,
                "config": {"code": code, "domains": ["127.0.0.1"],
                           "per_item": {"input": "rows", "key": "id"}},
                "inputs": [{"name": "rows", "type": "list"}],
                "outputs": [{"name": "result", "type": "list"}]}

    def test_a_step_with_a_ledger_is_re_entered_and_skips_the_rows_that_went(self):
        self.mode["status"] = [200, 200, 503]
        rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        p = _workflow([self._ledger_node("n_led")], pid="p_ledger")
        res = executor.run_workflow(p, {"rows": rows})
        self.assertEqual(res["status"], "halted", res)
        self.assertEqual(res["reason"], "send-unverified")
        self.assertEqual((res["progress"]["done"], res["progress"]["of"]), (2, 3))
        self.assertTrue(run_state.has_fired(res["run_id"], "n_led"))
        self.handler.hits.clear()
        self.mode["status"] = 200
        res2 = executor.run_workflow(p, {}, run_id=res["run_id"])
        self.assertEqual(res2["status"], "completed", res2)
        self.assertEqual(len(self.handler.hits), 1)
        self.assertEqual([x["id"] for x in res2["outputs"]["n_led"]["result"]], ["a", "b", "c"])
        run_state.finish(res2["run_id"])

    def test_go_on_with_the_ones_that_went_splits_the_run_only_then(self):
        self.mode["status"] = [200, 503, 503]
        rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        p = _workflow([self._ledger_node("n_fork")], pid="p_ledgerfork")
        res = executor.run_workflow(p, {"rows": rows})
        self.assertEqual(res["status"], "halted", res)
        forked = run_state.fork_partial(res["run_id"])
        self.assertIsNotNone(forked)
        done_id, rest_id = forked
        self.handler.hits.clear()
        self.mode["status"] = 200
        res2 = executor.run_workflow(p, {}, run_id=done_id)
        self.assertEqual(res2["status"], "completed", res2)
        self.assertEqual(self.handler.hits, [])
        self.assertEqual([x["id"] for x in res2["outputs"]["n_fork"]["result"]], ["a"])
        self.assertEqual(run_state.load(rest_id)["status"], "halted")
        run_state.finish(rest_id)

    def test_run_the_whole_step_again_forgets_the_ledger_and_sends_everything(self):
        self.mode["status"] = [200, 200, 503]
        rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        p = _workflow([self._ledger_node("n_reset")], pid="p_ledgerreset")
        res = executor.run_workflow(p, {"rows": rows})
        self.assertEqual(res["status"], "halted", res)
        run_state.reset_step(res["run_id"], "n_reset")
        self.assertFalse(run_state.has_fired(res["run_id"], "n_reset"))
        self.assertNotIn("progress", run_state.load(res["run_id"]))
        self.handler.hits.clear()
        self.mode["status"] = 200
        res2 = executor.run_workflow(p, {}, run_id=res["run_id"])
        self.assertEqual(res2["status"], "completed", res2)
        self.assertEqual(len(self.handler.hits), 3)
        run_state.finish(res2["run_id"])

    def test_a_fired_step_with_no_ledger_is_still_refused_on_resume(self):
        self.mode["status"] = [200, 200, 503]
        p = _workflow([self._node("n_blind")], pid="p_blind")
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "halted", res)
        self.assertTrue(run_state.has_fired(res["run_id"], "n_blind"))
        res2 = executor.run_workflow(p, {}, run_id=res["run_id"])
        self.assertEqual(res2["reason"], "fired-unverified")
        run_state.finish(res2["run_id"])

    def test_a_lookup_by_get_answering_404_is_not_a_refused_send(self):
        self.mode["status"] = 200
        lookup = (f"try:\n    urllib.request.urlopen('http://127.0.0.1:{self.port}/lookup/7', timeout=5)\n"
                  "except urllib.error.HTTPError:\n    pass\n")
        p = _workflow([self._node("n_get", before=lookup)], pid="p_sendget")
        res = executor.run_workflow(p, {})
        self.assertEqual(res["status"], "completed", res)
        from storage import deliverables
        runs = deliverables.list_runs("p_sendget")
        step = next(e for e in runs[0].get("steps") or [] if e.get("node") == "n_get")
        notes = step.get("notes") or []
        self.assertTrue(any("3 of 3 answered 2xx" in n for n in notes), notes)
