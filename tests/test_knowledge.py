# Tests: the Knowledge tab's content: index.json must be valid, every listed topic file must exist with real content, and no orphan topic files may sit unlisted (the tab IS the documentation surface - a page that exists but is unreachable is documentation rot)
from __future__ import annotations

import json
import unittest

from tests import _bootstrap

import config

KNOWLEDGE = config.FRONTEND_DIR / "knowledge"

class KnowledgeStructureTest(unittest.TestCase):
    def test_index_lists_real_nonempty_files(self):
        topics = json.loads((KNOWLEDGE / "index.json").read_text())
        self.assertGreaterEqual(len(topics), 4)
        ids = [t["id"] for t in topics]
        self.assertEqual(len(ids), len(set(ids)), "duplicate topic ids")
        for t in topics:
            f = KNOWLEDGE / t["file"]
            self.assertTrue(f.exists(), f"{t['id']}: {t['file']} missing")
            body = f.read_text()
            self.assertGreater(len(body), 300, f"{t['id']}: suspiciously thin")
            self.assertTrue(body.lstrip().startswith("# "),
                            f"{t['id']}: must open with a # title")

    def test_no_orphan_topic_files(self):
        topics = json.loads((KNOWLEDGE / "index.json").read_text())
        listed = {t["file"] for t in topics}
        on_disk = {f.name for f in KNOWLEDGE.glob("*.md")}
        self.assertEqual(on_disk - listed, set(),
                         "topic files not reachable from index.json")

    def test_plain_language(self):
        banned = ("node_tools", "corpus.db", "run_state", "executor.py",
                  "save_plan", "structure_hash", "deopt",
                  "freeze", "codify", "corpus", "graduation", "seam",
                  "ticket", "receipt")
        for f in KNOWLEDGE.glob("*.md"):
            body = f.read_text().lower()
            for term in banned:
                self.assertNotIn(term, body, f"{f.name} leaks internal term {term!r}")

    def test_index_html_visible_text_is_plain(self):
        import re
        html = (KNOWLEDGE.parent / "index.html").read_text()
        visible = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
        visible = re.sub(r"<[^>]+>", " ", visible).lower()
        for term in ("freeze", "codify", "corpus", "explorer", "auditor",
                     "graduation", "deopt", "node_tools"):
            self.assertNotIn(term, visible,
                             f"index.html shows internal term {term!r}")

if __name__ == "__main__":
    unittest.main()

class NavLabelTest(unittest.TestCase):
    def test_named_tabs_exist(self):
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "frontend"
        html = (root / "index.html").read_text()
        labels = set(re.findall(r'data-ptab="[^"]*"[^>]*>([^<]+)</button>', html))
        labels |= {"Admin", "Knowledge", "Environments"}
        for md in (root / "knowledge").glob("*.md"):
            for name in re.findall(r"the \*\*([A-Za-z ]+)\*\* tab", md.read_text()):
                self.assertIn(name, labels,
                              f"{md.name} points at a tab called {name!r} "
                              f"that no longer exists ({sorted(labels)})")
