"""Tests for cascadia.depot.signing — Ed25519 sign/verify, key rotation."""
from __future__ import annotations

import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

from cascadia.depot.signing import (
    LocalSigner,
    Verifier,
    load_key_bundle,
    sign_manifest,
    verify_manifest,
)


def _make_ephemeral_signer(key_id: str = "test-2026-q1") -> tuple[LocalSigner, str]:
    """Return (LocalSigner, public_key_b64) using a temp file. No ~/.config writes."""
    private_key = Ed25519PrivateKey.generate()
    pub = private_key.public_key()
    pub_bytes = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    priv_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".key")
    tmp.write(priv_bytes)
    tmp.close()
    signer = LocalSigner(key_path=tmp.name, _key_id=key_id)
    return signer, pub_b64


def _make_verifier(key_id: str, pub_b64: str) -> Verifier:
    return Verifier.from_bundle({key_id: pub_b64})


SAMPLE_MANIFEST = {
    "type": "mission",
    "id": "test_mission",
    "version": "1.0.0",
    "name": "Test Mission",
    "description": "For signing tests only.",
    "tier_required": "lite",
    "runtime": "server",
    "author": "zyrcon-labs",
    "signed_by": "zyrcon-labs",
    "signature_algorithm": "Ed25519",
    "key_id": "test-2026-q1",
    "package_digest": "sha256:" + "a" * 64,
    "files": [],
    "capabilities": [],
    "requires_approval": [],
    "risk_level": "low",
    "operators": {"required": [], "optional": []},
    "connectors": {"required": [], "optional": []},
}


class TestLocalSigner(unittest.TestCase):

    def setUp(self):
        self.signer, self.pub_b64 = _make_ephemeral_signer("test-2026-q1")

    def test_sign_returns_bytes(self):
        sig = self.signer.sign(b"hello world")
        self.assertIsInstance(sig, bytes)
        self.assertEqual(len(sig), 64)

    def test_key_id_returned(self):
        self.assertEqual(self.signer.key_id(), "test-2026-q1")

    def test_public_key_b64_matches_verifier(self):
        pub_b64 = self.signer.public_key_b64()
        self.assertEqual(pub_b64, self.pub_b64)


class TestVerifier(unittest.TestCase):

    def setUp(self):
        self.signer, self.pub_b64 = _make_ephemeral_signer("test-2026-q1")
        self.verifier = _make_verifier("test-2026-q1", self.pub_b64)

    def test_verify_valid_signature(self):
        message = b"hello world"
        sig = self.signer.sign(message)
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        self.assertTrue(self.verifier.verify(message, sig_b64, "test-2026-q1"))

    def test_verify_tampered_message_fails(self):
        message = b"hello world"
        sig = self.signer.sign(message)
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        self.assertFalse(self.verifier.verify(b"tampered", sig_b64, "test-2026-q1"))

    def test_verify_unknown_key_id_raises(self):
        with self.assertRaises(ValueError):
            self.verifier.verify(b"msg", "AAAA", "unknown-key-id")

    def test_known_key_ids(self):
        self.assertIn("test-2026-q1", self.verifier.known_key_ids())

    def test_from_bundle_factory(self):
        v = Verifier.from_bundle({"test-2026-q1": self.pub_b64})
        self.assertIn("test-2026-q1", v.known_key_ids())


class TestSignManifest(unittest.TestCase):

    def setUp(self):
        self.signer, self.pub_b64 = _make_ephemeral_signer("test-2026-q1")

    def test_sign_manifest_adds_signature_fields(self):
        result = sign_manifest(SAMPLE_MANIFEST, self.signer)
        self.assertIn("signature", result)
        self.assertIn("signed_by", result)
        self.assertIn("signature_algorithm", result)
        self.assertIn("key_id", result)

    def test_sign_manifest_signature_algorithm_is_ed25519(self):
        result = sign_manifest(SAMPLE_MANIFEST, self.signer)
        self.assertEqual(result["signature_algorithm"], "Ed25519")

    def test_sign_manifest_key_id_matches_signer(self):
        result = sign_manifest(SAMPLE_MANIFEST, self.signer)
        self.assertEqual(result["key_id"], self.signer.key_id())

    def test_sign_manifest_signature_is_base64url_string(self):
        result = sign_manifest(SAMPLE_MANIFEST, self.signer)
        sig = result["signature"]
        self.assertIsInstance(sig, str)
        # Padding-free base64url
        self.assertNotIn("=", sig)
        self.assertNotIn("+", sig)
        self.assertNotIn("/", sig)

    def test_sign_manifest_does_not_modify_original(self):
        original = copy.deepcopy(SAMPLE_MANIFEST)
        sign_manifest(SAMPLE_MANIFEST, self.signer)
        self.assertEqual(SAMPLE_MANIFEST, original)


