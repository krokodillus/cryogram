# Tests: a file given to an AI step reaches the model whole or the run stops before the call, and the step knows it sends a file
from __future__ import annotations

import unittest
from unittest import mock

from tests import _bootstrap

from agent import context, orchestrator, steps
from runtime import capability, env_checks, executor
from storage import blobstore, model_catalog, secrets_store, settings

PDF_BYTES = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n2 0 obj << /Type /Page >> endobj\n"

def _register():
    settings.update({"providers": [
        {"id": "anthropic", "name": "Anthropic", "adapter": "anthropic", "auth": "api-key",
         "use": "workflow", "enabled": True, "key_name": "F2M_A", "tags": [],
         "models": [{"name": "claude-sonnet-5", "cost": 6, "quality": 8}]},
        {"id": "p_txt", "name": "Text Box", "adapter": "openai", "auth": "api-key",
         "use": "workflow", "enabled": True, "key_name": "F2M_T", "tags": [],
         "endpoint": "http://localhost:11434/v1",
         "models": [{"name": "textonly", "cost": 1, "quality": 3}]}],
        "master_ai": {"model": "claude-sonnet-5"}})
    for k in ("F2M_A", "F2M_T"):
        secrets_store.set_secret(k, "k", secrets_store.OWNER_APP)

def _forget():
    settings.update({"providers": [], "master_ai": {"model": ""}})

def _pdf():
    return blobstore.put(PDF_BYTES, "application/pdf", {"name": "invoice.pdf"},
                         owner=blobstore.OWNER_APP)

class DerefTest(unittest.TestCase):
    def setUp(self):
        _register()

    def tearDown(self):
        _forget()

    def test_a_pdf_goes_whole_to_a_model_that_reads_it(self):
        out, atts = capability._deref_blobs({"doc": _pdf(), "n": 3}, {"model": "claude-sonnet-5"})
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["mime"], "application/pdf")
        self.assertEqual(atts[0]["data"], PDF_BYTES)
        self.assertEqual(out["doc"], "(the file for doc is attached)")
        self.assertEqual(out["n"], 3)

    def test_a_pdf_to_a_text_only_model_stops_before_the_call(self):
        with self.assertRaises(capability.FileUnsupported) as cm:
            capability._deref_blobs({"doc": _pdf()}, {"model": "textonly", "provider_id": "p_txt"})
        msg = str(cm.exception)
        self.assertIn("gives a PDF to textonly", msg)
        self.assertIn("Text Box does not read PDFs", msg)
        self.assertIn("step's panel", msg)
        self.assertEqual(cm.exception.detail["kind"], "pdf")

    def test_a_file_no_model_reads_says_so(self):
        ref = blobstore.put(b"PK\x03\x04 fake docx",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            {"name": "brief.docx"}, owner=blobstore.OWNER_APP)
        with self.assertRaises(capability.FileUnsupported) as cm:
            capability._deref_blobs({"doc": ref}, {"model": "claude-sonnet-5"})
        self.assertIn("no AI model reads Word or PowerPoint files directly", str(cm.exception))

    def test_the_vendors_size_limit_is_checked_before_the_call(self):
        with mock.patch.dict(model_catalog.PROVIDERS["anthropic"], {"request_bytes": 40, "pdf_pages": 600}):
            with self.assertRaises(capability.FileUnsupported) as cm:
                capability._deref_blobs({"doc": _pdf()}, {"model": "claude-sonnet-5"})
        self.assertIn("the most Anthropic takes in one call is", str(cm.exception))

    def test_the_vendors_page_limit_is_checked_before_the_call(self):
        with mock.patch.dict(model_catalog.PROVIDERS["anthropic"], {"pdf_pages": 0}):
            with mock.patch.object(capability, "_pdf_pages", lambda data: 700):
                with mock.patch.dict(model_catalog.PROVIDERS["anthropic"], {"pdf_pages": 600}):
                    with self.assertRaises(capability.FileUnsupported) as cm:
                        capability._deref_blobs({"doc": _pdf()}, {"model": "claude-sonnet-5"})
        self.assertIn("about 700 pages", str(cm.exception))

    def test_the_page_count_reads_the_files_own_page_objects(self):
        self.assertEqual(capability._pdf_pages(PDF_BYTES), 1)
        self.assertIsNone(capability._pdf_pages(b"nothing"))

    def test_ai_call_reports_the_unsupported_file_without_calling_out(self):
        called = {"n": 0}
        import providers

        def never(*a, **k):
            called["n"] += 1
            return {"text": "x"}
        with mock.patch.object(providers, "call", never):
            out = capability.ai_call("read", {"model": "textonly", "provider_id": "p_txt"},
                                     {"doc": _pdf()}, output_ports=[{"name": "total", "type": "number"}])
        self.assertEqual(called["n"], 0)
        self.assertTrue(out.get("_unparsed"))
        self.assertEqual(out.get("error_kind"), "file-unsupported")
        self.assertIn("does not read PDFs", out["_text"])

