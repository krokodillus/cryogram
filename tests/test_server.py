# Tests for Server seams: the request _guard (DNS rebinding / CSRF), static-file traversal containment, malformed-JSON 400s and the FRONTEND_DIR pin: the security/reliability layer that had no tests at all
from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from tests import _bootstrap

import config
import server as server_mod

def _start() -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]

class ClosedConnectionTest(unittest.TestCase):
    def _errors_of(self, exc):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        srv = server_mod._Server.__new__(server_mod._Server)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                raise exc
            except Exception:
                srv.handle_error(object(), ("127.0.0.1", 1))
        return out.getvalue(), err.getvalue()

    def test_a_closed_connection_prints_nothing(self):
        for exc in (BrokenPipeError(32, "Broken pipe"), ConnectionResetError(54, "reset"),
                    ConnectionAbortedError()):
            self.assertEqual(self._errors_of(exc), ("", ""))

    def test_a_real_failure_says_so_in_one_line_then_the_traceback(self):
        out, err = self._errors_of(KeyError("nope"))
        self.assertTrue(out.startswith("Cryogram could not answer a request: KeyError: 'nope'"))
        self.assertIn("Traceback", err)

    def test_the_server_bound_is_the_quiet_one(self):
        srv = server_mod._bind("127.0.0.1", 0)
        try:
            self.assertIsInstance(srv, server_mod._Server)
        finally:
            srv.server_close()

class ConsoleActionTest(unittest.TestCase):
    def test_maps_commands_and_aliases(self):
        for line in ("/restart", "restart", " reload ", "R\n"):
            self.assertEqual(server_mod._console_action(line), "restart", line)
        for line in ("/quit", "exit", "STOP", "q"):
            self.assertEqual(server_mod._console_action(line), "quit", line)
        for line in ("/help", "?", "commands"):
            self.assertEqual(server_mod._console_action(line), "help", line)

    def test_ignores_unknown_and_blank(self):
        for line in ("", "  ", "hello", "/build", "restartx"):
            self.assertIsNone(server_mod._console_action(line), line)

class ServerGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json", **(headers or {})}
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_foreign_host_rejected(self):
        status, data = self._req("GET", "/api/workflows",
                                 headers={"Host": "attacker.example"})
        self.assertEqual(status, 403)
        self.assertIn(b"forbidden host", data)

    def test_foreign_origin_rejected_on_writes(self):
        status, data = self._req("POST", "/api/workflows", body="{}",
                                 headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn(b"forbidden origin", data)

        status, _ = self._req("GET", "/api/workflows",
                              headers={"Origin": "http://localhost:1234"})
        self.assertEqual(status, 200)

    def test_static_traversal_contained(self):
        for path in ("/../backend/config.py", "/..%2f..%2fbackend/config.py",
                     "/%2e%2e/backend/secrets_store.py"):
            status, data = self._req("GET", path)
            self.assertIn(status, (403, 404), path)
            self.assertNotIn(b"VERSION", data, path)

    def test_frontend_dir_is_resolved(self):
        self.assertEqual(config.FRONTEND_DIR, config.FRONTEND_DIR.resolve())

    def test_malformed_json_answers_400(self):
        status, data = self._req("POST", "/api/workflows", body="{not json")
        self.assertEqual(status, 400)
        self.assertIn(b"malformed JSON", data)
        status, data = self._req("PUT", "/api/settings", body="[broken")
        self.assertEqual(status, 400)

    def test_a_prebuilt_provider_says_where_its_key_comes_from(self):
        from storage import settings
        status, data = self._req("GET", "/api/settings")
        self.assertEqual(status, 200)
        view = json.loads(data)
        urls = {p["id"]: p.get("key_url") for p in view.get("providers") or []}
        for pid in settings.DEFAULT_PROVIDER_IDS:
            if pid in urls:
                self.assertTrue(str(urls[pid]).startswith("https://"), pid)
        self.assertEqual(settings.KEY_URLS["gemini"], "https://aistudio.google.com/api-keys")
        self.assertEqual(len(settings.KEY_URLS), len(settings.DEFAULT_PROVIDER_IDS))

    def test_oversize_body_answers_400(self):
        status, data = self._req("POST", "/api/workflows", body="{}",
                                 headers={"Content-Length":
                                          str(server_mod.Handler._MAX_BODY + 1)})
        self.assertEqual(status, 400)
        self.assertIn(b"too large", data)

    def test_delete_workflow_removes_it(self):
        status, data = self._req("POST", "/api/workflows",
                                 body=json.dumps({"name": "To delete"}))
        self.assertEqual(status, 200)
        pid = json.loads(data)["id"]
        status, _ = self._req("GET", f"/api/workflows/{pid}")
        self.assertEqual(status, 200)
        status, data = self._req("DELETE", f"/api/workflows/{pid}")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data).get("ok"))
        status, _ = self._req("GET", f"/api/workflows/{pid}")
        self.assertEqual(status, 404)
        status, data = self._req("DELETE", f"/api/workflows/{pid}")
        self.assertEqual(status, 404)

class OwnedSecretRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data or b"{}")

    def test_workflow_secret_route_and_app_route_do_not_share(self):
        from storage import secrets_store, store
        store.save({"id": "p_own_r1", "name": "one", "nodes": [], "edges": [],
                    "chat": [], "variables": [{"name": "tok", "secret": True,
                                               "value": None, "persistent": True}]})
        try:
            status, d = self._req("PUT", "/api/workflows/p_own_r1/secrets/tok", {"value": "wf-val"})
            self.assertEqual(status, 200, d)
            self.assertEqual(secrets_store.get_secret("tok", secrets_store.workflow_owner("p_own_r1")), "wf-val")
            self.assertFalse(secrets_store.has_secret("tok", secrets_store.OWNER_APP))
            status, _ = self._req("PUT", "/api/secrets/tok", {"value": "app-val"})
            self.assertEqual(status, 200)
            self.assertEqual(secrets_store.get_secret("tok", secrets_store.OWNER_APP), "app-val")
            self.assertEqual(secrets_store.get_secret("tok", secrets_store.workflow_owner("p_own_r1")), "wf-val")
            status, _ = self._req("PUT", "/api/workflows/p_nope_r1/secrets/tok", {"value": "x"})
            self.assertEqual(status, 404)
        finally:
            secrets_store.delete_secret("tok", secrets_store.OWNER_APP)
            secrets_store.delete_secret("tok", secrets_store.workflow_owner("p_own_r1"))
            store.delete_workflow("p_own_r1")

    def test_deleting_a_secret_variable_touches_only_its_own_row(self):
        from storage import secrets_store, store
        step = {"id": "n1", "name": "S", "type": "code", "config": {"code": "x = 1"},
                "inputs": [], "outputs": [], "tests": []}
        for pid in ("p_own_d1", "p_own_d2"):
            store.save({"id": pid, "name": pid, "nodes": [step], "edges": [], "chat": [],
                        "variables": [{"name": "shared_key", "secret": True,
                                       "value": None, "persistent": True}]})
            secrets_store.set_secret("shared_key", f"val-{pid}", secrets_store.workflow_owner(pid))
        try:
            status, d = self._req("DELETE", "/api/workflows/p_own_d1/variables/shared_key")
            self.assertEqual(status, 200, d)
            self.assertFalse(secrets_store.has_secret("shared_key", secrets_store.workflow_owner("p_own_d1")))
            self.assertEqual(secrets_store.get_secret("shared_key", secrets_store.workflow_owner("p_own_d2")),
                             "val-p_own_d2")
        finally:
            for pid in ("p_own_d1", "p_own_d2"):
                secrets_store.delete_secret("shared_key", secrets_store.workflow_owner(pid))
                store.delete_workflow(pid)

if __name__ == "__main__":
    unittest.main()

class NodeModelRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from storage import store
        cls.pid = "p_modelroute"
        store.save({"id": cls.pid, "nodes": [
            {"id": "n_code", "name": "step", "type": "code",
             "config": {"code": "x=1"}, "inputs": [], "outputs": [], "tests": []},
            {"id": "n_ai", "name": "judge", "type": "ai",
             "config": {"prompt": "p", "model": {"model": "", "temperature": 0}},
             "inputs": [], "outputs": [{"name": "out", "type": "text"}],
             "tests": []}],
            "edges": [], "variables": [], "chat": []})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, body=json.dumps(body),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data or b"{}")

    def test_non_ai_step_refused(self):
        status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_code/model",
                               {"model": "whatever"})
        self.assertEqual(status, 400)
        self.assertIn("AI step", d["error"])

    def test_unready_model_refused(self):
        status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/model",
                               {"model": "model-that-is-not-set-up"})
        self.assertEqual(status, 400)
        self.assertIn("ready workflow models", d["error"])

    def test_temperature_and_token_limit_change_through_the_same_route(self):
        from storage import settings, secrets_store, store
        import config
        settings.update({"providers": [
            {"id": "p_tt", "name": "Tune", "adapter": "anthropic", "auth": "api-key",
             "use": "workflow", "key_name": "TT_KEY", "tags": [],
             "models": [{"name": "tune-model"}, {"name": "picky", "no_temperature": True}]}]})
        secrets_store.set_secret("TT_KEY", "k", secrets_store.OWNER_APP)
        wf = store.load(self.pid)
        wf["plan"] = {"nodes": [{"id": "n_ai", "name": "judge", "type": "ai",
                                 "prompt": "p", "model": "", "temperature": 0.0}], "edges": []}
        store.save(wf)
        try:
            status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/model",
                                   {"model": "tune-model", "provider_id": "p_tt"})
            self.assertEqual(status, 200, d)
            status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/model", {"temperature": 0.4})
            self.assertEqual(status, 200, d)
            status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/model", {"max_tokens": 8000})
            self.assertEqual(status, 200, d)
            node = next(n for n in store.load(self.pid)["nodes"] if n["id"] == "n_ai")
            self.assertEqual(node["config"]["model"]["temperature"], 0.4)
            self.assertEqual(node["config"]["max_tokens"], 8000)
            plan_step = store.load(self.pid)["plan"]["nodes"][0]
            self.assertEqual((plan_step["model"], plan_step["temperature"], plan_step["max_tokens"]),
                             ("tune-model", 0.4, 8000))
            status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/model", {"max_tokens": ""})
            self.assertEqual(status, 200, d)
            node = next(n for n in store.load(self.pid)["nodes"] if n["id"] == "n_ai")
            self.assertNotIn("max_tokens", node["config"])
            self.assertNotIn("max_tokens", store.load(self.pid)["plan"]["nodes"][0])
            status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/model", {"temperature": 5})
            self.assertEqual(status, 400)
            self.assertIn("between 0 and 1", d["error"])
            status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/model",
                                   {"model": "picky", "provider_id": "p_tt"})
            self.assertEqual(status, 200, d)
            status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/model", {"temperature": 0.2})
            self.assertEqual(status, 400)
            self.assertIn("takes no temperature", d["error"])
            self.assertEqual(config.AI_MAX_TOKENS, 32_768)
        finally:
            settings.update({"providers": []})

    def test_the_change_carries_the_provider(self):
        from storage import settings, secrets_store, store
        settings.update({"providers": [
            {"id": "p_r1", "name": "Route One", "adapter": "anthropic",
             "auth": "api-key", "use": "workflow", "key_name": "R1_KEY",
             "tags": [], "models": [{"name": "route-model"}]},
            {"id": "p_r2", "name": "Route Two", "adapter": "anthropic",
             "auth": "api-key", "use": "workflow", "key_name": "R2_KEY",
             "tags": [], "models": [{"name": "route-model"}]}]})
        for k in ("R1_KEY", "R2_KEY"):
            secrets_store.set_secret(k, "k", secrets_store.OWNER_APP)
        try:
            status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/model",
                                   {"model": "route-model", "provider_id": "p_r2"})
            self.assertEqual(status, 200, d)
            ref = next(n for n in store.load(self.pid)["nodes"]
                       if n["id"] == "n_ai")["config"]["model"]
            self.assertEqual((ref["model"], ref["provider_id"]), ("route-model", "p_r2"))
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", "/api/models/workflow")
            rows = json.loads(c.getresponse().read())["models"]
            c.close()
            twin = [m for m in rows if m["name"] == "route-model"]
            self.assertEqual({m["provider_id"] for m in twin}, {"p_r1", "p_r2"})
            self.assertTrue(all(m["ambiguous"] and m["ready"] for m in twin))
        finally:
            settings.update({"providers": []})

    def test_unknown_node_404(self):
        status, _ = self._post(f"/api/workflows/{self.pid}/nodes/n_nope/model",
                               {"model": "x"})
        self.assertEqual(status, 404)

    def test_a_step_that_sends_a_pdf_refuses_a_model_that_does_not_read_one(self):
        from storage import settings, secrets_store, store
        settings.update({"providers": [
            {"id": "p_txt", "name": "Text Box", "adapter": "openai", "auth": "api-key",
             "use": "workflow", "key_name": "TB_KEY", "tags": [],
             "endpoint": "http://localhost:11434/v1", "models": [{"name": "textonly"}]},
            {"id": "anthropic", "name": "Anthropic", "adapter": "anthropic", "auth": "api-key",
             "use": "workflow", "key_name": "AN_KEY", "tags": [],
             "models": [{"name": "claude-sonnet-5"}]}]})
        for k in ("TB_KEY", "AN_KEY"):
            secrets_store.set_secret(k, "k", secrets_store.OWNER_APP)
        p = store.load(self.pid)
        node = next(n for n in p["nodes"] if n["id"] == "n_ai")
        node["inputs"] = [{"name": "doc", "type": "file", "file_kind": "pdf"}]
        node["config"]["sends_file"] = {"kinds": ["pdf"]}
        store.save(p)
        try:
            status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/model",
                                   {"model": "textonly", "provider_id": "p_txt"})
            self.assertEqual(status, 400, d)
            self.assertIn("Text Box does not read PDFs", d["error"])
            status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/model",
                                   {"model": "claude-sonnet-5", "provider_id": "anthropic"})
            self.assertEqual(status, 200, d)
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", "/api/models/workflow")
            rows = {m["name"]: m for m in json.loads(c.getresponse().read())["models"]}
            c.close()
            self.assertEqual(rows["textonly"]["reads"], [])
            self.assertEqual(rows["claude-sonnet-5"]["reads"], ["pdf", "image"])
        finally:
            p = store.load(self.pid)
            node = next(n for n in p["nodes"] if n["id"] == "n_ai")
            node["inputs"] = []
            node["config"].pop("sends_file", None)
            store.save(p)
            settings.update({"providers": []})

    def test_prompt_silent_save_applies_and_backfills_baseline(self):
        status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/prompt",
                               {"prompt": "my rewording"})
        self.assertEqual(status, 200, d)
        from storage import store
        doc = store.load_ro(self.pid)
        cfg = next(n for n in doc["nodes"] if n["id"] == "n_ai")["config"]
        self.assertEqual(cfg["prompt"], "my rewording")

        self.assertEqual(cfg["prompt_agent"], "p")

    def test_prompt_reset_restores_the_agent_version(self):
        self._post(f"/api/workflows/{self.pid}/nodes/n_ai/prompt",
                   {"prompt": "another wording"})
        status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/prompt",
                               {"reset": True})
        self.assertEqual(status, 200, d)
        from storage import store
        doc = store.load_ro(self.pid)
        cfg = next(n for n in doc["nodes"] if n["id"] == "n_ai")["config"]
        self.assertEqual(cfg["prompt"], "p")

    def test_prompt_empty_and_non_ai_refused(self):
        status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_ai/prompt",
                               {"prompt": "   "})
        self.assertEqual(status, 400)
        self.assertIn("empty", d["error"])
        status, d = self._post(f"/api/workflows/{self.pid}/nodes/n_code/prompt",
                               {"prompt": "x"})
        self.assertEqual(status, 400)
        self.assertIn("AI step", d["error"])

    def test_prompt_save_refused_while_a_turn_runs(self):
        import turns
        self.assertTrue(turns.begin(self.pid, "chat"))
        try:
            status, d = self._post(
                f"/api/workflows/{self.pid}/nodes/n_ai/prompt",
                {"prompt": "raced"})
            self.assertEqual(status, 409)
        finally:
            turns.finish(self.pid)

    def test_workflow_models_listing(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/models/workflow")
        r = c.getresponse()
        d = json.loads(r.read())
        c.close()
        self.assertEqual(r.status, 200)
        self.assertIn("models", d)
        for m in d["models"]:
            self.assertIn("name", m)
            self.assertIn("ready", m)

class PageDocumentLeavesTestsBehindTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from storage import store
        cls.pid = "p_pagedoc"
        case = {"name": "recorded", "inputs": {"url": "https://r.example"},
                "expect": "ok", "asserts": [], "outputs": {"posts": [{"title": "a"}]}}
        store.save({"id": cls.pid, "nodes": [
            {"id": "n_f", "name": "Fetch posts", "type": "code",
             "config": {"code": "x=1"}, "inputs": [], "outputs": [],
             "tests": [case]}],
            "plan": {"nodes": [{"id": "n_f", "name": "Fetch posts", "type": "code",
                                "tests": [case]}], "edges": []},
            "edges": [], "variables": [], "chat": []})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", path)
        r = c.getresponse()
        d = json.loads(r.read())
        c.close()
        return r.status, d

    def test_the_page_document_carries_no_tests_and_the_store_keeps_them(self):
        from storage import store
        status, d = self._get(f"/api/workflows/{self.pid}")
        self.assertEqual(status, 200)
        self.assertNotIn("tests", d["nodes"][0])
        self.assertNotIn("tests", d["plan"]["nodes"][0])
        self.assertEqual(len(store.load(self.pid)["nodes"][0]["tests"]), 1)
        self.assertEqual(len(store.load(self.pid)["plan"]["nodes"][0]["tests"]), 1)

class ProviderTestButtonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, body="{}", headers={"Content-Type": "application/json"})
        r = c.getresponse()
        d = json.loads(r.read() or b"{}")
        c.close()
        return r.status, d

    def test_the_cheapest_model_is_asked_and_true_means_it_works(self):
        from unittest import mock
        import providers
        from storage import settings, secrets_store
        settings.update({"providers": [
            {"id": "p_tb", "name": "Mine", "adapter": "openai", "auth": "api-key",
             "use": "workflow", "enabled": True, "endpoint": "https://x.example/v1",
             "key_name": "TB_KEY", "tags": [],
             "models": [{"name": "big", "cost": 8}, {"name": "small", "cost": 1}]}]})
        secrets_store.set_secret("TB_KEY", "k", secrets_store.OWNER_APP)
        seen = {}
        def fake_call(model_ref, prompt, inputs, **kw):
            seen.update(model_ref=model_ref, schema=kw.get("output_schema"))
            return {"structured": {"response": True}}
        with mock.patch.object(providers, "call", fake_call):
            status, d = self._post("/api/providers/p_tb/test")
        self.assertEqual(status, 200)
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["model"], "small")
        self.assertEqual(seen["model_ref"], {"model": "small", "provider_id": "p_tb"})
        self.assertEqual(seen["schema"], providers.TEST_CALL_SCHEMA)
        with mock.patch.object(providers, "call", lambda *a, **k: {"text": "(auth error) refused", "error_kind": "auth"}):
            _, d = self._post("/api/providers/p_tb/test")
        self.assertFalse(d["ok"])
        self.assertIn("refused", d["message"])
        with mock.patch.object(providers, "call", lambda *a, **k: {"structured": {"response": False}}):
            _, d = self._post("/api/providers/p_tb/test")
        self.assertFalse(d["ok"])
        self.assertIn("answer shape", d["message"])

    def test_no_model_no_key_and_unknown_provider_are_said(self):
        import providers
        from storage import settings
        self.assertIn("add a model", providers.test_call({"id": "p_none", "models": []})["message"])
        settings.update({"providers": [
            {"id": "p_nokey", "name": "NoKey", "adapter": "openai", "auth": "api-key",
             "use": "workflow", "enabled": True, "endpoint": "https://x.example/v1",
             "key_name": "NOKEY_KEY", "tags": [], "models": [{"name": "m"}]}]})
        self.assertIn("API key", providers.test_call(settings.get()["providers"][0])["message"])
        status, _ = self._post("/api/providers/p_nope/test")
        self.assertEqual(status, 404)

class NodeEvidenceRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from agent import cells
        from storage import store
        cls.pid = "p_evroute"
        store.save({"id": cls.pid, "nodes": [
            {"id": "n_f", "name": "Fetch posts", "type": "code",
             "config": {"code": "x=1"}, "inputs": [], "outputs": [], "tests": []},
            {"id": "n_dry", "name": "Untried", "type": "code",
             "config": {"code": "x=1"}, "inputs": [], "outputs": [], "tests": []}],
            "edges": [], "variables": [], "chat": []})
        cells.record(cls.pid, "Fetch posts", "x=1", {"url": "https://r.example"},
                     {"posts": [{"title": "a"}]}, True, 0.1, [])

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", path)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data or b"{}")

    def test_recorded_sample_served(self):
        status, d = self._get(f"/api/workflows/{self.pid}/nodes/n_f/evidence")
        self.assertEqual(status, 200)
        self.assertTrue(d.get("sample"))
        self.assertIn("inputs", d["sample"])
        self.assertIn("outputs", d["sample"])

    def test_no_recording_serves_null(self):
        status, d = self._get(f"/api/workflows/{self.pid}/nodes/n_dry/evidence")
        self.assertEqual(status, 200)
        self.assertIsNone(d.get("sample"))

    def test_unknown_node_404(self):
        status, _ = self._get(f"/api/workflows/{self.pid}/nodes/n_nope/evidence")
        self.assertEqual(status, 404)

class UnusedSettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from storage import store
        cls.pid = "p_unused"
        store.save({"id": cls.pid, "nodes": [
            {"id": "n_a", "name": "Fetch", "type": "connector",
             "config": {"code": "x=1"},
             "inputs": [{"name": "posts_per_run", "type": "number"}],
             "outputs": [{"name": "feed", "type": "any"}], "tests": []}],
            "edges": [], "chat": [], "variables": [
                {"name": "posts_per_run", "value": 20, "secret": False},
                {"name": "max_posts_per_run", "value": "20", "secret": False},
                {"name": "feed", "value": "", "secret": False}]})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_a_file_setting_reports_its_filename_not_a_blob_ref(self):
        from storage import blobstore, store
        ref = blobstore.put(b"col_a,col_b\n1,2\n", "text/csv",
                            {"name": "rates.csv"}, owner=blobstore.OWNER_APP)
        p = store.load(self.pid)
        p["nodes"][0]["inputs"].append({"name": "rates", "type": "file"})
        p["variables"].append({"name": "rates", "value": ref, "secret": False})
        store.save(p)
        try:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", f"/api/workflows/{self.pid}")
            d = json.loads(c.getresponse().read())
            c.close()
            self.assertEqual(d["_variable_files"]["rates"], "rates.csv")
            self.assertEqual(d["_variable_types"]["rates"], "file")
        finally:
            p = store.load(self.pid)
            p["nodes"][0]["inputs"] = [q for q in p["nodes"][0]["inputs"]
                                       if q["name"] != "rates"]
            p["variables"] = [v for v in p["variables"] if v["name"] != "rates"]
            store.save(p)

    def test_workflow_get_carries_the_open_interactions(self):
        from agent import interactions
        iid = interactions.create_sync("approval", {
            "workflow_id": self.pid, "title": "Open a window?"})
        try:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", f"/api/workflows/{self.pid}")
            d = json.loads(c.getresponse().read())
            c.close()
            self.assertIn(iid, [e["id"] for e in d.get("pending", [])])
        finally:
            with interactions._lock:
                interactions._pending.pop(iid, None)

    def test_only_the_unread_names_are_marked(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", f"/api/workflows/{self.pid}")
        d = json.loads(c.getresponse().read())
        c.close()

        read = {r["name"]: r["read"] for r in d["_variable_resolution"]}
        self.assertEqual(read, {"posts_per_run": True,
                                "max_posts_per_run": False, "feed": False})

        self.assertEqual(len(d["variables"]), 3)

    def test_a_code_consumed_secret_counts_as_read(self):
        from storage import environments, store
        p = store.load(self.pid)
        p["nodes"][0]["config"]["code"] = \
            'k = get_secret("sheets_key")\nx = 1'
        p["variables"].append({"name": "sheets_key", "value": None,
                               "secret": True})
        store.save(p)
        try:
            read = {r["name"]: r["read"]
                    for r in environments.resolution_summary(store.load(self.pid))}
            self.assertTrue(read["sheets_key"])
        finally:
            p = store.load(self.pid)
            p["nodes"][0]["config"]["code"] = "x=1"
            p["variables"] = [v for v in p["variables"]
                              if v["name"] != "sheets_key"]
            store.save(p)

    def test_the_consumed_value_cannot_be_deleted_the_unread_one_can(self):
        from storage import store
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)

        c.request("DELETE", f"/api/workflows/{self.pid}/variables/posts_per_run")
        r = c.getresponse()
        body = json.loads(r.read())
        self.assertEqual(r.status, 409)
        self.assertIn("runs use", body["error"])

        p = store.load(self.pid)
        p.setdefault("env_bindings", {})["max_posts_per_run"] = "workflow"
        store.save(p)
        c.request("DELETE",
                  f"/api/workflows/{self.pid}/variables/max_posts_per_run")
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        r.read()
        c.close()
        fresh = store.load(self.pid)
        try:
            names = [v["name"] for v in fresh["variables"]]
            self.assertNotIn("max_posts_per_run", names)
            self.assertIn("posts_per_run", names)
            self.assertNotIn("max_posts_per_run",
                             fresh.get("env_bindings") or {})
        finally:
            fresh["variables"].append(
                {"name": "max_posts_per_run", "value": "20", "secret": False})
            store.save(fresh)

class SayAtRestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from storage import store
        cls.pid = "p_asksettle"
        from agent import transcript
        proj = {"id": cls.pid, "nodes": [], "edges": [], "variables": [],
                "transcript": [], "transcript_seq": 0}
        transcript.append_request(proj, "ask",
                                  {"question": "Proceed?", "options": ["Yes"]},
                                  iid="int_rest1")
        store.save(proj)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, body=b"{}"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, body=body,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data or b"{}")

    def _say(self, body):
        return self._post(f"/api/workflows/{self.pid}/say",
                          json.dumps(body).encode())

    def _wait_idle(self):
        import time as _t
        import turns
        deadline = _t.time() + 10
        while _t.time() < deadline and turns.snapshot(self.pid).get("active"):
            _t.sleep(0.05)

    def test_a_click_is_an_answer_never_a_message_and_carries_its_cid(self):
        from storage import store
        status, d = self._say({"kind": "click", "iid": "int_rest1",
                               "text": "Yes", "cid": "c-rest1"})
        self.assertEqual(status, 200, d)
        self.assertEqual(d.get("landed"), "started")
        self._wait_idle()
        from agent import transcript
        p = store.load(self.pid)

        card = next(i for i in transcript.items(p)
                    if i.get("iid") == "int_rest1")
        self.assertEqual(card["payload"]["question"], "Proceed?")
        self.assertNotIn("shown", card)
        ans = next(i for i in transcript.items(p)
                   if i["kind"] == "answer" and i["to"] == "int_rest1")
        self.assertEqual(ans["shown"], "Yes")
        self.assertEqual(ans["cid"], "c-rest1")
        self.assertFalse([i for i in transcript.items(p)
                          if i["kind"] == "message" and i.get("from") == "user"
                          and i.get("text") == "Yes"])

        status2, d2 = self._say({"kind": "click", "iid": "int_rest1",
                                 "text": "Yes"})
        self.assertEqual(status2, 200)
        self.assertEqual(d2.get("landed"), "noop")

    def test_a_parked_plan_card_clicked_later_still_approves(self):
        from agent import node_tools, transcript
        from storage import store
        p = store.load(self.pid)
        transcript.append_request(
            p, "blueprint", {"head": "plan", "summary": "Do the thing.",
                             "steps": [], "options": ["Looks right - go ahead"]},
            iid="blu_parked1")
        store.save(p)
        self.assertFalse(node_tools.plan_approved(store.load(self.pid)))
        status, _ = self._say({"kind": "click", "iid": "blu_parked1",
                               "text": "Looks right - go ahead"})
        self.assertEqual(status, 200)
        self._wait_idle()
        self.assertTrue(node_tools.plan_approved(store.load(self.pid)))

    def test_a_parked_fix_card_stamps_per_fix_never_global(self):
        from agent import node_tools, transcript
        from storage import store
        p = store.load(self.pid)
        p.pop("plan_approved_ts", None)
        p["plan"] = {"status": "draft", "summary": "s",
                     "ticket_id": "tkt_fix001", "nodes": []}
        transcript.append_request(
            p, "blueprint", {"head": "plan", "summary": "Fix it.",
                             "steps": [], "fix": "tkt_fix001",
                             "options": ["Looks right - go ahead"]},
            iid="blu_fixpark")
        store.save(p)
        status, _ = self._say({"kind": "click", "iid": "blu_fixpark",
                               "text": "Looks right - go ahead"})
        self.assertEqual(status, 200)
        self._wait_idle()
        p2 = store.load(self.pid)
        self.assertTrue(p2["plan"].get("fix_approved_ts"))
        self.assertFalse(node_tools.plan_approved(p2))

    def test_a_parked_built_record_approves_nothing(self):
        from agent import node_tools, transcript
        from storage import store
        p = store.load(self.pid)
        p.pop("plan_approved_ts", None)
        transcript.append_request(
            p, "blueprint", {"head": "built", "summary": "Done.", "steps": []},
            iid="blu_parked2")
        store.save(p)
        self._say({"kind": "click", "iid": "blu_parked2", "text": "anything"})
        self._wait_idle()
        self.assertFalse(node_tools.plan_approved(store.load(self.pid)))

    def test_a_long_field_value_is_kept_whole_never_clipped(self):
        from agent import transcript
        from storage import blobstore, store
        p = store.load(self.pid)
        transcript.append_request(p, "ask", {
            "question": "Criteria?",
            "fields": [{"name": "fit_criteria", "label": "Fit criteria",
                        "type": "text"}]}, iid="int_long1")
        store.save(p)
        long_text = "HEAD " + ("x" * 5000) + " TAIL"
        status, d = self._say({"kind": "click", "iid": "int_long1",
                               "answers": {"fit_criteria": long_text}})
        self.assertEqual(status, 200, d)
        self._wait_idle()
        p2 = store.load(self.pid)
        ans = [i for i in transcript.items(p2) if i["kind"] == "answer"][-1]
        shown = ans["shown"]["fit_criteria"]
        self.assertEqual(shown, long_text)

    def test_setup_card_stores_partial_answers(self):
        from storage import store
        from agent import transcript
        p = store.load(self.pid)
        transcript.append_request(p, "ask", {
            "question": "Setup",
            "fields": [{"name": "sheet_url", "label": "Sheet link",
                        "type": "text"},
                       {"name": "post_cap", "label": "Posts per run",
                        "type": "text"}]}, iid="int_fields1")
        store.save(p)
        status, d = self._say({"kind": "click", "iid": "int_fields1",
                               "answers": {"sheet_url": "https://s.example/x",
                                           "stray": "nope"}})
        self.assertEqual(status, 200, d)
        self._wait_idle()
        p2 = store.load(self.pid)
        ans = [i for i in transcript.items(p2) if i["kind"] == "answer"
               and i["to"] == "int_fields1"][-1]
        self.assertEqual(ans["shown"], {"sheet_url": "https://s.example/x"})

        self.assertIn("Sheet link: https://s.example/x", ans["text"])
        self.assertIn("Not provided: Posts per run", ans["text"])

class FsListTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", path)
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_lists_home_and_never_escapes(self):
        from pathlib import Path
        status, d = self._get("/api/fs")
        self.assertEqual(status, 200)
        self.assertEqual(d["path"], str(Path.home().resolve()))
        self.assertIsInstance(d["dirs"], list)
        status, d2 = self._get("/api/fs?path=/etc")
        self.assertEqual(d2["path"], str(Path.home().resolve()))
        status, d3 = self._get("/api/fs?path=" + d["path"] + "/..")
        self.assertEqual(d3["path"], str(Path.home().resolve()))

class ChatMessageSizeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from tests._bootstrap import workflow as _mkworkflow
        cls.pid = _mkworkflow([], pid="p_msglimit")["id"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_native_folder_pick_shapes(self):
        class R:
            def __init__(self, code, out="", err=""):
                self.returncode, self.stdout, self.stderr = code, out, err

        picked = server_mod._native_pick_folder(
            runner=lambda *a, **k: R(0, "/Users/x/Reports\n"),
            platform="darwin")
        self.assertEqual(picked, {"path": "/Users/x/Reports"})
        cancelled = server_mod._native_pick_folder(
            runner=lambda *a, **k: R(1, "", "User canceled."),
            platform="darwin")
        self.assertEqual(cancelled, {"cancelled": True})

        def _missing(*a, **k):
            raise FileNotFoundError("zenity")
        self.assertEqual(server_mod._native_pick_folder(
            runner=_missing, platform="linux"), {"unavailable": True})
        headless = server_mod._native_pick_folder(
            runner=lambda *a, **k: R(1, "", "cannot open display"),
            platform="linux")
        self.assertEqual(headless, {"unavailable": True})

        self.assertEqual(server_mod._native_pick_folder(
            runner=lambda *a, **k: R(0, ""), platform="win32"),
            {"cancelled": True})

    def test_a_long_message_is_not_refused_for_its_length(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = json.dumps({"kind": "typed", "text": "x" * 50_000})
        c.request("POST", f"/api/workflows/{self.pid}/say", body=body,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        self.assertNotEqual(r.status, 400, data)
        self.assertNotIn("too long", str(data.get("error") or ""))

class BuildMaterialsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from tests._bootstrap import workflow as _mkworkflow
        cls.pid = _mkworkflow([], pid="p_materials")["id"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_upload_stamps_delete_removes_inline_serves(self):
        import base64
        png = base64.b64encode(b"\x89PNG fake image bytes").decode()
        r, data = self._req("POST", f"/api/workflows/{self.pid}/samples",
                            json.dumps({"name": "shot.png", "mime": "image/png",
                                        "data_b64": png}))
        self.assertEqual(r.status, 200)
        up = json.loads(data)
        self.assertTrue(up["samples"][0].get("ts"))

        ref = up["ref"].replace("blob:", "")
        r2, _ = self._req("GET", f"/api/blobs/{ref}")
        self.assertIn("inline", r2.getheader("Content-Disposition", ""))

        r3, d3 = self._req("DELETE",
                           f"/api/workflows/{self.pid}/samples/shot.png")
        self.assertEqual(r3.status, 200)
        self.assertEqual(json.loads(d3)["samples"], [])
        r4, _ = self._req("DELETE",
                          f"/api/workflows/{self.pid}/samples/shot.png")
        self.assertEqual(r4.status, 404)

    def test_har_upload_is_sniffed_and_scrubbed(self):
        import base64
        har = {"log": {"entries": [{
            "startedDateTime": "2026-08-04T10:00:00Z",
            "request": {"method": "GET",
                        "url": "https://api.x.example/a",
                        "headers": [{"name": "Cookie",
                                     "value": "sid=UPLOADSECRET99"}]},
            "response": {"status": 200, "headers": [],
                         "content": {"text": "{\"a\": 1}"}}}]}}
        b64 = base64.b64encode(json.dumps(har).encode()).decode()
        r, data = self._req("POST", f"/api/workflows/{self.pid}/samples",
                            json.dumps({"name": "cap.har",
                                        "mime": "application/json",
                                        "data_b64": b64}))
        self.assertEqual(r.status, 200)
        up = json.loads(data)
        self.assertTrue(up.get("ok"), up)
        entry = next(s for s in up["samples"] if s["name"] == "cap.har")
        self.assertEqual(entry.get("kind"), "recording")
        from storage import blobstore
        stored = blobstore.get(entry["ref"]).decode()
        self.assertNotIn("UPLOADSECRET99", stored)
        self._req("DELETE", f"/api/workflows/{self.pid}/samples/cap.har")

class ProviderRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data or b"{}")

    def test_usage_route_shape(self):
        status, d = self._req("GET", "/api/providers/usage")
        self.assertEqual(status, 200)
        self.assertIn("usage", d)
        for counts in d["usage"].values():
            self.assertEqual(sorted(counts), ["nodes", "workflows"])

    def test_secret_delete_guarded_while_referenced(self):
        from storage import secrets_store, settings
        settings.update({"providers": [
            {"id": "p_delguard", "name": "G", "adapter": "anthropic",
             "auth": "api-key", "use": "workflow", "key_name": "DEL_GUARD_KEY",
             "tags": [], "models": []}]})
        secrets_store.set_secret("DEL_GUARD_KEY", "v", secrets_store.OWNER_APP)
        status, d = self._req("DELETE", "/api/secrets/DEL_GUARD_KEY")
        self.assertEqual(status, 409)
        self.assertIn("provider", d["error"])
        settings.update({"providers": []})
        status, d = self._req("DELETE", "/api/secrets/DEL_GUARD_KEY")
        self.assertEqual(status, 200)
        self.assertTrue(d["deleted"])
        self.assertFalse(secrets_store.has_secret("DEL_GUARD_KEY", secrets_store.OWNER_APP))

    def test_key_check_without_a_key_answers_offline(self):
        status, d = self._req("POST", "/api/providers/test",
                              json.dumps({"adapter": "anthropic",
                                          "model": "m"}))
        self.assertEqual(status, 200)
        self.assertFalse(d["ok"])
        self.assertEqual(d["error_kind"], "auth")

class VersionRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from storage import store
        cls.pid = "p_verroute"
        cls.p = {"id": cls.pid, "nodes": [
            {"id": "n1", "name": "Step one", "type": "code",
             "config": {"code": "v1"}}],
            "edges": [], "variables": [], "chat": []}
        store.save(cls.p)
        store.record_version(cls.p, "build")
        cls.p["nodes"][0]["config"]["code"] = "v2"
        store.save(cls.p)
        store.record_version(cls.p, "build")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data or b"{}")

    def test_list_is_metadata_only_newest_first(self):
        status, d = self._req("GET", f"/api/workflows/{self.pid}/versions")
        self.assertEqual(status, 200)
        self.assertEqual(len(d["versions"]), 2)
        for v in d["versions"]:
            self.assertEqual(sorted(v), ["hash", "id", "number", "reason", "ts"])
        self.assertGreater(d["versions"][0]["id"], d["versions"][1]["id"])

    def test_unknown_workflow_404(self):
        status, _ = self._req("GET", "/api/workflows/p_nope/versions")
        self.assertEqual(status, 404)

    def test_restore_reverts_and_is_slot_guarded(self):
        import turns
        from storage import store
        oldest = self._req("GET", f"/api/workflows/{self.pid}/versions")[1][
            "versions"][-1]["id"]

        self.assertTrue(turns.begin(self.pid, "chat"))
        try:
            status, d = self._req(
                "POST", f"/api/workflows/{self.pid}/versions/{oldest}/restore",
                json.dumps({}))
            self.assertEqual(status, 409)
        finally:
            turns.finish(self.pid)
        status, d = self._req(
            "POST", f"/api/workflows/{self.pid}/versions/{oldest}/restore",
            json.dumps({}))
        self.assertEqual(status, 200)
        self.assertTrue(d["ok"])
        doc = store.load(self.pid)
        self.assertEqual(doc["nodes"][0]["config"]["code"], "v1")

        top = self._req("GET", f"/api/workflows/{self.pid}/versions")[1][
            "versions"][0]
        self.assertTrue(top["reason"].startswith("restore::"))
        self.assertEqual(top["hash"], doc["config_hash"])

    def test_restore_bad_ids_answer_plainly(self):
        status, _ = self._req(
            "POST", f"/api/workflows/{self.pid}/versions/abc/restore",
            json.dumps({}))
        self.assertEqual(status, 400)
        status, _ = self._req(
            "POST", f"/api/workflows/{self.pid}/versions/99999/restore",
            json.dumps({}))
        self.assertEqual(status, 400)

class IssueStatusRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from tests._bootstrap import workflow as _mkworkflow
        cls.pid = _mkworkflow([], pid="p_issues")["id"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _put(self, path, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("PUT", path, body=json.dumps(body),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data or b"{}")

    def _seed(self, tid):
        from storage import store
        p = store.load(self.pid)
        p["tickets"] = [{"id": tid, "status": "open", "node_id": "n1",
                         "reason": "node threw: TimeoutError", "verdict": {},
                         "notes": ""}]
        store.save(p)

    def test_the_client_settable_statuses(self):
        self._seed("tkt_x")
        for status in ("in-progress", "dismissed", "closed", "open"):
            code, body = self._put(
                f"/api/workflows/{self.pid}/tickets/tkt_x", {"status": status})
            self.assertEqual(code, 200, body)
            self.assertEqual(body["status"], status)
        code, _ = self._put(f"/api/workflows/{self.pid}/tickets/tkt_x",
                            {"status": "ready"})
        self.assertEqual(code, 400)

    def test_the_landed_flag_records_a_confirmed_non_arrival(self):
        from storage import store
        self._seed("tkt_nl")
        code, body = self._put(f"/api/workflows/{self.pid}/tickets/tkt_nl",
                               {"landed": False})
        self.assertEqual(code, 200, body)
        t = store.load(self.pid)["tickets"][0]
        self.assertIs(t["landed"], False)
        self.assertNotIn("continued", t)

    def test_a_continuation_is_consumed_once(self):
        from storage import store
        self._seed("tkt_c")
        code, body = self._put(f"/api/workflows/{self.pid}/tickets/tkt_c",
                               {"continued": True})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["continued"])
        self.assertTrue(store.load(self.pid)["tickets"][0]["continued"])

    def test_a_dismissed_issue_is_still_there(self):
        from storage import store
        self._seed("tkt_y")
        self._put(f"/api/workflows/{self.pid}/tickets/tkt_y",
                  {"status": "dismissed"})
        self.assertEqual([t["status"] for t in store.load(self.pid)["tickets"]],
                         ["dismissed"])

class DidItLandSubmitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from tests._bootstrap import workflow as _mkworkflow
        cls.pid = _mkworkflow(
            [{"id": "n_w", "name": "Save rows", "type": "connector",
              "read_only": False, "config": {},
              "outputs": [{"name": "status", "type": "number"},
                          {"name": "updated_range", "type": "text"}]}],
            pid="p_didland")["id"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, body=json.dumps(body),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data or b"{}")

    def _halted(self, run_id, tid):
        from runtime import run_state
        from storage import store
        run_state.start(self.pid, run_id, {"sheet": "abc"})
        run_state.halt(run_id, "n_w", "send-unverified")
        p = store.load(self.pid)
        p["tickets"] = [{"id": tid, "status": "open", "node_id": "n_w",
                         "run_id": run_id, "reason": "send-unverified",
                         "verdict": {}, "notes": ""}]
        store.save(p)

    def test_submit_records_the_step_and_stamps_the_issue(self):
        from runtime import run_state
        from storage import store
        self._halted("run_dl1", "tkt_dl1")
        code, body = self._post(
            f"/api/workflows/{self.pid}/tickets/tkt_dl1/user-outputs",
            {"outputs": {"status": 200}, "unsure": ["updated_range"]})
        self.assertEqual(code, 200, body)

        self.assertTrue(run_state.is_done("run_dl1", "n_w"))
        self.assertEqual(run_state.output_of("run_dl1", "n_w"),
                         {"status": 200})
        t = store.load(self.pid)["tickets"][0]
        self.assertEqual(t["user_outputs"]["given"], ["status"])
        self.assertEqual(t["user_outputs"]["unsure"], ["updated_range"])

        self.assertTrue(t["continued"])

    def test_a_dead_run_is_a_plain_409(self):
        from storage import store
        p = store.load(self.pid)
        p["tickets"] = [{"id": "tkt_dl2", "status": "open", "node_id": "n_w",
                         "run_id": "run_gone", "reason": "send-unverified",
                         "verdict": {}, "notes": ""}]
        store.save(p)
        code, body = self._post(
            f"/api/workflows/{self.pid}/tickets/tkt_dl2/user-outputs",
            {"outputs": {"status": 200}})
        self.assertEqual(code, 409)
        self.assertIn("no longer be continued", body["error"])

class IssueSnapshotsEntryInputsTest(unittest.TestCase):
    def test_the_ticket_carries_the_entry_values(self):
        from runtime import run_state
        run_state.start("p_snap", "run_s1", {"search_url": "https://x/y"})
        snap = run_state.load("run_s1") or {}
        self.assertEqual(snap.get("entry_inputs"),
                         {"search_url": "https://x/y"})

class LocalTokenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_dev_channel_never_enforces(self):
        self.assertEqual(config.BUILD, "dev")
        code, _ = self._get("/api/workflows")
        self.assertEqual(code, 200)

    def test_platform_channel_requires_the_token(self):
        from unittest import mock
        with mock.patch.object(config, "BUILD", "mac-silicon"):
            code, body = self._get("/api/workflows")
            self.assertEqual(code, 403)
            self.assertIn("token", json.loads(body)["error"])
            code, _ = self._get("/api/workflows",
                                {"X-Cryogram-Token": server_mod.LOCAL_TOKEN})
            self.assertEqual(code, 200)

    def test_handshake_and_blob_reads_stay_open(self):
        from unittest import mock
        with mock.patch.object(config, "BUILD", "mac-silicon"):
            for path in ("/api/version", "/api/build"):
                code, _ = self._get(path)
                self.assertEqual(code, 200, path)

            code, _ = self._get("/api/blobs/" + "0" * 64)
            self.assertNotEqual(code, 403)

    def test_index_html_carries_the_token(self):
        code, body = self._get("/")
        self.assertEqual(code, 200)
        self.assertIn(server_mod.LOCAL_TOKEN.encode(), body)
        self.assertNotIn(b"__CRYOGRAM_LOCAL_TOKEN__", body)

class NoIntegrationMeansNoRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, body="{}",
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_cloud_paths_are_unknown_routes(self):
        import extensions
        extensions.reset()
        extensions._LOADED = True
        try:
            for path in ("/api/cloud/workflows/w1/run", "/api/workflows/p_x/publish"):
                code, body = self._post(path)
                self.assertEqual(code, 404, path)
                self.assertEqual(json.loads(body)["error"], "unknown route", path)

            code, _ = self._post("/api/workflows/p_missing/duplicate")
            self.assertNotEqual(code, 403)
        finally:
            extensions.reset()

    def test_version_says_what_is_installed(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/version")
        v = json.loads(c.getresponse().read())
        c.close()
        self.assertEqual(v["channel"], "dev")
        self.assertEqual(v["version"], config.VERSION)
        self.assertIsInstance(v["extensions"], list)
        self.assertNotIn("cloud", v)

class DisplayIsPolledTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from storage import store
        cls.pid = "p_polled"
        store.save({"id": cls.pid, "nodes": [
            {"id": "n1", "name": "Slow step", "type": "code",
             "config": {"code": "import time\n"
                                "time.sleep(0.5)\n"
                                "write_output('y', 1)"},
             "inputs": [], "outputs": [{"name": "y", "type": "number"}],
             "tests": []}],
            "edges": [], "variables": [], "chat": []})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _poll(self, after=0):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", f"/api/workflows/{self.pid}/turn?after={after}")
        r = c.getresponse()
        body = json.loads(r.read())
        c.close()
        self.assertEqual(r.status, 200)
        return body

    def test_a_run_survives_a_client_that_disconnects_at_once(self):
        import time as _t

        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", f"/api/workflows/{self.pid}/run", body=b"{}",
                  headers={"Content-Type": "application/json"})
        c.close()

        deadline = _t.time() + 30
        snap = {}
        while _t.time() < deadline:
            snap = self._poll()
            if snap.get("last_seq") and not snap.get("active"):
                break
            _t.sleep(0.2)
        kinds = [e.get("type") for e in snap.get("events") or []]
        self.assertIn("node-start", kinds)
        self.assertIn("done", kinds)
        done = [e for e in snap["events"] if e.get("type") == "done"][-1]
        self.assertEqual(done["result"]["status"], "completed")
        self.assertIn("pending", snap)

        tail = self._poll(after=snap["last_seq"])
        self.assertEqual(tail["events"], [])

class RunHistoryPageRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from storage import store
        cls.pid = "p_runs_page"
        store.save({"id": cls.pid, "nodes": [], "edges": [], "variables": [],
                    "chat": []})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_the_route_pages(self):
        from unittest import mock

        from storage import deliverables
        fake = {"runs": [], "page": 3, "pages": 4, "total": 80, "per_page": 25}
        with mock.patch.object(deliverables, "runs_page",
                               return_value=fake) as rp:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", f"/api/workflows/{self.pid}/runs?page=3")
            r = c.getresponse()
            body = json.loads(r.read())
            c.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(body["page"], 3)
        rp.assert_called_once_with(self.pid, 3)

class ImportRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/api/workflows/import", body=json.dumps(body),
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data or b"{}")

    def test_doc_import_creates_an_owned_workflow(self):
        from storage import share, store
        src = {"id": "p_imp_src", "name": "Shared one", "nodes": [
            {"id": "n1", "name": "Step", "type": "code",
             "config": {"code": "write_output('x', 1)"}, "inputs": [],
             "outputs": [{"name": "x", "type": "number"}]}],
            "edges": [], "variables": [{"name": "k", "value": "v",
                                        "secret": False, "persistent": True}]}
        store.save(src)
        status, d = self._post({"doc": share.export_doc(src)})
        self.assertEqual(status, 200, d)
        pid = d["workflow"]["id"]
        self.assertNotEqual(pid, "p_imp_src")
        loaded = store.load(pid)
        self.assertEqual(loaded["name"], "Shared one")
        self.assertIsNone(loaded["variables"][0]["value"])
        self.assertTrue(loaded["description"].startswith("Built by Cryogram"))

    def test_a_bad_file_or_no_file_is_a_plain_400(self):
        status, d = self._post({"doc": {"format": "nope"}})
        self.assertEqual(status, 400)
        self.assertIn("error", d)

        status, d = self._post({"url": "http://localhost/#/project/proj_aaaaaa"})
        self.assertEqual(status, 400)
        self.assertIn("error", d)

class SetAsideRaisesAnIssueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _workflow(self, pid, policy):
        from runtime import shape
        from storage import store
        code = ("write_output('posts', [{'title': 'a', 'n': 1}, {'title': 'b', 'n': 2}, {'title': 'bad'}])")
        store.save({"id": pid, "nodes": [
            {"id": "n1", "name": "Fetch", "type": "code",
             "config": {"code": code, "on_invalid_items": policy},
             "inputs": [], "outputs": [{"name": "posts", "type": "any",

                                        "schema": shape.apply_item_fields(
                                            shape.infer([{"title": "a", "n": 1}]),
                                            [{"name": "n", "type": "number", "required": True}])}],
             "tests": []}],
            "edges": [], "variables": [], "chat": []})

    def _run_and_wait(self, pid):
        import time as _t
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", f"/api/workflows/{pid}/run", body=b"{}",
                  headers={"Content-Type": "application/json"})
        c.getresponse().read()
        c.close()
        deadline = _t.time() + 30
        while _t.time() < deadline:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", f"/api/workflows/{pid}/turn")
            snap = json.loads(c.getresponse().read())
            c.close()
            if snap.get("last_seq") and not snap.get("active"):
                return [e for e in snap["events"] if e.get("type") == "done"][-1]
            _t.sleep(0.2)
        self.fail("run never finished")

    def test_proceed_raises_the_issue_log_does_not(self):
        from storage import deliverables, store
        self._workflow("p_aside_pr", "proceed")
        done = self._run_and_wait("p_aside_pr")
        self.assertEqual(done["result"]["status"], "completed")
        proj = store.load("p_aside_pr")
        tickets = [t for t in proj.get("tickets") or [] if t["reason"] == "items-set-aside"]
        self.assertEqual(len(tickets), 1)
        t = tickets[0]
        self.assertEqual((t["status"], t["node_id"]), ("open", "n1"))
        self.assertEqual(t["offending"][0]["total"], 1)
        self.assertEqual(t["offending"][0]["rows"][0]["value"], {"title": "bad"})
        self.assertIn("1 of 3 items", " ".join(t["verdict"]["set_aside"]))
        run = next(m for m in deliverables.list_runs("p_aside_pr")
                   if m["run_id"] == done["result"]["run_id"])
        self.assertEqual(run["set_aside"][0]["ports"][0]["total"], 1)

        self._workflow("p_aside_log", "log")
        done = self._run_and_wait("p_aside_log")
        self.assertEqual(done["result"]["status"], "completed")
        proj = store.load("p_aside_log")
        self.assertEqual([t for t in proj.get("tickets") or []
                          if t["reason"] == "items-set-aside"], [])

class MayBeEmptyRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_marks_port_and_plan_and_clears_expectation(self):
        from storage import store
        pid = "p_mbe1"
        store.save({"id": pid, "nodes": [
            {"id": "n1", "name": "Browse", "type": "code", "config": {},
             "inputs": [],
             "outputs": [{"name": "people", "type": "any",
                          "schema": {"type": "array", "items": {},
                                     "x-nonempty": True}}], "tests": []}],
            "edges": [], "variables": [], "chat": [],
            "plan": {"nodes": [{"id": "n1", "name": "Browse",
                                "outputs": [{"name": "people", "type": "any",
                                             "schema": {"type": "array",
                                                        "x-nonempty": True}}]}]}})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("PUT", f"/api/workflows/{pid}/nodes/n1/may-be-empty",
                  body=json.dumps({"ports": ["people"]}),
                  headers={"Content-Type": "application/json"})
        r = json.loads(c.getresponse().read())
        c.close()
        self.assertEqual(r.get("may_be_empty"), ["people"])
        p = store.load(pid)
        out = p["nodes"][0]["outputs"][0]
        self.assertTrue(out["may_be_empty"])
        self.assertNotIn("x-nonempty", out["schema"])
        pn = p["plan"]["nodes"][0]["outputs"][0]
        self.assertTrue(pn["may_be_empty"])
        self.assertNotIn("x-nonempty", pn["schema"])

class ParkedApprovalGrantTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _settle(self, pid, iid, ans):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", f"/api/workflows/{pid}/say",
                  body=json.dumps({"kind": "click", "iid": iid, "text": ans}),
                  headers={"Content-Type": "application/json"})
        r = json.loads(c.getresponse().read())
        c.close()
        import time as _t
        import turns
        deadline = _t.time() + 10
        while _t.time() < deadline and turns.snapshot(pid).get("active"):
            _t.sleep(0.05)
        return r

    def test_allow_stamps_the_grant_deny_does_not(self):
        from agent import transcript
        from storage import store

        for pid, iid, ans, step in (("p_grant_rt", "int_grant1", "allow", "Fetch posts"),
                                    ("p_grant_rt2", "int_grant2", "deny", "Send rows")):
            p = {"id": pid, "nodes": [], "edges": [], "variables": [],
                 "transcript": [], "transcript_seq": 0}
            transcript.append_request(
                p, "approval", {"title": "Do it?", "detail": "d", "scope": "s",
                                "step": step}, iid=iid)
            store.save(p)
            self.assertEqual(self._settle(pid, iid, ans).get("landed"), "started")
        allow = store.load("p_grant_rt")
        self.assertEqual([g["step"] for g in allow.get("approval_grants") or []],
                         ["Fetch posts"])
        deny = store.load("p_grant_rt2")
        self.assertEqual(deny.get("approval_grants") or [], [])

        ans = [i for i in deny["transcript"] if i["kind"] == "answer"][-1]
        self.assertEqual(ans["shown"], "deny")
        self.assertTrue(ans["text"].startswith('No to "Do it?"'))

class OneErrorTypeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path,
                  body=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def _run_and_wait(self, pid, body):
        import time as _t
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", f"/api/workflows/{pid}/run", body=json.dumps(body),
                  headers={"Content-Type": "application/json"})
        c.getresponse().read()
        c.close()
        deadline = _t.time() + 30
        while _t.time() < deadline:
            status, snap = self._req("GET", f"/api/workflows/{pid}/turn")
            if snap.get("last_seq") and not snap.get("active"):
                return [e for e in snap["events"] if e.get("type") == "done"][-1]
            _t.sleep(0.2)
        self.fail("run never finished")

    def test_a_blocked_website_is_an_issue_not_a_banner(self):
        from runtime import run_state
        from storage import store
        pid = "p_one_error"
        store.save({"id": pid, "nodes": [
            {"id": "n_r", "name": "Read the site", "type": "connector",
             "read_only": True, "external_impact": "reads a site",
             "config": {"code": "raise RuntimeError('egress blocked: example.test')"},
             "inputs": [], "outputs": [{"name": "rows", "type": "any"}],
             "tests": []}],
            "edges": [], "chat": [], "variables": []})
        done = self._run_and_wait(pid, {})
        res = done["result"]
        self.assertEqual(res["reason"], "egress-blocked")
        self.assertIs(res.get("waiting_on_you"), False)
        self.assertTrue(res.get("ticket_id"))
        _, proj = self._req("GET", f"/api/workflows/{pid}")
        self.assertNotIn("_run_wait", proj)
        tickets = store.load(pid).get("tickets") or []
        self.assertEqual([t["reason"] for t in tickets], ["egress-blocked"])
        run_state.finish(res["run_id"])

    def test_proceed_forks_the_run_and_moves_the_issue(self):
        from runtime import run_state
        from storage import store
        pid = "p_proceed"
        code = "items = read_input('items')\ndone = checkpoints()\nfor it in items:\n    k = str(it['id'])\n    if k in done:\n        continue\n    if it['id'] >= 3:\n        raise RuntimeError('boom at ' + k)\n    checkpoint(k, {'id': it['id'], 'ok': True})\nwrite_output('sent', list(checkpoints().values()))\n"
        store.save({"id": pid, "nodes": [
            {"id": "n_pi", "name": "Send each", "type": "code",
             "config": {"code": code, "per_item": {"input": "items", "key": "id"}},
             "inputs": [{"name": "items", "type": "any"}],
             "outputs": [{"name": "sent", "type": "any"}], "tests": []}],
            "edges": [], "chat": [], "variables": []})
        done = self._run_and_wait(pid, {"entry_inputs": {
            "items": [{"id": 1}, {"id": 2}, {"id": 3}]}})
        res = done["result"]
        self.assertEqual(res["status"], "halted", res)
        self.assertEqual(res["progress"]["done"], 2)
        run_id, tid = res["run_id"], res["ticket_id"]
        status, _ = self._req("POST", f"/api/workflows/p_other/runs/{run_id}/proceed", {})
        self.assertEqual(status, 404)
        status, r = self._req("POST", f"/api/workflows/{pid}/runs/{run_id}/proceed", {})
        self.assertEqual(status, 200, r)
        self.assertEqual(r["run_id"], run_id)
        sibling = r["sibling"]
        self.assertTrue(sibling)
        t = next(x for x in store.load(pid)["tickets"] if x["id"] == tid)
        self.assertEqual(t["run_id"], sibling)
        status, _ = self._req("POST", f"/api/workflows/{pid}/runs/{run_id}/proceed", {})
        self.assertEqual(status, 409)
        done2 = self._run_and_wait(pid, {"run_id": run_id})
        self.assertEqual(done2["result"]["status"], "completed", done2["result"])
        _, page = self._req("GET", f"/api/workflows/{pid}/runs")
        rows = {x["run_id"]: x for x in page["runs"]}
        self.assertEqual(rows[run_id]["partial"]["half"], "done")
        self.assertEqual(rows[sibling]["partial"]["half"], "remaining")
        self.assertEqual(rows[sibling]["status"], "halted")
        run_state.finish(sibling)

    def test_the_newest_unseen_run_opens_on_arrival_until_marked_seen(self):
        from storage import store
        pid = "p_unseen"
        store.save({"id": pid, "nodes": [
            {"id": "n_ok", "name": "Count", "type": "code",
             "config": {"code": "write_output('n', 1)"},
             "inputs": [], "outputs": [{"name": "n", "type": "number"}], "tests": []}],
            "edges": [], "chat": [], "variables": []})
        done = self._run_and_wait(pid, {})
        run_id = done["result"]["run_id"]
        self.assertEqual(done["result"]["status"], "completed")
        _, proj = self._req("GET", f"/api/workflows/{pid}")
        self.assertEqual(proj.get("_run_unseen", {}).get("run_id"), run_id)
        self.assertEqual(proj["_run_unseen"]["status"], "completed")
        status, r = self._req("POST", f"/api/workflows/{pid}/runs/{run_id}/seen", {})
        self.assertEqual((status, r.get("ok")), (200, True))
        _, proj = self._req("GET", f"/api/workflows/{pid}")
        self.assertNotIn("_run_unseen", proj)

        store.save({**store.load(pid), "nodes": [
            {"id": "n_ok", "name": "Count", "type": "code",
             "config": {"code": "raise RuntimeError('no')"},
             "inputs": [], "outputs": [{"name": "n", "type": "number"}], "tests": []}]})
        done = self._run_and_wait(pid, {})
        run2 = done["result"]["run_id"]
        _, proj = self._req("GET", f"/api/workflows/{pid}")
        u = proj.get("_run_unseen") or {}
        self.assertEqual((u.get("run_id"), u.get("status"), u.get("waiting_on_you")),
                         (run2, "halted", False))
        self.assertTrue(u.get("verdict"))
        self._req("POST", f"/api/workflows/{pid}/runs/{run2}/seen", {})
        _, proj = self._req("GET", f"/api/workflows/{pid}")
        self.assertNotIn("_run_unseen", proj)
        from runtime import run_state
        run_state.finish(run2)

    def test_an_issue_closes_itself_when_the_step_goes_through(self):
        import os
        import tempfile
        from runtime import run_state
        from storage import store
        pid = "p_pass_later"
        marker = os.path.join(tempfile.gettempdir(), f"cryo_pass_once_{os.getpid()}")
        if os.path.exists(marker):
            os.unlink(marker)
        code = ("import os\n"
                f"m = {marker!r}\n"
                "if not os.path.exists(m):\n"
                "    open(m, 'w').close()\n"
                "    raise RuntimeError('first try')\n"
                "write_output('rows', [1])\n")
        store.save({"id": pid, "nodes": [
            {"id": "n_c", "name": "Fetch once", "type": "code",
             "config": {"code": code},
             "inputs": [], "outputs": [{"name": "rows", "type": "any"}],
             "tests": []}],
            "edges": [], "chat": [], "variables": [],
            "deliverables": [{"node": "n_c", "port": "rows", "label": "rows"}]})
        done = self._run_and_wait(pid, {})
        res = done["result"]
        self.assertEqual(res["status"], "halted", res)
        self.assertTrue(res["reason"].startswith("node threw"))
        tid = res["ticket_id"]
        self.assertEqual(store.load(pid)["tickets"][0]["status"], "open")

        done2 = self._run_and_wait(pid, {"run_id": res["run_id"]})
        self.assertEqual(done2["result"]["status"], "completed", done2["result"])
        t = next(x for x in store.load(pid)["tickets"] if x["id"] == tid)
        self.assertEqual(t["status"], "closed")
        self.assertEqual(t["resolution_note"], "went through on a later try")
        try:
            os.unlink(marker)
        except OSError:
            pass

class StaleCodeSaysSoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_version_and_turn_carry_the_build(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/version")
        v = json.loads(c.getresponse().read()); c.close()
        self.assertEqual(v["build"], server_mod.BUILD_ID)
        self.assertIn(v["stale"], (True, False))
        from storage import store
        store.save({"id": "p_build_poll", "nodes": [], "edges": [], "chat": [], "variables": []})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/workflows/p_build_poll/turn")
        snap = json.loads(c.getresponse().read()); c.close()
        self.assertEqual(snap["build"], server_mod.BUILD_ID)

    def test_a_reply_during_a_run_answers_busy(self):
        import turns
        from storage import store
        pid = "p_busy_run"
        store.save({"id": pid, "nodes": [], "edges": [], "chat": [], "variables": []})
        self.assertTrue(turns.begin(pid, "run"))
        try:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("POST", f"/api/workflows/{pid}/say",
                      body=json.dumps({"cid": "x", "kind": "typed", "text": "hello"}),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse(); data = json.loads(r.read()); c.close()
            self.assertEqual((r.status, data.get("landed"), data.get("kind")), (200, "busy", "run"))
            self.assertEqual([i for i in (store.load(pid).get("transcript") or [])], [])
        finally:
            turns.finish(pid)

class RunWaitBannerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get_workflow(self, pid):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", f"/api/workflows/{pid}")
        data = json.loads(c.getresponse().read())
        c.close()
        return data

    def _run_and_wait(self, pid, body):
        import time as _t
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", f"/api/workflows/{pid}/run", body=json.dumps(body),
                  headers={"Content-Type": "application/json"})
        c.getresponse().read()
        c.close()
        deadline = _t.time() + 30
        while _t.time() < deadline:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", f"/api/workflows/{pid}/turn")
            snap = json.loads(c.getresponse().read())
            c.close()
            if snap.get("last_seq") and not snap.get("active"):
                return [e for e in snap["events"] if e.get("type") == "done"][-1]
            _t.sleep(0.2)
        self.fail("run never finished")

    def test_halt_leaves_run_wait_and_resume_persists_the_value(self):
        from storage import store
        pid = "p_run_wait"
        store.save({"id": pid, "nodes": [
            {"id": "n1", "name": "Fetch city", "type": "code",
             "config": {"code": "write_output('greeting', 'hi ' + read_input('city'))"},
             "inputs": [{"name": "city", "type": "text"}],
             "outputs": [{"name": "greeting", "type": "text"}], "tests": []}],
            "edges": [], "chat": [],
            "variables": [{"name": "city", "value": "", "secret": False,
                           "persistent": True}]})
        done = self._run_and_wait(pid, {})
        self.assertEqual(done["result"]["reason"], "missing-value")
        run_id = done["result"]["run_id"]

        proj = self._get_workflow(pid)
        wait = proj.get("_run_wait")
        self.assertTrue(wait, "halted run must surface _run_wait on GET")
        self.assertEqual(wait["reason"], "missing-value")
        self.assertEqual(wait["node_name"], "Fetch city")
        self.assertEqual(wait["run_id"], run_id)

        self.assertEqual(wait["verdict"]["missing_fields"][0]["name"], "city")

        done = self._run_and_wait(pid, {"run_id": run_id,
                                        "entry_inputs": {"city": "Oslo"}})
        self.assertEqual(done["result"]["status"], "completed")
        proj = self._get_workflow(pid)
        self.assertNotIn("_run_wait", proj)

        var = next(v for v in proj["variables"] if v["name"] == "city")
        self.assertEqual(var["value"], "Oslo")

    def test_stopped_by_user_shows_no_wait(self):
        from runtime import run_state
        from storage import store
        pid = "p_run_stop"
        store.save({"id": pid, "nodes": [], "edges": [], "variables": [],
                    "chat": []})
        run_state.start(pid, "run_stop1", {})
        run_state.halt("run_stop1", "nX", "stopped-by-user")
        try:
            self.assertNotIn("_run_wait", self._get_workflow(pid))
        finally:
            run_state.finish("run_stop1")

    def test_halt_older_than_last_completed_run_shows_no_wait(self):
        import time as _t
        from runtime import run_state
        from storage import store
        pid = "p_run_stale"
        store.save({"id": pid, "nodes": [], "edges": [], "variables": [],
                    "chat": []})
        run_state.start(pid, "run_old1", {})
        run_state.halt("run_old1", "nX", "missing-value")
        try:
            self.assertIn("_run_wait", self._get_workflow(pid))
            p = store.load(pid)
            p["last_run_ts"] = _t.time() + 1
            store.save(p)
            self.assertNotIn("_run_wait", self._get_workflow(pid))
        finally:
            run_state.finish("run_old1")

class DeclinedApprovalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path,
                  body=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def _run_and_wait(self, pid, body):
        import time as _t
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", f"/api/workflows/{pid}/run", body=json.dumps(body),
                  headers={"Content-Type": "application/json"})
        c.getresponse().read()
        c.close()
        deadline = _t.time() + 30
        while _t.time() < deadline:
            status, snap = self._req("GET", f"/api/workflows/{pid}/turn")
            if snap.get("last_seq") and not snap.get("active"):
                return [e for e in snap["events"] if e.get("type") == "done"][-1]
            _t.sleep(0.2)
        self.fail("run never finished")

    def test_decline_ends_the_run_but_keeps_it_resumable(self):
        from runtime import run_state
        from storage import store
        pid = "p_decline"
        store.save({"id": pid, "nodes": [
            {"id": "n_w", "name": "Send rows", "type": "connector",
             "read_only": False, "external_impact": "sends rows",
             "config": {"code": "write_output('sent', True)"},
             "inputs": [], "outputs": [{"name": "sent", "type": "boolean"}],
             "tests": []}],
            "edges": [], "chat": [], "variables": []})
        done = self._run_and_wait(pid, {})
        self.assertEqual(done["result"]["reason"], "awaiting-approval")
        run_id = done["result"]["run_id"]
        _, proj = self._req("GET", f"/api/workflows/{pid}")
        self.assertEqual(proj.get("_run_wait", {}).get("run_id"), run_id)

        status, _ = self._req(
            "POST", f"/api/workflows/p_other/runs/{run_id}/decline", {})
        self.assertIn(status, (404,))

        status, r = self._req(
            "POST", f"/api/workflows/{pid}/runs/{run_id}/decline", {})
        self.assertEqual(status, 200)
        self.assertTrue(r.get("ok"))

        _, proj = self._req("GET", f"/api/workflows/{pid}")
        self.assertNotIn("_run_wait", proj)
        rec = run_state.load(run_id)
        self.assertEqual((rec.get("status"), rec.get("reason")),
                         ("halted", "approval-declined"))
        _, page = self._req("GET", f"/api/workflows/{pid}/runs")
        row = next(x for x in page["runs"] if x["run_id"] == run_id)
        self.assertEqual(row["reason"], "approval-declined")

        self.assertEqual(store.load(pid).get("tickets", []), [])

        status, _ = self._req(
            "POST", f"/api/workflows/{pid}/runs/{run_id}/decline", {})
        self.assertEqual(status, 409)

        done = self._run_and_wait(pid, {"run_id": run_id})
        self.assertEqual(done["result"]["reason"], "awaiting-approval")
        run_state.finish(run_id)

