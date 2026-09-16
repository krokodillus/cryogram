# Tests for the inline limit: a result rides whole under the engine's limit and comes back as its outline past it
from __future__ import annotations

import unittest
from unittest import mock

from tests import _bootstrap

from agent import previews

class InlineLimitTest(unittest.TestCase):
    def test_values_under_the_limit_pass_untouched(self):
        big = {"ok": True, "html": "A" * 20_000, "rows": [{"i": i} for i in range(500)]}
        self.assertIs(previews.guard(big, "claude"), big)
        self.assertIs(previews.guard(big, "codex"), big)

    def test_each_engine_has_its_own_limit_under_its_own_cut(self):
        self.assertLess(previews.inline_limit("claude"), 25_000 * 3)
        self.assertLess(previews.inline_limit("codex"), 12_000 * 3)
        self.assertGreater(previews.inline_limit("claude"), previews.inline_limit("codex"))

        self.assertEqual(previews.inline_limit(""), min(previews.INLINE_CHARS.values()))
        self.assertEqual(previews.inline_limit("other"), min(previews.INLINE_CHARS.values()))

    def test_past_the_limit_the_outline_is_complete_and_says_how_to_open_the_whole(self):
        with mock.patch.dict(previews.INLINE_CHARS, {"codex": 1000}), \
                mock.patch.object(previews, "OUTLINE_STRING_CHARS", 100), \
                mock.patch.object(previews, "OUTLINE_LIST_CHARS", 200):
            v = {"ok": True, "html": "B" * 5000,
                 "rows": [{"name": f"n{i}", "tags": ["x"]} for i in range(50)],
                 "small": [1, 2, 3], "note": "kept"}
            out = previews.guard(v, "codex")
        self.assertIn("window_note", out)
        self.assertIn("read_earlier_result", out["window_note"])
        self.assertIn("$recorded", out["window_note"])
        self.assertEqual(out["ok"], True)
        self.assertEqual(out["note"], "kept")
        self.assertEqual(out["small"], [1, 2, 3])
        self.assertTrue(out["html"].startswith("B" * 100))
        self.assertIn("5,000 characters in total", out["html"])
        rows = out["rows"]
        self.assertEqual(rows["$list"], 50)
        self.assertEqual(rows["first"], {"name": "n0", "tags": ["x"]})
        self.assertIn("name", str(rows["shape"]))

    def test_the_outline_is_far_smaller_than_the_limit(self):
        v = {"rows": [{"name": f"n{i}", "text": "t" * 500} for i in range(2000)],
             "html": "H" * 500_000, "meta": {"k": list(range(10_000))}}
        out = previews.guard(v, "codex")
        self.assertLess(previews.size(out), previews.inline_limit("codex") // 4)

    def test_sketch_keeps_small_values_and_outlines_big_ones(self):
        self.assertEqual(previews.sketch({"a": 1}), {"a": 1})
        with mock.patch.object(previews, "OUTLINE_LIST_CHARS", 50):
            out = previews.sketch([{"k": i} for i in range(100)])
        self.assertEqual(out["$list"], 100)

if __name__ == "__main__":
    unittest.main()