class TestVerifyManifest(unittest.TestCase):

    def setUp(self):
        self.signer, self.pub_b64 = _make_ephemeral_signer("test-2026-q1")
        self.verifier = _make_verifier("test-2026-q1", self.pub_b64)

    def test_verify_correctly_signed_manifest(self):
        signed = sign_manifest(SAMPLE_MANIFEST, self.signer)
        self.assertTrue(verify_manifest(signed, self.verifier))

    def test_verify_tampered_manifest_fails(self):
        signed = sign_manifest(SAMPLE_MANIFEST, self.signer)
        tampered = copy.deepcopy(signed)
        tampered["id"] = "attacker_mission"
        self.assertFalse(verify_manifest(tampered, self.verifier))

    def test_verify_tampered_version_fails(self):
        signed = sign_manifest(SAMPLE_MANIFEST, self.signer)
        tampered = copy.deepcopy(signed)
        tampered["version"] = "9.9.9"
        self.assertFalse(verify_manifest(tampered, self.verifier))

    def test_verify_unknown_key_id_raises(self):
        signed = sign_manifest(SAMPLE_MANIFEST, self.signer)
        bad_verifier = Verifier.from_bundle({})
        with self.assertRaises(ValueError):
            verify_manifest(signed, bad_verifier)


class TestKeyRotation(unittest.TestCase):
    """Two active keys — signatures from either key pass."""

    def test_both_keys_verify_independently(self):
        signer_q1, pub_q1 = _make_ephemeral_signer("test-2026-q1")
        signer_q2, pub_q2 = _make_ephemeral_signer("test-2026-q2")
        verifier = Verifier.from_bundle({
            "test-2026-q1": pub_q1,
            "test-2026-q2": pub_q2,
        })

        m1 = copy.deepcopy(SAMPLE_MANIFEST)
        m1["key_id"] = "test-2026-q1"
        signed_q1 = sign_manifest(m1, signer_q1)
        self.assertTrue(verify_manifest(signed_q1, verifier))

        m2 = copy.deepcopy(SAMPLE_MANIFEST)
        m2["key_id"] = "test-2026-q2"
        signed_q2 = sign_manifest(m2, signer_q2)
        self.assertTrue(verify_manifest(signed_q2, verifier))

    def test_expired_key_rejected(self):
        signer_old, pub_old = _make_ephemeral_signer("test-2025-q4")
        signer_new, pub_new = _make_ephemeral_signer("test-2026-q1")
        # New verifier has only the new key — old key expired
        verifier_new_only = Verifier.from_bundle({"test-2026-q1": pub_new})

        m = copy.deepcopy(SAMPLE_MANIFEST)
        m["key_id"] = "test-2025-q4"
        signed_old = sign_manifest(m, signer_old)
        with self.assertRaises(ValueError):
            verify_manifest(signed_old, verifier_new_only)


class TestLoadKeyBundle(unittest.TestCase):

    def test_load_from_file(self):
        signer, pub_b64 = _make_ephemeral_signer("test-key")
        bundle = {"test-key": pub_b64}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(bundle, f)
            path = f.name
        loaded = load_key_bundle(path)
        self.assertEqual(loaded, bundle)


class TestVerifierFromFile(unittest.TestCase):

    def test_verifier_loads_from_json_file(self):
        signer, pub_b64 = _make_ephemeral_signer("test-2026-q1")
        bundle = {"test-2026-q1": pub_b64}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(bundle, f)
            path = f.name
        verifier = Verifier(key_bundle_path=path)
        self.assertIn("test-2026-q1", verifier.known_key_ids())

        signed = sign_manifest(SAMPLE_MANIFEST, signer)
        self.assertTrue(verify_manifest(signed, verifier))

    def test_verifier_missing_bundle_file_gives_empty_key_map(self):
        verifier = Verifier(key_bundle_path="/nonexistent/bundle.json")
        self.assertEqual(verifier.known_key_ids(), [])


if __name__ == "__main__":
    unittest.main()
