"""Integration tests for the full mission package pipeline.

Tests the complete sign → package → verify → install → uninstall flow using
real Ed25519 keys, real zip extraction, real MissionRegistry writes, and
real STITCH in-process registration (no live services required).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cascadia.depot.canonicalization import (
    canonical_file_bytes,
    compute_package_digest,
    canonical_manifest_bytes,
)
from cascadia.depot.signing import (
    LocalSigner,
    Verifier,
    sign_manifest,
    verify_manifest,
)
from cascadia.depot.kill_switch import InMemoryKillSwitchProvider, NoopKillSwitchProvider
from cascadia.missions.manifest import MissionManifest
from cascadia.missions.registry import MissionRegistry
from cascadia.registry.crew import CrewService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ephemeral_signer_verifier(key_id: str = "int-test-2026"):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, PrivateFormat, NoEncryption,
    )
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.urlsafe_b64encode(
        priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).rstrip(b"=").decode()
    priv_bytes = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".key")
    tmp.write(priv_bytes)
    tmp.close()
    signer = LocalSigner(key_path=tmp.name, _key_id=key_id)
    verifier = Verifier.from_bundle({key_id: pub_b64})
    return signer, verifier


def _build_package(signer, mission_id: str = "acme_crm",
                   version: str = "1.0.0",
                   workflows: dict | None = None,
                   tier: str = "lite") -> bytes:
    """Build a correctly signed mission zip package."""
    workflows = workflows or {}
    payload_files: dict[str, bytes] = {}
    wf_map: dict[str, str] = {}
    for wf_id, wf_json in workflows.items():
        rel = f"workflows/{wf_id}.json"
        raw = wf_json if isinstance(wf_json, bytes) else json.dumps(wf_json).encode()
        payload_files[rel] = raw
        wf_map[wf_id] = rel

    canonical = {p: canonical_file_bytes(p, b) for p, b in payload_files.items()}
    pkg_digest = compute_package_digest(canonical)
    files_list = [
        {
            "path": p,
            "sha256": hashlib.sha256(canonical[p]).hexdigest(),
            "size_bytes": len(canonical[p]),
        }
        for p in sorted(canonical.keys())
    ]
    manifest_unsigned = {
        "type": "mission",
        "id": mission_id,
        "version": version,
        "name": f"{mission_id} Mission",
        "description": "Integration test mission.",
        "tier_required": tier,
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
        "workflows": wf_map,
        "events": {"produces": [], "consumes": []},
        "billing": {"included_in": [tier], "available_as_addon": False,
                    "addon_price_monthly": None},
        "limits": {tier: {"enabled": True, "mode": tier}},
        "prism": {"nav_label": "Test", "schema": "ui/prism.json"},
        "mobile": {"schema": "ui/mobile.json"},
    }
    manifest = sign_manifest(manifest_unsigned, signer)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("mission.json", json.dumps(manifest))
        for path, content in payload_files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _make_crew(tmp_path, verifier: Verifier, monkeypatch) -> CrewService:
    import cascadia.registry.crew as crew_module
    monkeypatch.setattr(crew_module, "_OPERATORS_DIR", tmp_path / "operators")
    mock_rt = MagicMock()
    mock_rt.logger = MagicMock()
    svc = CrewService.__new__(CrewService)
    svc.registry = {}
    svc.runtime = mock_rt
    svc._config = {
        "operators_registry_path": str(tmp_path / "registry.json"),
        "database_path": str(tmp_path / "cascadia.db"),
        "missions": {"packages_root": str(tmp_path / "missions")},
    }
    svc._kill_switch = NoopKillSwitchProvider()
    svc._verifier = verifier
    return svc


def _fake_license_ok():
    class _R:
        status = 200
        def read(self): return json.dumps({"ok": True}).encode()
        def __enter__(self): return self
        def __exit__(self, *_): pass
    return _R()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSignPackageVerifyPipeline:
    """Phase 1-4: signing, canonicalization, and verification layer."""

    def test_sign_then_verify_roundtrip(self):
        signer, verifier = _ephemeral_signer_verifier()
        manifest = {"type": "mission", "id": "roundtrip", "version": "1.0.0"}
        signed = sign_manifest(manifest, signer)
        assert "signature" in signed
        assert verify_manifest(signed, verifier) is True

    def test_tampered_manifest_fails_verification(self):
        signer, verifier = _ephemeral_signer_verifier()
        manifest = {"type": "mission", "id": "tamper_test", "version": "1.0.0"}
        signed = sign_manifest(manifest, signer)
        tampered = dict(signed)
        tampered["id"] = "ATTACKER"
        assert verify_manifest(tampered, verifier) is False

    def test_package_digest_is_deterministic(self):
        files = {
            "workflows/main.json": b'{"steps":[]}\n',
            "data/schema.sql": b"CREATE TABLE x (id INT);",
        }
        canonical = {p: canonical_file_bytes(p, b) for p, b in files.items()}
        d1 = compute_package_digest(canonical)
        d2 = compute_package_digest(canonical)
        assert d1 == d2
        assert d1.startswith("sha256:")

    def test_crlf_normalized_in_digest(self):
        unix = {"file.json": b'{"a":1}\n'}
        crlf = {"file.json": b'{"a":1}\r\n'}
        c_unix = {p: canonical_file_bytes(p, b) for p, b in unix.items()}
        c_crlf = {p: canonical_file_bytes(p, b) for p, b in crlf.items()}
        assert compute_package_digest(c_unix) == compute_package_digest(c_crlf)

    def test_binary_file_not_normalized(self):
        raw = b'\x00\x01\x02\r\n\xff'
        result = canonical_file_bytes("image.png", raw)
        assert result == raw  # binary untouched


class TestPackageVerification:
    """Phase 5: _verify_mission_package and _verify_mission_signature."""

    def test_valid_package_no_errors(self):
        signer, verifier = _ephemeral_signer_verifier()
        zip_bytes = _build_package(signer)
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(zip_bytes)).read("mission.json"))
        errors = CrewService._verify_mission_package(zip_bytes, manifest)
        assert errors == [], errors

    def test_file_hash_tamper_detected(self):
        signer, verifier = _ephemeral_signer_verifier()
        zip_bytes = _build_package(signer, workflows={"main": {"steps": []}})
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(zip_bytes)).read("mission.json"))
        # Rebuild zip with tampered workflow content
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mission.json", json.dumps(manifest))
            zf.writestr("workflows/main.json", b'{"TAMPERED": true}')
        errors = CrewService._verify_mission_package(buf.getvalue(), manifest)
        assert any("file_hash_mismatch" in e for e in errors)

    def test_signature_verified_correct_key(self):
        signer, verifier = _ephemeral_signer_verifier()
        zip_bytes = _build_package(signer)
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(zip_bytes)).read("mission.json"))
        ok, err = CrewService._verify_mission_signature(manifest, verifier)
        assert ok is True
        assert err == ""

    def test_signature_rejected_wrong_key(self):
        signer, _ = _ephemeral_signer_verifier("key-a")
        _, wrong_verifier = _ephemeral_signer_verifier("key-b")
        zip_bytes = _build_package(signer)
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(zip_bytes)).read("mission.json"))
        ok, err = CrewService._verify_mission_signature(manifest, wrong_verifier)
        assert ok is False
        assert err in ("unknown_key_id", "invalid_signature")

    def test_reserved_paths_ignored(self):
        signer, verifier = _ephemeral_signer_verifier()
        zip_bytes = _build_package(signer)
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(zip_bytes)).read("mission.json"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for info in zipfile.ZipFile(io.BytesIO(zip_bytes)).infolist():
                zf.writestr(info, zipfile.ZipFile(io.BytesIO(zip_bytes)).read(info.filename))
            zf.writestr(".DS_Store", b"garbage")
            zf.writestr("__MACOSX/.hidden", b"garbage")
        errors = CrewService._verify_mission_package(buf.getvalue(), manifest)
        assert errors == [], errors


class TestInstallMissionEndToEnd:
    """Phase 6: install_mission full pipeline without live services."""

    def test_install_creates_files_on_disk(self, tmp_path, monkeypatch):
        signer, verifier = _ephemeral_signer_verifier()
        svc = _make_crew(tmp_path, verifier, monkeypatch)
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_bytes = _build_package(signer, workflows={"main": {"steps": []}})
        status, body = svc.install_mission({"_zip_bytes": zip_bytes})
        assert status == 201
        install_dir = tmp_path / "missions" / "acme_crm"
        assert install_dir.exists()
        assert (install_dir / "mission.json").exists()
        assert (install_dir / "workflows" / "main.json").exists()

    def test_install_writes_registry_entry(self, tmp_path, monkeypatch):
        signer, verifier = _ephemeral_signer_verifier()
        svc = _make_crew(tmp_path, verifier, monkeypatch)
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_bytes = _build_package(signer)
        svc.install_mission({"_zip_bytes": zip_bytes})
        reg_file = tmp_path / "missions" / "missions_registry.json"
        assert reg_file.exists()
        data = json.loads(reg_file.read_text())
        installed_ids = [
            m.get("id") for m in data["installed"] if isinstance(m, dict)
        ]
        assert "acme_crm" in installed_ids

    def test_reinstall_over_existing_mission(self, tmp_path, monkeypatch):
        signer, verifier = _ephemeral_signer_verifier()
        svc = _make_crew(tmp_path, verifier, monkeypatch)
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_v1 = _build_package(signer, version="1.0.0")
        zip_v2 = _build_package(signer, version="2.0.0")
        svc.install_mission({"_zip_bytes": zip_v1})
        status, body = svc.install_mission({"_zip_bytes": zip_v2})
        assert status == 201
        assert body["version"] == "2.0.0"
        reg_file = tmp_path / "missions" / "missions_registry.json"
        data = json.loads(reg_file.read_text())
        entries = [m for m in data["installed"] if isinstance(m, dict) and m.get("id") == "acme_crm"]
        assert len(entries) == 1
        assert entries[0]["version"] == "2.0.0"

    def test_kill_switch_blocks_install(self, tmp_path, monkeypatch):
        signer, verifier = _ephemeral_signer_verifier()
        svc = _make_crew(tmp_path, verifier, monkeypatch)
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        ks = InMemoryKillSwitchProvider()
        ks.revoke("acme_crm", "1.0.0")
        svc._kill_switch = ks
        zip_bytes = _build_package(signer)
        status, body = svc.install_mission({"_zip_bytes": zip_bytes})
        assert status == 403
        assert body["error"] == "package_revoked"
        assert not (tmp_path / "missions" / "acme_crm").exists()

    def test_wrong_key_blocks_install(self, tmp_path, monkeypatch):
        signer, _ = _ephemeral_signer_verifier("key-a")
        _, wrong_verifier = _ephemeral_signer_verifier("key-b")
        svc = _make_crew(tmp_path, wrong_verifier, monkeypatch)
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_bytes = _build_package(signer)
        status, body = svc.install_mission({"_zip_bytes": zip_bytes})
        assert status == 400
        assert body["error"] in ("unknown_key_id", "invalid_signature")

    def test_tampered_payload_blocks_install(self, tmp_path, monkeypatch):
        signer, verifier = _ephemeral_signer_verifier()
        svc = _make_crew(tmp_path, verifier, monkeypatch)
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_bytes = _build_package(signer, workflows={"main": {"steps": []}})
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(zip_bytes)).read("mission.json"))
        # Tamper the workflow file
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mission.json", json.dumps(manifest))
            zf.writestr("workflows/main.json", b'{"TAMPERED": true}')
        status, body = svc.install_mission({"_zip_bytes": buf.getvalue()})
        assert status == 400
        assert body["error"] in ("package_digest_mismatch", "file_hash_mismatch",
                                 "package_verification_failed")

    def test_stitch_registration_in_memory(self, tmp_path, monkeypatch):
        """When STITCH registers, stitch_pending is False in response."""
        signer, verifier = _ephemeral_signer_verifier()
        svc = _make_crew(tmp_path, verifier, monkeypatch)

        def _urlopen(req, timeout=None):
            if "license" in req.full_url:
                return _fake_license_ok()
            class _R:
                status = 200
                def read(self): return json.dumps({
                    "registered_workflow_ids": ["acme_crm.main"],
                    "failed": [],
                }).encode()
                def __enter__(self): return self
                def __exit__(self, *_): pass
            return _R()

        monkeypatch.setattr("cascadia.registry.crew._urllib_request.urlopen", _urlopen)
        zip_bytes = _build_package(signer, workflows={"main": {"steps": []}})
        status, body = svc.install_mission({"_zip_bytes": zip_bytes})
        assert status == 201
        assert body["stitch_registered"] is True
        assert body["stitch_pending"] is False


class TestUninstallMissionEndToEnd:
    """Uninstall pipeline integration tests."""

    def _install(self, svc, signer, monkeypatch):
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_bytes = _build_package(signer)
        svc.install_mission({"_zip_bytes": zip_bytes})

    def test_dry_run_reports_install_path(self, tmp_path, monkeypatch):
        signer, verifier = _ephemeral_signer_verifier()
        svc = _make_crew(tmp_path, verifier, monkeypatch)
        self._install(svc, signer, monkeypatch)
        status, body = svc.uninstall_mission({"mission_id": "acme_crm", "dry_run": True})
        assert status == 200
        assert body["dry_run"] is True
        assert "acme_crm" in body["install_path"]
        assert (tmp_path / "missions" / "acme_crm").exists()

    def test_confirmed_removes_files_and_registry(self, tmp_path, monkeypatch):
        signer, verifier = _ephemeral_signer_verifier()
        svc = _make_crew(tmp_path, verifier, monkeypatch)
        self._install(svc, signer, monkeypatch)
        status, body = svc.uninstall_mission({
            "mission_id": "acme_crm", "dry_run": False, "confirmed": True
        })
        assert status == 200
        assert body["uninstalled"] == "acme_crm"
        assert not (tmp_path / "missions" / "acme_crm").exists()
        reg_file = tmp_path / "missions" / "missions_registry.json"
        data = json.loads(reg_file.read_text())
        assert not any(
            isinstance(m, dict) and m.get("id") == "acme_crm"
            for m in data.get("installed", [])
        )

    def test_uninstall_without_confirmation_rejected(self, tmp_path, monkeypatch):
        signer, verifier = _ephemeral_signer_verifier()
        svc = _make_crew(tmp_path, verifier, monkeypatch)
        self._install(svc, signer, monkeypatch)
        status, body = svc.uninstall_mission({
            "mission_id": "acme_crm", "dry_run": False, "confirmed": False
        })
        assert status == 400
        assert body["error"] == "confirmation_required"
        assert (tmp_path / "missions" / "acme_crm").exists()

    def test_uninstall_not_installed_returns_404(self, tmp_path, monkeypatch):
        signer, verifier = _ephemeral_signer_verifier()
        svc = _make_crew(tmp_path, verifier, monkeypatch)
        status, body = svc.uninstall_mission({
            "mission_id": "never_installed", "dry_run": False, "confirmed": True
        })
        assert status == 404


class TestStitchRegisterMission:
    """STITCH register_mission route integration tests."""

    def _make_stitch(self):
        from unittest.mock import MagicMock
        from cascadia.automation.stitch import StitchService, WorkflowDefinition
        svc = StitchService.__new__(StitchService)
        svc._lock = __import__("threading").Lock()
        svc._workflows = {}
        return svc

    def test_register_mission_inline_workflows(self, tmp_path):
        svc = self._make_stitch()
        wf_file = tmp_path / "main.json"
        wf_file.write_text(json.dumps({
            "name": "Main Workflow",
            "description": "Test wf",
            "steps": [
                {"id": "step1", "operator": "scout", "action": "crm.read"}
            ]
        }))
        status, body = svc.register_mission({
            "mission_id": "acme_crm",
            "install_path": str(tmp_path),
            "workflows": {"main": "main.json"},
            "manifest": {"description": "Test"},
        })
        assert status == 200
        assert "acme_crm.main" in body["registered_workflow_ids"]
        assert "acme_crm.main" in svc._workflows

    def test_register_mission_missing_file_reports_failed(self, tmp_path):
        svc = self._make_stitch()
        status, body = svc.register_mission({
            "mission_id": "acme_crm",
            "install_path": str(tmp_path),
            "workflows": {"main": "nonexistent.json"},
            "manifest": {},
        })
        assert status == 422  # all failed, none registered
        assert "main" in body["failed"]

    def test_register_mission_no_workflows_returns_empty(self, tmp_path):
        svc = self._make_stitch()
        status, body = svc.register_mission({
            "mission_id": "bare_mission",
            "install_path": str(tmp_path),
            "workflows": {},
            "manifest": {},
        })
        assert status == 200
        assert body["registered_workflow_ids"] == []
        assert body["mission_id"] == "bare_mission"

    def test_register_mission_missing_mission_id_returns_400(self):
        svc = self._make_stitch()
        status, body = svc.register_mission({})
        assert status == 400

    def test_workflows_are_retrievable_after_registration(self, tmp_path):
        svc = self._make_stitch()
        wf_file = tmp_path / "pipeline.json"
        wf_file.write_text(json.dumps({
            "name": "Pipeline",
            "steps": [
                {"id": "s1", "operator": "chief", "action": "summarize"}
            ]
        }))
        svc.register_mission({
            "mission_id": "sales_pkg",
            "install_path": str(tmp_path),
            "workflows": {"pipeline": "pipeline.json"},
            "manifest": {},
        })
        wf = svc._workflows.get("sales_pkg.pipeline")
        assert wf is not None
        assert wf.name == "Pipeline"
        assert len(wf.steps) == 1
        assert wf.steps[0].operator == "chief"


if __name__ == "__main__":
    import unittest
    unittest.main()
