# Tests: the model call - each vendor's request shape with a file in its own form, and the catalogue's facts about what a model reads
from __future__ import annotations

import json
import unittest
import urllib.request
from unittest import mock

from tests import _bootstrap

from modelcall import adapters
from storage import model_catalog

PDF = {"name": "invoice", "mime": "application/pdf", "data": b"%PDF-1.7 fake"}
PNG = {"name": "shot", "mime": "image/png", "data": b"\x89PNG fake"}

class _Resp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return json.dumps(self._p).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

def _capture(reply: dict):
    seen = {}

    def fake(req, timeout=None, context=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["body"] = json.loads(req.data.decode())
        return _Resp(reply)
    return seen, fake

class FactsTest(unittest.TestCase):
    def test_every_catalogue_row_resolves_with_its_own_reads(self):
        for key, rows in model_catalog.CATALOG.items():
            for row in rows:
                f = model_catalog.facts({"id": key}, row["name"])
                self.assertEqual(f["reads"], row.get("reads", []), (key, row["name"]))
                self.assertEqual(f["source"], "catalogue")

    def test_an_unknown_model_gets_the_providers_default(self):
        self.assertEqual(model_catalog.facts({"id": "gemini", "adapter": "openai"}, "gemini-9")["reads"],
                         ["pdf", "image"])

        self.assertEqual(model_catalog.facts({"id": "p_mine", "adapter": "openai"}, "some-model")["reads"], [])

    def test_a_stored_rows_reads_win(self):
        p = {"id": "p_local", "adapter": "openai", "models": [{"name": "llava", "reads": ["image"]}]}
        f = model_catalog.facts(p, "llava")
        self.assertEqual((f["reads"], f["source"], f["adapter"]), (["image"], "row", "compatible"))

    def test_the_route_is_the_catalogues_not_the_records_adapter(self):
        self.assertEqual(model_catalog.facts({"id": "gemini", "adapter": "openai"}, "x")["adapter"], "gemini")
        self.assertEqual(model_catalog.catalog_key({"id": "p_x", "adapter": "anthropic"}), "anthropic")
        self.assertEqual(model_catalog.catalog_key({"id": "p_x", "adapter": "openai"}), "compatible")
        self.assertEqual(model_catalog.catalog_key({}), "compatible")

    def test_the_words_for_what_a_model_reads(self):
        self.assertEqual(model_catalog.reads_phrase(model_catalog.facts({"id": "anthropic"}, "claude-sonnet-5")),
                         "reads PDFs and images")
        self.assertEqual(model_catalog.reads_phrase(model_catalog.facts(
            {"id": "p_mine", "adapter": "openai", "models": [{"name": "llava", "reads": ["image"]}]}, "llava")),
                         "reads images")
        self.assertEqual(model_catalog.reads_phrase(model_catalog.facts({"id": "p_mine", "adapter": "openai"}, "x")),
                         "text only")

    def test_the_vendor_limits_ride_the_provider_key(self):
        f = model_catalog.facts({"id": "openai"}, "gpt-5.6-terra")
        self.assertEqual((f["request_bytes"], f["file_bytes"], f["pdf_pages"]),
                         (32 * 1024 * 1024, 50 * 1024 * 1024, 100))
        self.assertIsNone(model_catalog.facts({"id": "p_x", "adapter": "openai"}, "m")["request_bytes"])

class PartsTest(unittest.TestCase):
    def test_anthropic_blocks(self):
        blocks = adapters.attachment_blocks([PDF, PNG])
        self.assertEqual([b["type"] for b in blocks], ["document", "image"])
        self.assertEqual(blocks[0]["source"]["media_type"], "application/pdf")

    def test_openai_own_api_sends_a_pdf_as_a_file_part(self):
        parts = adapters.openai_parts([PDF, PNG], files_ok=True)
        self.assertEqual([p["type"] for p in parts], ["file", "image_url"])
        self.assertEqual(parts[0]["file"]["filename"], "invoice.pdf")
        self.assertTrue(parts[0]["file"]["file_data"].startswith("data:application/pdf;base64,"))
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_the_compatible_shape_never_sends_a_pdf(self):
        parts = adapters.openai_parts([PDF, PNG], files_ok=False)
        self.assertEqual([p["type"] for p in parts], ["image_url"])

    def test_gemini_inline_data(self):
        parts = adapters.gemini_parts([PDF])
        self.assertEqual(parts[0]["inline_data"]["mime_type"], "application/pdf")

class RequestShapeTest(unittest.TestCase):
    def test_anthropic_call_with_a_pdf(self):
        seen, fake = _capture({"stop_reason": "tool_use", "usage": {"input_tokens": 3, "output_tokens": 2},
                               "content": [{"type": "tool_use", "input": {"total": 5}}]})
        with mock.patch.object(urllib.request, "urlopen", fake):
            res = adapters.call("anthropic", "claude-x", "k", "read it", [PDF],
                                schema={"type": "object", "properties": {"total": {"type": "number"}}})
        self.assertEqual(res["structured"], {"total": 5})
        self.assertEqual(res["usage"], {"in": 3, "out": 2})
        content = seen["body"]["messages"][0]["content"]
        self.assertEqual([c["type"] for c in content], ["document", "text"])
        self.assertEqual(seen["body"]["tool_choice"], {"type": "tool", "name": "emit_output"})

    def test_a_model_that_refuses_temperature_is_asked_again_without_it(self):
        import io as _io
        bodies = []
        reply = {"stop_reason": "tool_use", "usage": {"input_tokens": 1, "output_tokens": 1},
                 "content": [{"type": "tool_use", "input": {"total": 7}}]}

        def fake(req, timeout=None, context=None):
            body = json.loads(req.data.decode())
            bodies.append(body)
            if "temperature" in body:
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                             _io.BytesIO(b'{"error":{"message":"`temperature` is deprecated for this model."}}'))
            return _Resp(reply)
        adapters._NO_TEMPERATURE.discard("claude-new-5")
        told = []
        with mock.patch.object(urllib.request, "urlopen", fake), \
                mock.patch.object(adapters, "remember_no_temperature", told.append):
            res = adapters.call("anthropic", "claude-new-5", "k", "read it", [], temperature=0,
                                schema={"type": "object", "properties": {"total": {"type": "number"}}})
            self.assertEqual(res["structured"], {"total": 7})
            self.assertEqual(len(bodies), 2)
            self.assertIn("temperature", bodies[0])
            self.assertNotIn("temperature", bodies[1])
            res2 = adapters.call("anthropic", "claude-new-5", "k", "read it", [], temperature=0,
                                 schema={"type": "object", "properties": {"total": {"type": "number"}}})
            self.assertEqual(len(bodies), 3)
            self.assertNotIn("temperature", bodies[2])
            self.assertEqual(res2["structured"], {"total": 7})
        self.assertEqual(told, ["claude-new-5"])
        adapters._NO_TEMPERATURE.discard("claude-new-5")

    def test_openai_call_with_a_pdf_and_an_image(self):
        seen, fake = _capture({"choices": [{"finish_reason": "stop",
                                            "message": {"content": '{"total": 5}'}}],
                               "usage": {"prompt_tokens": 7, "completion_tokens": 1}})
        with mock.patch.object(urllib.request, "urlopen", fake):
            res = adapters.call("openai", "gpt-x", "k", "read it", [PDF, PNG],
                                schema={"type": "object"})
        self.assertEqual(res["structured"], {"total": 5})
        self.assertTrue(seen["url"].startswith("https://api.openai.com/v1/chat/completions"))
        content = seen["body"]["messages"][0]["content"]
        self.assertEqual([c["type"] for c in content], ["file", "image_url", "text"])
        self.assertEqual(seen["body"]["response_format"]["type"], "json_schema")

    def test_the_compatible_call_keeps_the_image_and_drops_the_pdf(self):
        seen, fake = _capture({"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]})
        with mock.patch.object(urllib.request, "urlopen", fake):
            res = adapters.call("compatible", "m", "k", "look", [PDF, PNG], endpoint="http://localhost:11434/v1")
        self.assertEqual(res["text"], "ok")
        self.assertEqual([c["type"] for c in seen["body"]["messages"][0]["content"]], ["image_url", "text"])

    def test_a_compatible_call_without_files_sends_plain_text(self):
        seen, fake = _capture({"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]})
        with mock.patch.object(urllib.request, "urlopen", fake):
            adapters.call("compatible", "m", "k", "hello", [])
        self.assertEqual(seen["body"]["messages"][0]["content"], "hello")

    def test_gemini_call_with_a_pdf_and_a_schema(self):
        seen, fake = _capture({"candidates": [{"finishReason": "STOP", "content": {"parts": [
                                   {"text": '{"total": 5}'}]}}],
                               "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4}})
        schema = {"type": "object", "properties": {"total": {"type": "number", "x-file": True}}}
        with mock.patch.object(urllib.request, "urlopen", fake):
            res = adapters.call("gemini", "gemini-3.5-flash", "k", "read it", [PDF], schema=schema,
                                endpoint="https://generativelanguage.googleapis.com/v1beta/openai/")
        self.assertEqual(res["structured"], {"total": 5})
        self.assertEqual(res["usage"], {"in": 9, "out": 4})

        self.assertEqual(seen["url"],
                         "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent")
        self.assertEqual(seen["headers"].get("x-goog-api-key"), "k")
        parts = seen["body"]["contents"][0]["parts"]
        self.assertIn("inline_data", parts[0])
        self.assertEqual(parts[-1], {"text": "read it"})
        gen = seen["body"]["generationConfig"]
        self.assertEqual(gen["responseMimeType"], "application/json")
        self.assertNotIn("x-file", json.dumps(gen["responseJsonSchema"]))

    def test_a_gemini_answer_cut_off_is_a_classified_failure(self):
        seen, fake = _capture({"candidates": [{"finishReason": "MAX_TOKENS",
                                               "content": {"parts": [{"text": '{"tot'}]}}]})
        with mock.patch.object(urllib.request, "urlopen", fake):
            res = adapters.call("gemini", "g", "k", "x", [], schema={"type": "object"})
        self.assertEqual(res.get("error_kind"), "max-tokens")
        self.assertNotIn("structured", res)

    def test_a_custom_gemini_base_is_honoured(self):
        self.assertEqual(adapters._gemini_url("g", "https://proxy.example/v1beta"),
                         "https://proxy.example/v1beta/models/g:generateContent")

if __name__ == "__main__":
    unittest.main()
