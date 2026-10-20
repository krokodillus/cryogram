import unittest
from unittest.mock import patch

from tests import _bootstrap
from agent import node_tools
from runtime import executor, output_contract, port_checks, sandbox

class OutputContractTest(unittest.TestCase):
    def test_named_outputs_remain_named_with_extras_and_captures(self):
        for kind in ("code", "connector", "browser", "ai"):
            with self.subTest(kind=kind):
                node = {"type": kind, "outputs": [{"name": "count", "type": "number"}]}
                raw = {"count": 3, "diagnostic": "extra", "$capture": {"har": "ref"}}
                trial = output_contract.normalize(node["outputs"], raw, named=True)
                run = executor._shape_output(node, raw, named=True)
                self.assertEqual(run, trial)
                self.assertEqual(run["count"], 3)
                self.assertEqual(port_checks.check_ports(node["outputs"], run, require_all=True), [])

    def test_missing_explicit_record_output_is_not_wrapped_into_a_false_success(self):
        node = {"type": "code", "outputs": [{"name": "record", "type": "record"}]}
        got = executor._shape_output(node, {"diagnostic": "only this"}, named=True)
        problems = port_checks.check_ports(node["outputs"], got, require_all=True)
        self.assertEqual(problems[0]["port"], "record")
        self.assertIn("not produced", problems[0]["problem"])

    def test_legacy_function_results_remain_supported(self):
        for raw, port, expected in [
            (3, "count", {"count": 3}),
            ({"id": 1}, "record", {"record": {"id": 1}}),
            ({"count": 3, "extra": 4}, "count", {"count": 3, "extra": 4}),
            ([1, 2], "items", {"items": [1, 2]}),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(output_contract.normalize([{"name": port}], raw), expected)

    def test_multiple_and_optional_ports_keep_their_validation(self):
        ports = [{"name": "count", "type": "number"},
                 {"name": "note", "type": "text", "optional": True}]
        self.assertEqual(port_checks.check_ports(ports, output_contract.normalize(
            ports, {"count": 3, "extra": True}, named=True), require_all=True), [])
        self.assertTrue(port_checks.check_ports(ports, output_contract.normalize(
            ports, {"note": "no count"}, named=True), require_all=True))

    def test_real_cell_and_real_run_use_the_same_declared_value(self):
        code = "write_output('count', len(read_input('items')))\nwrite_output('diagnostic', 'extra')"
        node = {"id": "n_count", "name": "Count items", "type": "code",
                "inputs": [{"name": "items", "type": "list"}],
                "outputs": [{"name": "count", "type": "number"}],
                "config": {"code": code}, "tests": []}
        workflow = _bootstrap.workflow([node], pid="v3_output_parity")
        workflow["plan"] = {"nodes": [{**node, "code": code}]}

        with patch.object(node_tools, "_proposal_first", return_value=None):
            cell = node_tools.tool_run_cell(workflow, name=node["name"], code=code,
                                            inputs={"items": [1, 2, 3]})
        self.assertTrue(cell.get("ok"), cell)
        run = executor.run_workflow(workflow, {"items": [1, 2, 3]})
        self.assertEqual(run["status"], "completed", run)
        self.assertEqual(run["outputs"]["n_count"]["count"], cell["output"]["count"])

    def test_sandbox_marks_function_write_output_and_raw_return_differently(self):
        for code, named in [
            ("def entry():\n    write_output('debug', 1)\n", True),
            ("def entry():\n    return {'id': 1}\n", False),
        ]:
            report = {}
            sandbox.run(code, "entry", {}, {}, report=report)
            self.assertEqual(report["outputs_named"], named)

    def test_real_run_rejects_missing_named_output(self):
        node = {"id": "n_missing", "name": "Build record", "type": "code",
                "inputs": [], "outputs": [{"name": "record", "type": "record"}],
                "config": {"code": "write_output('debug', {'id': 1})"}, "tests": []}
        workflow = _bootstrap.workflow([node], pid="v3_missing_named")
        run = executor.run_workflow(workflow, {})
        self.assertNotEqual(run["status"], "completed", run)
        self.assertIn("not produced", str(run))

if __name__ == "__main__":
    unittest.main()
