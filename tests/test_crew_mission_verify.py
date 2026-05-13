"""Tests for CrewService._verify_mission_package and _verify_mission_signature."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
import tempfile

from cascadia.depot.canonicalization import canonical_file_bytes, compute_package_digest
from cascadia.depot.signing import Verifier, sign_manifest
from cascadia.registry.crew import CrewService


# ── Test helpers ──────────────────────────────────────────────────────────────

def _make_signer_and_verifier(key_id: str = "test-2026-q1"):
    """Return (private_key, Verifier) with an ephemeral keypair."""
    private_key = Ed25519PrivateKey.generate()
    pub = private_key.public_key()
    pub_bytes = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    priv_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".key")
    tmp.write(priv_bytes)
    tmp.close()
    from cascadia.depot.signing import LocalSigner
    signer = LocalSigner(key_path=tmp.name, _key_id=key_id)
    verifier = Verifier.from_bundle({key_id: pub_b64})
    return signer, verifier


def _make_mission_zip(mission_json: dict, extra_files: dict | None = None) -> bytes:
    """Build a zip containing mission.json and any extra files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("mission.json", json.dumps(mission_json))
        for name, content in (extra_files or {}).items():
            if isinstance(content, str):
                content = content.encode()
            zf.writestr(name, content)
    return buf.getvalue()


def _make_valid_package_with_files(signer, files: dict[str, bytes]) -> tuple[dict, bytes]:
    """Create a signed manifest + zip for the given files dict {path: raw_bytes}."""
    canonical_map = {path: canonical_file_bytes(path, raw) for path, raw in files.items()}
    pkg_digest = compute_package_digest(canonical_map)
    files_list = [
        {
            "path": path,
            "sha256": hashlib.sha256(canonical_map[path]).hexdigest(),
            "size_bytes": len(canonical_map[path]),
        }
        for path in sorted(files.keys())
    ]
    manifest_unsigned = {
        "type": "mission",
        "id": "test_mission",
        "version": "1.0.0",
        "name": "Test Mission",
        "description": "Mission for verify tests.",
        "tier_required": "lite",
        "runtime": "server",
        "author": "zyrcon-labs",
        "signed_by": signer.key_id(),
        "signature_algorithm": "Ed25519",
        "key_id": signer.key_id(),
        "package_digest": pkg_digest,
        "files": files_list,
        "capabilities": [],
        "requires_approval": [],
        "risk_level": "low",
        "industries": [],
        "operators": {"required": [], "optional": []},
        "connectors": {"required": [], "optional": []},
        "schedules": [],
        "approval_flows": [],
        "database": {"schema_file": "data/schema.sql", "owned_tables": []},
        "workflows": {},
        "events": {"produces": [], "consumes": []},
        "billing": {"included_in": ["lite"], "available_as_addon": False, "addon_price_monthly": None},
        "limits": {"lite": {"enabled": True, "mode": "lite"}},
        "prism": {"nav_label": "Test", "schema": "ui/prism.json"},
        "mobile": {"schema": "ui/mobile.json"},
    }
    manifest = sign_manifest(manifest_unsigned, signer)
    zip_bytes = _make_mission_zip(manifest, files)
    return manifest, zip_bytes


# ── Tests for _verify_mission_package ─────────────────────────────────────────

class TestVerifyMissionPackage(unittest.TestCase):

    def setUp(self):
        self.signer, self.verifier = _make_signer_and_verifier()
        self.files = {
            "workflows/main.json": b'{"steps":[]}\n',
        }

    def test_valid_package_no_errors(self):
        manifest, zip_bytes = _make_valid_package_with_files(self.signer, self.files)
        errors = CrewService._verify_mission_package(zip_bytes, manifest)
        self.assertEqual(errors, [], errors)

    def test_file_hash_mismatch_detected(self):
        manifest, _ = _make_valid_package_with_files(self.signer, self.files)
        # Build zip with tampered file content
        tampered_zip = _make_mission_zip(manifest, {"workflows/main.json": b'{"TAMPERED":true}\n'})
        errors = CrewService._verify_mission_package(tampered_zip, manifest)
        self.assertTrue(any("file_hash_mismatch" in e for e in errors), errors)

    def test_extra_file_in_zip_detected(self):
        manifest, _ = _make_valid_package_with_files(self.signer, self.files)
        # Zip with an extra file not in files[]
        extra_zip = _make_mission_zip(
            manifest,
            {"workflows/main.json": b'{"steps":[]}\n', "extra/file.txt": b"extra"},
        )
        errors = CrewService._verify_mission_package(extra_zip, manifest)
        self.assertTrue(any("extra_files_in_package" in e for e in errors), errors)

    def test_missing_declared_file_detected(self):
        manifest, _ = _make_valid_package_with_files(self.signer, self.files)
        # Zip without the workflow file
        empty_zip = _make_mission_zip(manifest, {})
        errors = CrewService._verify_mission_package(empty_zip, manifest)
        self.assertTrue(any("missing_files" in e for e in errors), errors)

    def test_package_digest_mismatch_detected(self):
        manifest, zip_bytes = _make_valid_package_with_files(self.signer, self.files)
        bad_manifest = dict(manifest)
        bad_manifest["package_digest"] = "sha256:" + "0" * 64
        errors = CrewService._verify_mission_package(zip_bytes, bad_manifest)
        self.assertTrue(any("package_digest_mismatch" in e for e in errors), errors)

    def test_reserved_paths_stripped(self):
        """DS_Store and __MACOSX in zip are silently ignored."""
        manifest, _ = _make_valid_package_with_files(self.signer, self.files)
        zip_with_junk = _make_mission_zip(
            manifest,
            {
                "workflows/main.json": b'{"steps":[]}\n',
                ".DS_Store": b"garbage",
                "__MACOSX/.cache": b"garbage",
            },
        )
        errors = CrewService._verify_mission_package(zip_with_junk, manifest)
        self.assertEqual(errors, [], errors)

    def test_not_a_zip_file(self):
        errors = CrewService._verify_mission_package(b"not a zip", {})
        self.assertTrue(len(errors) > 0)


# ── Tests for _verify_mission_signature ───────────────────────────────────────

class TestVerifyMissionSignature(unittest.TestCase):

    def setUp(self):
        self.signer, self.verifier = _make_signer_and_verifier()
        self.files = {}

    def _make_signed_manifest(self):
        manifest, _ = _make_valid_package_with_files(self.signer, self.files)
        return manifest

    def test_valid_signature_returns_true(self):
        manifest = self._make_signed_manifest()
        ok, error = CrewService._verify_mission_signature(manifest, self.verifier)
        self.assertTrue(ok, f"Expected True, got error: {error!r}")
        self.assertEqual(error, "")

    def test_invalid_signature_returns_false(self):
        manifest = self._make_signed_manifest()
        tampered = dict(manifest)
        tampered["signature"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        ok, error = CrewService._verify_mission_signature(tampered, self.verifier)
        self.assertFalse(ok)
        self.assertEqual(error, "invalid_signature")

    def test_tampered_field_signature_fails(self):
        manifest = self._make_signed_manifest()
        tampered = dict(manifest)
        tampered["id"] = "ATTACKER"
        ok, error = CrewService._verify_mission_signature(tampered, self.verifier)
        self.assertFalse(ok)

    def test_unknown_key_id_returns_error(self):
        manifest = self._make_signed_manifest()
        empty_verifier = Verifier.from_bundle({})
        ok, error = CrewService._verify_mission_signature(manifest, empty_verifier)
        self.assertFalse(ok)
        self.assertEqual(error, "unknown_key_id")


if __name__ == "__main__":
    unittest.main()
