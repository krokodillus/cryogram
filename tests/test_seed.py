# Tests: default-provider seeding (storage/seed.py): every fresh install gets the default cards, existing providers merge without losing keys or edited models, markers hold, and one failing step never blocks the rest of startup
from __future__ import annotations

import unittest

from tests import _bootstrap

from storage import seed

class SeedRunIsolationTest(unittest.TestCase):
    def test_one_failing_step_neither_kills_startup_nor_skips_the_rest(self):
        from storage import settings
        settings.update({"providers": [],
                         "migrations": {"default_providers": False,
                                        "default_providers_2": False}})
        calls = []
        real = seed._seed_default_providers_once

        def boom():
            calls.append("boom")
            raise RuntimeError("disk gone")

        seed._seed_default_providers_once = boom
        try:
            seed.run()
        finally:
            seed._seed_default_providers_once = real
        self.assertEqual(calls, ["boom"])

        self.assertTrue((settings.get().get("migrations") or {})
                        .get("default_providers_2"))

class DefaultProviderSeedTest(unittest.TestCase):
    def _reset(self, providers_list):
        from storage import settings
        settings.update({"providers": providers_list,
                         "migrations": {"default_providers": False}})

    def _seed(self):
        from storage import settings
        seed._seed_default_providers_once()
        return settings.get().get("providers", [])

    def test_fresh_install_gets_every_default_card(self):
        from storage import settings
        self._reset([])
        out = self._seed()
        self.assertEqual([p["id"] for p in out],
                         list(settings.DEFAULT_PROVIDER_IDS))
        by_id = {p["id"]: p for p in out}
        self.assertEqual(by_id["anthropic"]["name"], "Anthropic")
        self.assertEqual(by_id["gemini"]["adapter"], "openai")
        self.assertIn("generativelanguage.googleapis.com",
                      by_id["gemini"]["endpoint"])
        self.assertNotIn("mistral", by_id)
        for p in out:
            self.assertTrue(p["enabled"])
            self.assertEqual(p["use"], "workflow")

            self.assertEqual(p["models"], [], p["id"])
        self.assertEqual(by_id["openai"]["key_name"], "OPENAI_API_KEY")

    def test_existing_provider_merges_keeping_key_and_models(self):
        self._reset([{"adapter": "anthropic", "auth": "api-key",
                      "use": "workflow", "endpoint": "", "key_name": "MY_ANTH_KEY",
                      "tags": [], "models": [{"name": "my-edited-model",
                                              "cost": 3, "quality": 9}]}])
        out = self._seed()
        anth = next(p for p in out if p["id"] == "anthropic")
        self.assertEqual(anth["key_name"], "MY_ANTH_KEY")
        self.assertEqual([m["name"] for m in anth["models"]],
                         ["my-edited-model"])
        from storage import settings
        self.assertEqual(len(out), len(settings.DEFAULT_PROVIDER_IDS))

    def test_a_default_card_without_a_key_loses_its_starter_rows_once(self):
        from storage import secrets_store, settings
        secrets_store.set_secret("SEED_KEEP_KEY", "sk", secrets_store.OWNER_APP)
        self._reset([{"id": "anthropic", "adapter": "anthropic", "auth": "api-key",
                      "use": "workflow", "endpoint": "", "key_name": "SEED_KEEP_KEY",
                      "tags": [], "models": [{"name": "claude-x", "cost": 3, "quality": 9}]},
                     {"id": "openai", "adapter": "openai", "auth": "api-key",
                      "use": "workflow", "endpoint": "",
                      "key_name": "OPENAI_API_KEY", "tags": [],
                      "models": [{"name": "gpt-starter", "cost": 5, "quality": 5}]}])
        seed._clear_starter_models_once()
        by_id = {p["id"]: p for p in settings.get()["providers"]}
        self.assertEqual([m["name"] for m in by_id["anthropic"]["models"]], ["claude-x"])
        self.assertEqual(by_id["openai"]["models"], [])
        self.assertTrue(settings.get()["migrations"]["starter_models_cleared"])
        by_id["openai"]["models"] = [{"name": "typed-later", "cost": 5, "quality": 5}]
        settings.update({"providers": list(by_id.values())})
        seed._clear_starter_models_once()
        self.assertEqual([m["name"] for m in next(p for p in settings.get()["providers"] if p["id"] == "openai")["models"]], ["typed-later"])

    def test_gemini_endpoint_provider_merges_onto_gemini(self):
        self._reset([{"adapter": "openai", "auth": "api-key", "use": "workflow",
                      "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/",
                      "key_name": "MY_GEM_KEY", "tags": [], "models": []}])
        out = self._seed()
        gem = next(p for p in out if p["id"] == "gemini")
        self.assertEqual(gem["key_name"], "MY_GEM_KEY")
        from storage import settings
        self.assertEqual(len(out), len(settings.DEFAULT_PROVIDER_IDS))

    def test_local_and_custom_endpoint_providers_stay_custom(self):
        self._reset([{"adapter": "openai", "auth": "api-key", "use": "workflow",
                      "endpoint": "http://localhost:11434/v1",
                      "key_name": "OLLAMA_KEY", "tags": ["local"], "models": []}])
        out = self._seed()
        from storage import settings
        self.assertEqual(len(out), len(settings.DEFAULT_PROVIDER_IDS) + 1)
        custom = out[-1]
        self.assertTrue(custom["id"].startswith("p_"))
        self.assertIn("localhost", custom["name"])
        self.assertEqual(custom["key_name"], "OLLAMA_KEY")

    def test_builder_is_stamped_and_key_names_never_collide(self):
        self._reset([{"adapter": "anthropic", "auth": "api-key",
                      "use": "builder", "endpoint": "",
                      "key_name": "ANTHROPIC_API_KEY", "tags": [],
                      "models": [{"name": "claude-x"}]}])
        out = self._seed()
        self.assertEqual(out[0]["id"], "builder")
        self.assertEqual(out[0]["key_name"], "ANTHROPIC_API_KEY")
        anth = next(p for p in out if p["id"] == "anthropic")
        self.assertEqual(anth["key_name"], "ANTHROPIC_2_API_KEY")

    def test_it_runs_once(self):
        from storage import settings
        self._reset([])
        self._seed()
        settings.update({"providers": []})
        seed._seed_default_providers_once()
        self.assertEqual(settings.get().get("providers"), [])

    def test_second_seed_adds_only_the_missing_defaults(self):
        from storage import settings
        old_three = [
            {"id": "anthropic", "name": "Anthropic", "adapter": "anthropic",
             "auth": "api-key", "use": "workflow", "endpoint": "",
             "key_name": "ANTHROPIC_API_KEY", "tags": [],
             "models": [{"name": "claude-x"}]},
            {"id": "openai", "name": "OpenAI", "adapter": "openai",
             "auth": "api-key", "use": "workflow", "endpoint": "",
             "key_name": "OPENAI_API_KEY", "tags": [], "models": []},
            {"id": "gemini", "name": "Gemini", "adapter": "openai",
             "auth": "api-key", "use": "workflow",
             "endpoint": settings.GEMINI_OPENAI_BASE,
             "key_name": "GEMINI_API_KEY", "tags": [], "models": []},
            {"id": "p_mymistral", "name": "My Mistral", "adapter": "openai",
             "auth": "api-key", "use": "workflow",
             "endpoint": "https://api.mistral.ai/v1",
             "key_name": "MY_MISTRAL_KEY", "tags": [],
             "models": [{"name": "my-tuned-mistral"}]},
        ]
        settings.update({"providers": old_three,
                         "migrations": {"default_providers": True,
                                        "default_providers_2": False}})
        seed._seed_more_default_providers_once()
        out = settings.get()["providers"]
        ids = [p["id"] for p in out]
        self.assertEqual(ids, list(settings.DEFAULT_PROVIDER_IDS) + ["p_mymistral"])
        mine = next(p for p in out if p["id"] == "p_mymistral")
        self.assertEqual(mine["key_name"], "MY_MISTRAL_KEY")
        self.assertEqual([m["name"] for m in mine["models"]], ["my-tuned-mistral"])
        anth = next(p for p in out if p["id"] == "anthropic")
        self.assertEqual(anth["key_name"], "ANTHROPIC_API_KEY")
        self.assertEqual([m["name"] for m in anth["models"]], ["claude-x"])

        settings.update({"providers": []})
        seed._seed_more_default_providers_once()
        self.assertEqual(settings.get()["providers"], [])