class TakeoverFollowupTest(unittest.TestCase):
    def test_unread_interjections_spawn_the_followup_and_leave_done_clean(self):
        import time as _t
        import turns
        from unittest import mock
        from tests._bootstrap import workflow as _workflow
        p = _workflow([], pid="p_follow1")
        self.assertTrue(turns.begin("p_follow1", "chat"))
        turns.interject("p_follow1", {"kind": "typed", "text": "the takeover message"})
        spawned = {}
        with mock.patch.object(server_mod, "_spawn_unread_followup",
                               lambda pid, texts: spawned.update(
                                   pid=pid, texts=list(texts))):
            server_mod._turn_body(
                p, "chat",
                lambda emit: {"content": "", "kind": "chat", "stopped": True},
                None, None, lambda ev: None)
        self.assertEqual(spawned.get("texts"), ["the takeover message"])
        snap = turns.snapshot("p_follow1")
        done = [e for e in snap["events"] if e.get("type") == "done"][-1]
        self.assertNotIn("unread", done)

    def test_the_followup_runner_claims_the_slot_and_runs_a_chat_turn(self):
        import time as _t
        import turns
        from unittest import mock
        from tests._bootstrap import workflow as _workflow
        _workflow([], pid="p_follow2")
        ran = {}

        def fake_body(workflow, kind, run_turn, pre_line, done_extra, w):
            ran.update(kind=kind, pid=workflow["id"])
            turns.finish(workflow["id"], None)

        with mock.patch.object(server_mod, "_turn_body", fake_body):
            server_mod._spawn_unread_followup("p_follow2", ["go on"])
            deadline = _t.time() + 5
            while not ran and _t.time() < deadline:
                _t.sleep(0.05)
        self.assertEqual(ran.get("kind"), "chat")
        self.assertEqual(ran.get("pid"), "p_follow2")

class InterjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()
        from storage import store
        cls.pid = "p_interject"
        store.save({"id": cls.pid, "nodes": [], "edges": [], "variables": [],
                    "transcript": [], "transcript_seq": 0,
                    "intent": {"summary": "Purpose."}})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read() or b"{}")
        c.close()
        return r.status, data

    def _start_turn(self, content, **extra):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", f"/api/workflows/{self.pid}/say",
                  body=json.dumps({"kind": "typed", "text": content, **extra}),
                  headers={"Content-Type": "application/json"})
        c.close()

    def test_delivered_shown_and_read_at_the_tool_boundary(self):
        import threading, time as _t
        import turns
        from unittest.mock import patch
        from agent import orchestrator, actions
        seen = {}

        def fake_turn(workflow, message, emit):
            emit({"type": "tool", "text": "working"})
            deadline = _t.time() + 10
            while _t.time() < deadline and not turns.unread_interjections(workflow["id"]):
                _t.sleep(0.05)

            emit({"type": "tool", "text": "still working"})

            out = actions.execute(workflow, {"name": "list_nodes", "input": {}}, emit)
            seen["note"] = out.get("user_said_meanwhile", "")
            return {"content": "done", "kind": "text"}

        with patch.object(orchestrator, "_master_key", return_value=("k", "n")), \
                patch.object(orchestrator, "handle_chat_stream", fake_turn):
            self._start_turn("start please")
            deadline = _t.time() + 10
            while _t.time() < deadline and not turns.snapshot(self.pid).get("active"):
                _t.sleep(0.05)
            status, d = self._req("POST", f"/api/workflows/{self.pid}/say",
                                  {"kind": "typed", "cid": "c-inter1",
                                   "text": "actually use the other page"})
            self.assertEqual(status, 200)
            self.assertEqual(d.get("landed"), "parked", d)
            deadline = _t.time() + 10
            while _t.time() < deadline and turns.snapshot(self.pid).get("active"):
                _t.sleep(0.05)
        self.assertIn("actually use the other page", seen.get("note", ""))
        snap = turns.snapshot(self.pid)
        shown = [e for e in snap["events"] if e.get("type") == "shown"
                 and (e.get("item") or {}).get("from") == "user"]
        landed = [e for e in shown if "other page" in (e["item"].get("text") or "")]
        self.assertTrue(landed)
        self.assertEqual(landed[0]["item"].get("cid"), "c-inter1")
        done = [e for e in snap["events"] if e.get("type") == "done"][-1]
        self.assertNotIn("unread", done)
        from storage import store
        texts = [it.get("text") for it in store.load(self.pid).get("transcript", [])]
        self.assertEqual(texts.count("actually use the other page"), 1)

    def test_unread_starts_a_server_side_followup_turn(self):
        import time as _t
        import turns
        from unittest.mock import patch
        from agent import orchestrator
        from storage import store

        seen = {}

        def fake_turn(workflow, message, emit):
            if "first_done" not in seen:
                deadline = _t.time() + 10
                while _t.time() < deadline \
                        and not turns.unread_interjections(workflow["id"]):
                    _t.sleep(0.05)
                seen["first_done"] = True
                return {"content": "ended without a tool call", "kind": "text"}
            seen["message"] = message
            return {"content": "ok", "kind": "text"}

        with patch.object(orchestrator, "_master_key", return_value=("k", "n")), \
                patch.object(orchestrator, "handle_chat_stream", fake_turn):
            self._start_turn("go")
            deadline = _t.time() + 10
            while _t.time() < deadline and not turns.snapshot(self.pid).get("active"):
                _t.sleep(0.05)
            self._req("POST", f"/api/workflows/{self.pid}/say",
                      {"kind": "typed", "text": "one more thing"})
            deadline = _t.time() + 15
            while _t.time() < deadline and "message" not in seen:
                _t.sleep(0.05)
            deadline = _t.time() + 10
            while _t.time() < deadline and turns.snapshot(self.pid).get("active"):
                _t.sleep(0.05)
        self.assertEqual(seen.get("message"), "one more thing")
        for d in [e for e in turns.snapshot(self.pid)["events"]
                  if e.get("type") == "done"]:
            self.assertNotIn("unread", d)
        count = [it.get("text") for it in store.load(self.pid)["transcript"]
                 ].count("one more thing")
        self.assertEqual(count, 1)

class ExtensionSeamOverTheWireTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {"Content-Type": "application/json"} if body is not None else {})
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, json.loads(raw) if raw else None

    def test_empty_seam_lists_nothing_and_changes_no_route(self):
        import extensions
        extensions.reset()
        extensions._LOADED = True
        try:
            status, body = self._call("GET", "/api/extensions")
            self.assertEqual(status, 200)
            self.assertEqual(body, {"items": [], "problems": []})

            status, body = self._call("GET", "/api/nothing-here")
            self.assertEqual(status, 404)
            self.assertEqual(body["error"], "unknown route")
        finally:
            extensions.reset()

    def test_an_integration_answers_every_verb_and_a_raise_is_a_plain_problem(self):
        import extensions
        extensions.reset()
        reg = extensions.Registry("demo")
        reg.route("GET", ("api", "demo", "*"), lambda ctx: (200, {"saw": ctx.wildcards[0]}))
        reg.route("POST", ("api", "demo"), lambda ctx: (201, {"got": ctx.body()}))
        reg.route("PUT", ("api", "demo"), lambda ctx: (200, {"verb": ctx.method}))
        reg.route("DELETE", ("api", "demo"), lambda ctx: (200, {"verb": ctx.method}))
        reg.route("GET", ("api", "demo-boom"),
                  lambda ctx: (_ for _ in ()).throw(RuntimeError("the service is down")))
        extensions._merge(reg)
        extensions._LOADED = True
        try:
            self.assertEqual(self._call("GET", "/api/demo/w7"), (200, {"saw": "w7"}))
            self.assertEqual(self._call("POST", "/api/demo", {"a": 1}), (201, {"got": {"a": 1}}))
            self.assertEqual(self._call("PUT", "/api/demo"), (200, {"verb": "PUT"}))
            self.assertEqual(self._call("DELETE", "/api/demo"), (200, {"verb": "DELETE"}))
            status, body = self._call("GET", "/api/demo-boom")
            self.assertEqual(status, 502)
            self.assertEqual(body["error"], "the service is down")
        finally:
            extensions.reset()

    def test_an_integrations_own_files_are_served_and_contained(self):
        import extensions
        import tempfile
        from pathlib import Path
        extensions.reset()
        web = Path(tempfile.mkdtemp()) / "web"
        web.mkdir()
        (web / "main.js").write_text("export function render() {}")
        reg = extensions.Registry("demo")
        reg.web("Demo", web)
        extensions._merge(reg)
        extensions._LOADED = True
        try:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", "/ext/demo/main.js")
            r = c.getresponse()
            text = r.read().decode()
            c.close()
            self.assertEqual(r.status, 200)
            self.assertIn("export function render", text)

            status, body = self._call("GET", "/api/extensions")
            self.assertEqual(body["items"][0]["module"], "/ext/demo/main.js")

            for bad in ("/ext/demo/../escape", "/ext/nobody/main.js", "/ext/"):
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
                c.request("GET", bad)
                r = c.getresponse()
                r.read()
                c.close()
                self.assertEqual(r.status, 404, bad)
        finally:
            extensions.reset()

class ServiceProblemsAreIssuesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.port = _start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse(); data = json.loads(r.read()); c.close()
        return r.status, data

    def test_an_unanswered_connector_raises_an_issue(self):
        import time as _t
        from runtime import run_state
        from storage import store
        pid = "p_svc_issue"
        store.save({"id": pid, "nodes": [
            {"id": "n_f", "name": "Fetch the feed", "type": "connector",
             "read_only": True, "external_impact": "reads a feed",
             "config": {"code": "raise TimeoutError('The read operation timed out')"},
             "inputs": [], "outputs": [{"name": "rows", "type": "any"}], "tests": []}],
            "edges": [], "chat": [], "variables": []})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", f"/api/workflows/{pid}/run", body=json.dumps({}),
                  headers={"Content-Type": "application/json"})
        c.getresponse().read(); c.close()
        deadline = _t.time() + 30; done = None
        while _t.time() < deadline:
            _, snap = self._req("GET", f"/api/workflows/{pid}/turn")
            if snap.get("last_seq") and not snap.get("active"):
                done = [e for e in snap["events"] if e.get("type") == "done"][-1]; break
            _t.sleep(0.2)
        self.assertIsNotNone(done)
        self.assertEqual(done["result"]["reason"], "service-unavailable")
        p = store.load(pid)
        tickets = p.get("tickets") or []
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["reason"], "service-unavailable")
        self.assertIn('The step "Fetch the feed"', tickets[0]["verdict"]["environment"][0])
        run_state.finish(done["result"]["run_id"])