class RunTest(unittest.TestCase):
    def setUp(self):
        _register()

    def tearDown(self):
        _forget()

    def _node(self, model, provider_id=""):
        return {"id": "n_read", "name": "Read the invoice", "type": "ai",
                "config": {"prompt": "total?", "model": {"model": model, "provider_id": provider_id,
                                                          "temperature": 0}},
                "inputs": [{"name": "doc", "type": "file", "file_kind": "pdf"}],
                "outputs": [{"name": "total", "type": "number"}], "tests": []}

    def test_the_run_halts_with_the_reason_and_the_sentence(self):
        res = executor.run_isolated(self._node("textonly", "p_txt"), {"doc": _pdf()}, workflow_id="p_f2m")
        self.assertEqual(res["status"], "halted")
        self.assertEqual(res["reason"], "file-unsupported")
        self.assertIn("Text Box does not read PDFs", res["verdict"]["file"][0])

    def test_the_pre_run_check_names_it_from_the_steps_own_inputs(self):
        node = self._node("textonly", "p_txt")
        steps.refresh_sends_file(node)
        problems = env_checks.check_all_nodes({"nodes": [node]})
        self.assertEqual(len(problems), 1)
        self.assertIn('The step "Read the invoice" gives a PDF to textonly', problems[0])
        node_ok = self._node("claude-sonnet-5")
        steps.refresh_sends_file(node_ok)
        self.assertEqual(env_checks.check_all_nodes({"nodes": [node_ok]}), [])

    def test_the_fix_brief_carries_the_reason_and_the_two_remedies(self):
        brief = orchestrator._file_unsupported_brief(
            {"reason": "file-unsupported", "verdict": {"file": ["This step gives a PDF to textonly, and Text Box does not read PDFs."]}})
        self.assertIn("THIS IS A MODEL CHOICE, NOT A DEFECT", brief)
        self.assertIn("Text Box does not read PDFs", brief)
        self.assertIn("investigate nothing", brief)
        self.assertIn("1. Change this step's model", brief)
        self.assertIn("2. Add a code step before it", brief)
        self.assertEqual(orchestrator._file_unsupported_brief({"reason": "no-progress"}), "")

class SendsFileTest(unittest.TestCase):
    def test_an_ai_step_with_a_file_port_knows_its_kinds(self):
        node = {"id": "n", "type": "ai", "config": {},
                "inputs": [{"name": "doc", "type": "file", "file_kind": "PDF"},
                           {"name": "shot", "type": "file", "file_kind": "png"},
                           {"name": "n", "type": "number"}]}
        steps.refresh_sends_file(node)
        self.assertEqual(node["config"]["sends_file"], {"kinds": ["image", "pdf"]})

    def test_a_file_port_without_a_kind_is_other_and_a_step_without_files_has_no_flag(self):
        node = {"id": "n", "type": "ai", "config": {}, "inputs": [{"name": "doc", "type": "file"}]}
        steps.refresh_sends_file(node)
        self.assertEqual(node["config"]["sends_file"], {"kinds": ["other"]})
        node["inputs"] = [{"name": "n", "type": "number"}]
        steps.refresh_sends_file(node)
        self.assertNotIn("sends_file", node["config"])

    def test_a_code_step_never_carries_the_flag(self):
        node = {"id": "n", "type": "code", "config": {"sends_file": {"kinds": ["pdf"]}},
                "inputs": [{"name": "doc", "type": "file", "file_kind": "pdf"}]}
        steps.refresh_sends_file(node)
        self.assertNotIn("sends_file", node["config"])

    def test_the_kind_words_the_model_reaches_for(self):
        self.assertEqual(steps.canon_file_kind("xlsx"), "spreadsheet")
        self.assertEqual(steps.canon_file_kind("Word"), "document")
        self.assertEqual(steps.canon_file_kind("jpeg"), "image")
        self.assertEqual(steps.canon_file_kind("zip"), "other")
        self.assertEqual(steps.canon_file_kind(""), "")

    def test_the_declared_ports_keep_the_kind_and_refresh_the_flag(self):
        from agent import node_tools
        p = _bootstrap.workflow([{"id": "n_ai", "name": "Read", "type": "ai",
                                  "config": {"prompt": "p", "model": {"model": "", "temperature": 0}},
                                  "inputs": [], "outputs": [], "tests": []}], pid="p_f2m_ports")
        r = node_tools._set_declared_io(p, "n_ai",
                                        [{"name": "doc", "type": "file", "file_kind": "PDF"}],
                                        [{"name": "total", "type": "number"}])
        self.assertTrue(r.get("ok"), r)
        node = next(n for n in p["nodes"] if n["id"] == "n_ai")
        self.assertEqual(node["inputs"][0]["file_kinds"], ["pdf"])
        self.assertEqual(node["config"]["sends_file"], {"kinds": ["pdf"]})

class AttachedFileTest(unittest.TestCase):
    def test_the_driving_message_names_its_attached_file(self):
        from agent import transcript
        p = _bootstrap.workflow([], pid="p_f2m_attached")
        transcript.append_message(p, "user", "Read the attached one.", files=["invoice.pdf"])
        body = context.build(p, "Read the attached one.")[0]["content"]
        self.assertIn("user: Read the attached one.\n[attached and stored as a sample: invoice.pdf", body)
        self.assertIn("read_sample reads it", body)

    def test_an_earlier_message_keeps_its_file_names_in_the_replay(self):
        from agent import transcript
        p = _bootstrap.workflow([], pid="p_f2m_attached2")
        transcript.append_message(p, "user", "Here is last month's.", files=[{"name": "july.pdf", "ref": "blob:x"}])
        transcript.append_message(p, "assistant", "Got it.")
        body = context.build(p, "And this month's?")[0]["content"]
        self.assertIn("user: Here is last month's.\n[attached and stored as a sample: july.pdf", body)
        self.assertNotIn("And this month's?\n[attached", body)

class ModelsLineTest(unittest.TestCase):
    def tearDown(self):
        _forget()

    def test_the_builder_agent_is_told_what_each_model_reads(self):
        _register()
        line = context._models_line({})
        self.assertIn("claude-sonnet-5 (Anthropic, reads PDFs and images)", line)
        self.assertIn("textonly (Text Box, text only)", line)

if __name__ == "__main__":
    unittest.main()
