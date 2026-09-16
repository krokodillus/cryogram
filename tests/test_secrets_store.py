# Tests for storage/secrets_store.py: encryption at rest (enc1: AES-GCM, key outside the data dir, undecryptable = unset, plaintext legacy rows still readable)
from __future__ import annotations

import os
import unittest
from unittest import mock

from tests import _bootstrap

from storage import db
from storage import secrets_store

APP = secrets_store.OWNER_APP

class RoundTrip(unittest.TestCase):
    def test_set_get_roundtrip(self):
        secrets_store.set_secret("t_enc_a", "hunter2", APP)
        self.assertEqual(secrets_store.get_secret("t_enc_a", APP), "hunter2")
        self.assertTrue(secrets_store.has_secret("t_enc_a", APP))

    def test_stored_form_is_ciphertext(self):
        secrets_store.set_secret("t_enc_b", "sk-live-value", APP)
        stored = db.secret_get(APP, "t_enc_b")
        self.assertTrue(stored.startswith("enc1:"))
        self.assertNotIn("sk-live-value", stored)

    def test_each_write_gets_a_fresh_nonce(self):
        secrets_store.set_secret("t_enc_c1", "same", APP)
        secrets_store.set_secret("t_enc_c2", "same", APP)
        self.assertNotEqual(db.secret_get(APP, "t_enc_c1"), db.secret_get(APP, "t_enc_c2"))

    def test_delete(self):
        secrets_store.set_secret("t_enc_d", "x", APP)
        secrets_store.delete_secret("t_enc_d", APP)
        self.assertIsNone(secrets_store.get_secret("t_enc_d", APP))
        self.assertFalse(secrets_store.has_secret("t_enc_d", APP))

class ForgetKey(unittest.TestCase):
    def test_forget_key_removes_the_file_and_mints_fresh(self):
        import os as _os
        from pathlib import Path as _P
        secrets_store.set_secret("t_forget", "value", APP)
        before = secrets_store._key()
        keyfile = _P(_os.environ["CRYOGRAM_SECRET_KEY_FILE"])
        self.assertTrue(keyfile.exists())
        secrets_store.forget_key()
        self.assertFalse(keyfile.exists())
        self.assertNotEqual(secrets_store._key(), before)
        self.assertIsNone(secrets_store.get_secret("t_forget", APP))

class OneOwnerPerRow(unittest.TestCase):
    def test_same_name_two_workflows_two_rows(self):
        a, b = secrets_store.workflow_owner("wfl_a"), secrets_store.workflow_owner("wfl_b")
        secrets_store.set_secret("shared_name", "value-of-a", a)
        self.assertIsNone(secrets_store.get_secret("shared_name", b))
        self.assertFalse(secrets_store.has_secret("shared_name", b))
        secrets_store.set_secret("shared_name", "value-of-b", b)
        self.assertEqual(secrets_store.get_secret("shared_name", a), "value-of-a")
        self.assertEqual(secrets_store.get_secret("shared_name", b), "value-of-b")
        secrets_store.delete_secret("shared_name", a)
        self.assertEqual(secrets_store.get_secret("shared_name", b), "value-of-b")
        self.assertEqual(secrets_store.names(a), [])
        self.assertIn("shared_name", secrets_store.names(b))
        secrets_store.delete_secret("shared_name", b)

    def test_owner_is_required(self):
        with self.assertRaises(TypeError):
            secrets_store.set_secret("t_no_owner", "v")
        with self.assertRaises(ValueError):
            secrets_store.get_secret("t_no_owner", "")

    def test_all_values_is_nameless(self):
        secrets_store.set_secret("t_allv", "the-value-9z", secrets_store.workflow_owner("wfl_v"))
        vals = secrets_store.all_values()
        self.assertIn("the-value-9z", vals)
        self.assertTrue(all(isinstance(v, str) for v in vals))
        self.assertNotIn("t_allv", vals)
        self.assertNotIn("the-value-9z", secrets_store.all_values(min_len=40))
        secrets_store.delete_secret("t_allv", secrets_store.workflow_owner("wfl_v"))

class LegacyAndLostKey(unittest.TestCase):
    def test_plaintext_legacy_row_reads_through(self):
        db.secret_set(APP, "t_plain", "legacy-value")
        self.assertEqual(secrets_store.get_secret("t_plain", APP), "legacy-value")

    def test_wrong_key_reads_as_unset_never_crashes(self):
        secrets_store.set_secret("t_lost", "will-be-lost", APP)
        with mock.patch.object(secrets_store, "_key_cache", os.urandom(32)):
            self.assertIsNone(secrets_store.get_secret("t_lost", APP))
            self.assertTrue(secrets_store.has_secret("t_lost", APP))

    def test_truncated_row_reads_as_unset(self):
        db.secret_set(APP, "t_trunc", "enc1:AAAA")
        self.assertIsNone(secrets_store.get_secret("t_trunc", APP))

class KeyHandling(unittest.TestCase):
    def test_key_is_stable_across_cache_resets(self):
        secrets_store.set_secret("t_key_stable", "v1", APP)
        with mock.patch.object(secrets_store, "_key_cache", None):
            self.assertEqual(secrets_store.get_secret("t_key_stable", APP), "v1")

    def test_key_file_is_owner_only(self):
        secrets_store._key()
        path = secrets_store._fallback_key_file()
        self.assertTrue(path.exists())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_key_file_lives_outside_the_data_dir(self):
        import config
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop("CRYOGRAM_SECRET_KEY_FILE", None)
            default = secrets_store._fallback_key_file()
        if os.name != "nt":
            self.assertNotIn(str(config.DATA_DIR), str(default))

if __name__ == "__main__":
    unittest.main()
