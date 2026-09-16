# Tests: safe_eval - the AST interpreter for AI-authored expressions (when edges, criteria, test asserts)
from __future__ import annotations

import unittest

from tests import _bootstrap

from runtime import safe_eval
from runtime import verifier

FNS = verifier.FUNCTIONS

class GrammarTest(unittest.TestCase):
    def test_ordinary_predicates_evaluate(self):
        cases = [
            ("total > 0", {"total": 5}, True),
            ("subtotal + tax == total", {"subtotal": 8, "tax": 2, "total": 10}, True),
            ("len(headlines) == 3", {"headlines": ["a", "b", "c"]}, True),
            ("status in ('ok', 'sent')", {"status": "sent"}, True),
            ("rows[0]['amount'] > 0", {"rows": [{"amount": 2}]}, True),
            ("not missing", {"missing": False}, True),
            ("'x' if flag else 'y'", {"flag": True}, "x"),
            ("rate > 10 and rate < 12", {"rate": 11.06}, True),
            ("is_positive(total)", {"total": 3}, True),
            ("items[1:3]", {"items": [0, 1, 2, 3]}, [1, 2]),
        ]
        for expr, scope, want in cases:
            self.assertEqual(safe_eval.evaluate(expr, scope, FNS), want, expr)

    def test_comprehensions_and_generators(self):
        self.assertTrue(safe_eval.evaluate(
            "all(r['amount'] > 0 for r in rows)",
            {"rows": [{"amount": 1}, {"amount": 2}]}, FNS))
        self.assertEqual(safe_eval.evaluate(
            "[x * 2 for x in items if x > 1]", {"items": [1, 2, 3]}, FNS), [4, 6])
        self.assertEqual(safe_eval.evaluate(
            "{k: v for k, v in pairs}", {"pairs": [("a", 1)]}, FNS), {"a": 1})

    def test_check_mirrors_evaluate(self):
        for expr in ("total > 0", "len(xs) == 2", "any(x for x in xs)"):
            self.assertIsNone(safe_eval.check(expr, set(FNS)), expr)
        for expr in ("().__class__", "import os", "", "x.y > 1"):
            self.assertIsNotNone(safe_eval.check(expr, set(FNS)), expr)

        problem = safe_eval.check("isinstance(summary, dict)", set(FNS))
        self.assertIn("isinstance", problem)
        self.assertIn("len", problem)
        with self.assertRaises(safe_eval.UnsafeExpression):
            safe_eval.evaluate("isinstance(summary, dict)", {"summary": {}}, FNS)

class AdversarialTest(unittest.TestCase):
    def test_attribute_traversal_refused(self):
        for expr in (
            "().__class__.__bases__[0].__subclasses__()",
            "(1).__class__",
            "x.__dict__", "value.__globals__",
            "'{0.__class__}'.format(x)",
        ):
            with self.assertRaises(safe_eval.UnsafeExpression, msg=expr):
                safe_eval.evaluate(expr, {"x": 1, "value": 1}, FNS)

    def test_whitelisted_methods_work(self):
        self.assertTrue(safe_eval.evaluate(
            "status.startswith('ok')", {"status": "ok-sent"}, FNS))
        self.assertEqual(safe_eval.evaluate(
            "rec.get('amount')", {"rec": {"amount": 3}}, FNS), 3)
        self.assertEqual(safe_eval.evaluate("'-'.join(xs)", {"xs": ["a", "b"]},
                                            FNS), "a-b")

    def test_unknown_and_dunder_calls_refused(self):
        with self.assertRaises(safe_eval.UnsafeExpression):
            safe_eval.evaluate("__import__('os')", {}, FNS)
        with self.assertRaises(safe_eval.UnsafeExpression):
            safe_eval.evaluate("eval('1')", {}, FNS)
        self.assertNotIn("__import__", FNS)
        self.assertNotIn("eval", FNS)
        self.assertNotIn("getattr", FNS)

    def test_lambdas_walrus_fstrings_refused(self):
        for expr in ("(lambda: 1)()", "(y := 5)", "f'{1}'"):
            with self.assertRaises(safe_eval.UnsafeExpression, msg=expr):
                safe_eval.evaluate(expr, {}, FNS)

    def test_pow_bomb_capped(self):
        with self.assertRaises(safe_eval.UnsafeExpression):
            safe_eval.evaluate("10 ** 10 ** 10", {}, FNS)
        self.assertEqual(safe_eval.evaluate("2 ** 10", {}, FNS), 1024)

class VerifierIntegrationTest(unittest.TestCase):
    def test_verifier_routes_through_safe_eval(self):
        ok, err = verifier._evaluate_expr("total > 0", {"total": 1})
        self.assertTrue(ok)
        self.assertIsNone(err)

        ok, err = verifier._evaluate_expr(
            "().__class__.__bases__[0].__subclasses__()", {})
        self.assertFalse(ok)
        self.assertIn("UnsafeExpression", err)

    def test_gate_symmetry_with_runtime(self):
        from agent import node_tools
        self.assertTrue(node_tools.expr_ok("len(items) > 0"))
        self.assertFalse(node_tools.expr_ok("x.__class__"))
        self.assertFalse(node_tools.expr_ok("output.headlines"))
        self.assertFalse(node_tools.expr_ok("isinstance(x, dict)"))
        self.assertTrue(node_tools.expr_ok("is_number(total) and len(rows) > 0"))
        self.assertIn("isinstance", " ".join(node_tools.assert_problems(["isinstance(x, dict)"])))

        _, errs = node_tools.clean_criteria([{"expr": "isinstance(x, dict)"}], "criterion")
        self.assertTrue(errs and "isinstance" in errs[0] and "len" in errs[0], errs)

if __name__ == "__main__":
    unittest.main()
