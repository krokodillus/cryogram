# Tests: the integration seam - route matching, isolation between integrations, and an empty folder behaving as no folder at all
from __future__ import annotations

import sys
import types
import unittest

from tests import _bootstrap

import extensions

def _install(name, register):
    mod = types.ModuleType(f"extensions.{name}")
    mod.register = register
    sys.modules[f"extensions.{name}"] = mod
    return mod

def _load(**registers):
    extensions.reset()
    for name, fn in registers.items():
        _install(name, fn)
    names = sorted(registers)

    class _Found:
        def __init__(self, n):
            self.name = n

    import pkgutil
    real = pkgutil.iter_modules
    pkgutil.iter_modules = lambda _paths=None: [_Found(n) for n in names]
    try:
        extensions.load()
    finally:
        pkgutil.iter_modules = real

class SeamIsEmptyByDefault(unittest.TestCase):
    def setUp(self):
        extensions.reset()
        self.addCleanup(extensions.reset)

    def test_no_integrations_means_no_routes_no_page_no_answer(self):
        _load()
        self.assertEqual(extensions.ids(), [])
        self.assertEqual(extensions.manifest(), [])
        self.assertIsNone(extensions.dispatch("GET", ["api", "anything"]))
        self.assertIsNone(extensions.first("run_values", {}))
        extensions.fire("start")
        self.assertEqual(extensions.FAILED, [])

class RouteMatching(unittest.TestCase):
    def setUp(self):
        extensions.reset()
        self.addCleanup(extensions.reset)

    def test_exact_and_wildcard_patterns(self):
        def register(reg):
            reg.route("GET", ("api", "demo", "status"), lambda ctx: (200, {"at": "status"}))
            reg.route("GET", ("api", "demo", "*", "runs"), lambda ctx: (200, {"w": ctx.wildcards}))
        _load(demo=register)

        handler, caught = extensions.dispatch("GET", ["api", "demo", "status"])
        self.assertEqual(caught, [])
        self.assertEqual(handler(None), (200, {"at": "status"}))

        handler, caught = extensions.dispatch("GET", ["api", "demo", "w7", "runs"])
        self.assertEqual(caught, ["w7"])

    def test_method_and_length_must_match(self):
        _load(demo=lambda reg: reg.route("GET", ("api", "demo"), lambda ctx: (200, {})))
        self.assertIsNone(extensions.dispatch("POST", ["api", "demo"]))
        self.assertIsNone(extensions.dispatch("GET", ["api", "demo", "extra"]))
        self.assertIsNone(extensions.dispatch("GET", ["api"]))

    def test_a_wildcard_matches_one_segment_only(self):
        _load(demo=lambda reg: reg.route("GET", ("api", "*"), lambda ctx: (200, {})))
        self.assertIsNotNone(extensions.dispatch("GET", ["api", "one"]))
        self.assertIsNone(extensions.dispatch("GET", ["api", "one", "two"]))

class OneBadIntegrationIsContained(unittest.TestCase):
    def setUp(self):
        extensions.reset()
        self.addCleanup(extensions.reset)

    def test_an_import_that_raises_leaves_the_others_loaded(self):
        def good(reg):
            reg.route("GET", ("api", "good"), lambda ctx: (200, {"ok": True}))

        def bad(reg):
            raise RuntimeError("no config")

        _load(aaa_bad=bad, zzz_good=good)
        self.assertEqual(extensions.ids(), ["zzz_good"])
        self.assertIsNotNone(extensions.dispatch("GET", ["api", "good"]))
        self.assertEqual(len(extensions.FAILED), 1)
        self.assertIn("no config", extensions.FAILED[0][1])

    def test_a_half_finished_registration_leaves_nothing_behind(self):
        def half(reg):
            reg.route("GET", ("api", "half"), lambda ctx: (200, {}))
            raise RuntimeError("gave up")
        _load(half=half)
        self.assertIsNone(extensions.dispatch("GET", ["api", "half"]))
        self.assertEqual(extensions.ids(), [])

    def test_a_route_another_integration_answers_is_refused(self):
        def one(reg):
            reg.route("GET", ("api", "same"), lambda ctx: (200, {"who": "one"}))

        def two(reg):
            reg.route("GET", ("api", "same"), lambda ctx: (200, {"who": "two"}))

        _load(aaa_one=one, bbb_two=two)
        self.assertEqual(extensions.ids(), ["aaa_one"])
        handler, _ = extensions.dispatch("GET", ["api", "same"])
        self.assertEqual(handler(None), (200, {"who": "one"}))
        self.assertIn("already answered", extensions.FAILED[0][1])

    def test_the_same_route_twice_in_one_integration_is_refused(self):
        def twice(reg):
            reg.route("GET", ("api", "dup"), lambda ctx: (200, {}))
            reg.route("GET", ("api", "dup"), lambda ctx: (200, {}))
        _load(twice=twice)
        self.assertEqual(extensions.ids(), [])
        self.assertIn("registered twice", extensions.FAILED[0][1])

