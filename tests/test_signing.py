# Tests for backend/signing.py: the release trust anchor: canonical-JSON Ed25519, fail-closed once the public key is pinned, re-serialisation-proof
from __future__ import annotations

import json
import unittest
from unittest import mock

from tests import _bootstrap

import config
import signing

def _keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    from cryptography.hazmat.primitives import serialization
    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.Raw,
                             serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return priv, pub

class SignVerify(unittest.TestCase):
    def setUp(self):
        self.priv, self.pub = _keypair()

    def test_roundtrip(self):
        doc = {"latest_version": "0.200", "platforms": {"windows": {"sha256": "x"}}}
        signed = signing.sign(doc, self.priv)
        self.assertTrue(signing.verify(signed, self.pub))

    def test_survives_reserialisation(self):
        signed = signing.sign({"b": 1, "a": [1, 2], "n": "ø"}, self.priv)
        reserialised = json.loads(json.dumps(signed, indent=4))
        shuffled = dict(reversed(list(reserialised.items())))
        self.assertTrue(signing.verify(shuffled, self.pub))

    def test_tamper_refused(self):
        signed = signing.sign({"latest_version": "0.200"}, self.priv)
        forged = {**signed, "latest_version": "0.999"}
        self.assertFalse(signing.verify(forged, self.pub))

    def test_unsigned_refused_once_pinned(self):
        self.assertFalse(signing.verify({"latest_version": "0.200"}, self.pub))
        self.assertFalse(signing.verify({"sig": {"alg": "ed25519", "sig": "00"},
                                         "x": 1}, self.pub))
        self.assertFalse(signing.verify("not a dict", self.pub))

    def test_wrong_key_refused(self):
        _, other_pub = _keypair()
        signed = signing.sign({"x": 1}, self.priv)
        self.assertFalse(signing.verify(signed, other_pub))

    def test_no_pin_means_not_adopted(self):
        self.assertTrue(signing.verify({"anything": True}, ""))
        with mock.patch.object(config, "RELEASE_PUBKEY", ""):
            self.assertTrue(signing.verify({"anything": True}))

if __name__ == "__main__":
    unittest.main()