class HooksAndValues(unittest.TestCase):
    def setUp(self):
        extensions.reset()
        self.addCleanup(extensions.reset)

    def test_a_raising_hook_does_not_stop_the_next_one(self):
        seen = []
        _load(aaa=lambda reg: reg.hook("start", lambda: (_ for _ in ()).throw(RuntimeError("x"))),
              bbb=lambda reg: reg.hook("start", lambda: seen.append("ran")))
        extensions.fire("start")
        self.assertEqual(seen, ["ran"])

    def test_first_real_answer_wins_and_none_falls_through(self):
        _load(aaa=lambda reg: reg.value("v", lambda p: None),
              bbb=lambda reg: reg.value("v", lambda p: {"got": p}))
        self.assertEqual(extensions.first("v", 3), {"got": 3})

    def test_no_answer_at_all_is_none(self):
        _load(aaa=lambda reg: reg.value("v", lambda p: None))
        self.assertIsNone(extensions.first("v", 1))

class Pages(unittest.TestCase):
    def setUp(self):
        extensions.reset()
        self.addCleanup(extensions.reset)

    def test_web_gives_a_sidebar_entry_pointing_at_its_own_files(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        (d / "main.js").write_text("export function render() {}")
        _load(demo=lambda reg: reg.web("Demo", d, icon="&#9729;"))
        item = extensions.manifest()[0]
        self.assertEqual(item["id"], "demo")
        self.assertEqual(item["title"], "Demo")
        self.assertEqual(item["module"], "/ext/demo/main.js")
        self.assertEqual(item["route"], "#/demo")
        self.assertEqual(extensions.asset("demo", "main.js"), (d / "main.js").resolve())

    def test_an_asset_outside_the_integrations_own_folder_is_refused(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp()) / "web"
        d.mkdir()
        (d / "main.js").write_text("")
        _load(demo=lambda reg: reg.web("Demo", d))
        self.assertIsNone(extensions.asset("demo", "../escape.txt"))
        self.assertIsNone(extensions.asset("demo", "missing.js"))
        self.assertIsNone(extensions.asset("nobody", "main.js"))

class EveryAnswer(unittest.TestCase):
    def setUp(self):
        extensions.reset()
        self.addCleanup(extensions.reset)

    def test_all_the_real_answers_come_back_in_load_order(self):
        _load(aaa=lambda reg: reg.value("v", lambda p: ["a"]),
              bbb=lambda reg: reg.value("v", lambda p: None),
              ccc=lambda reg: reg.value("v", lambda p: ["c"]))
        self.assertEqual(extensions.every("v", 1), [["a"], ["c"]])

    def test_one_that_raises_is_skipped_and_the_rest_still_answer(self):
        def boom(_p):
            raise RuntimeError("no")
        _load(aaa=lambda reg: reg.value("v", boom),
              bbb=lambda reg: reg.value("v", lambda p: ["b"]))
        self.assertEqual(extensions.every("v", 1), [["b"]])

    def test_nothing_installed_answers_with_an_empty_list(self):
        _load()
        self.assertEqual(extensions.every("v", 1), [])

class OpeningQuestions(unittest.TestCase):
    def setUp(self):
        extensions.reset()
        self.addCleanup(extensions.reset)

    @staticmethod
    def _asks(*keys):
        qs = [{"key": k, "question": f"{k}?", "options": ["Yes", "No"]}
              for k in keys]
        return lambda reg: reg.value("opening_questions", lambda p: qs)

    def test_nothing_is_owed_with_no_integration_installed(self):
        from agent import opening
        _load()
        self.assertIsNone(opening.owed({"id": "p1", "transcript": []}))

    def test_the_first_owed_question_is_raised_as_an_answerable_card(self):
        from agent import opening, transcript
        _load(aaa=self._asks("one", "two"))
        workflow = {"id": "p1", "transcript": []}
        q = opening.owed(workflow)
        self.assertEqual(q["key"], "one")
        items = opening.raise_card(workflow, q)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "request")
        self.assertEqual(items[0]["payload"]["options"], ["Yes", "No"])
        self.assertIn(items[0], transcript.open_requests(workflow))

    def test_a_question_already_shown_is_never_shown_again(self):
        from agent import opening
        _load(aaa=self._asks("one"))
        workflow = {"id": "p1", "transcript": []}
        opening.raise_card(workflow, opening.owed(workflow))

        self.assertIsNone(opening.owed(workflow))

    def test_the_answer_is_read_back_out_of_the_transcript(self):
        from agent import opening, transcript
        _load(aaa=self._asks("one"))
        workflow = {"id": "p1", "transcript": []}
        items = opening.raise_card(workflow, opening.owed(workflow))
        self.assertIsNone(opening.answer(workflow, "one"))
        transcript.append_answer(workflow, items[0]["iid"], "Yes")
        self.assertEqual(opening.answer(workflow, "one"), "Yes")
        self.assertIsNone(opening.answer(workflow, "never asked"))

    def test_a_question_with_no_key_or_no_words_is_dropped(self):
        from agent import opening
        _load(aaa=lambda reg: reg.value("opening_questions", lambda p: [
            {"question": "no key?"}, {"key": "silent"}, "not even a dict",
            {"key": "real", "question": "yes?"}]))
        self.assertEqual(opening.owed({"id": "p1", "transcript": []})["key"],
                         "real")

if __name__ == "__main__":
    unittest.main()
