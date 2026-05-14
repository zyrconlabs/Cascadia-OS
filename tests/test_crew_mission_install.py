"""Tests for CrewService.install_mission and uninstall_mission (Phase 6).

Uses in-memory ephemeral keypairs, temp directories, and monkeypatching so
no real STITCH/LICENSE_GATE services are required.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cascadia.depot.canonicalization import canonical_file_bytes, compute_package_digest
from cascadia.depot.kill_switch import InMemoryKillSwitchProvider, NoopKillSwitchProvider
from cascadia.depot.signing import Verifier, sign_manifest
from cascadia.registry.crew import CrewService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ephemeral_signer_and_verifier(key_id: str = "test-2026-q1"):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
    import tempfile
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    priv_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".key")
    tmp.write(priv_bytes)
    tmp.close()
    from cascadia.depot.signing import LocalSigner
    signer = LocalSigner(key_path=tmp.name, _key_id=key_id)
    verifier = Verifier.from_bundle({key_id: pub_b64})
    return signer, verifier


def _build_signed_zip(signer, mission_id: str = "test_mission",
                      workflows: dict | None = None,
                      extra_file_bytes: dict | None = None) -> bytes:
    """Build a properly signed mission zip."""
    payload_files: dict[str, bytes] = {}
    wf_map: dict[str, str] = {}

    if workflows:
        for wf_id, wf_content in workflows.items():
            rel = f"workflows/{wf_id}.json"
            payload_files[rel] = wf_content if isinstance(wf_content, bytes) else wf_content.encode()
            wf_map[wf_id] = rel

    for path, content in (extra_file_bytes or {}).items():
        payload_files[path] = content if isinstance(content, bytes) else content.encode()

    canonical_map = {p: canonical_file_bytes(p, b) for p, b in payload_files.items()}
    pkg_digest = compute_package_digest(canonical_map)
    files_list = [
        {
            "path": p,
            "sha256": hashlib.sha256(canonical_map[p]).hexdigest(),
            "size_bytes": len(canonical_map[p]),
        }
        for p in sorted(canonical_map.keys())
    ]
    manifest_unsigned = {
        "type": "mission",
        "id": mission_id,
        "version": "1.0.0",
        "name": "Test Mission",
        "description": "Integration test mission.",
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
        "workflows": wf_map,
        "events": {"produces": [], "consumes": []},
        "billing": {"included_in": ["lite"], "available_as_addon": False, "addon_price_monthly": None},
        "limits": {"lite": {"enabled": True, "mode": "lite"}},
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


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def crew_and_signer(tmp_path, monkeypatch):
    """Returns (crew_svc, signer) with all I/O pointing at tmp_path."""
    import cascadia.registry.crew as crew_module
    monkeypatch.setattr(crew_module, "_OPERATORS_DIR", tmp_path / "operators")

    signer, verifier = _make_ephemeral_signer_and_verifier()
    mock_runtime = MagicMock()
    mock_runtime.port = 5100
    mock_runtime.logger = MagicMock()

    svc = CrewService.__new__(CrewService)
    svc.registry = {}
    svc.runtime = mock_runtime
    svc._config = {
        "operators_registry_path": str(tmp_path / "registry.json"),
        "database_path": str(tmp_path / "cascadia.db"),
        "missions": {"packages_root": str(tmp_path / "missions")},
    }
    svc._kill_switch = NoopKillSwitchProvider()
    svc._verifier = verifier
    return svc, signer


# ── install_mission tests ──────────────────────────────────────────────────────

class TestInstallMission:

    def _install(self, svc, zip_bytes: bytes, **extra) -> tuple[int, dict]:
        payload = {"_zip_bytes": zip_bytes, **extra}
        return svc.install_mission(payload)

    def test_valid_package_returns_201(self, crew_and_signer, tmp_path, monkeypatch):
        svc, signer = crew_and_signer
        # Patch LICENSE_GATE to succeed
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_bytes = _build_signed_zip(signer)
        status, body = self._install(svc, zip_bytes)
        assert status == 201, body
        assert body["installed"] == "test_mission"
        assert body["version"] == "1.0.0"

    def test_valid_package_extracted_to_disk(self, crew_and_signer, tmp_path, monkeypatch):
        svc, signer = crew_and_signer
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_bytes = _build_signed_zip(signer, workflows={"main": b'{"steps":[]}'})
        self._install(svc, zip_bytes)
        install_dir = tmp_path / "missions" / "test_mission"
        assert install_dir.exists()
        assert (install_dir / "mission.json").exists()
        assert (install_dir / "workflows" / "main.json").exists()

    def test_not_a_zip_returns_400(self, crew_and_signer):
        svc, _ = crew_and_signer
        status, body = svc.install_mission({"_zip_bytes": b"not a zip"})
        assert status == 400
        assert "bad_package" in body["error"]

    def test_missing_zip_b64_returns_400(self, crew_and_signer):
        svc, _ = crew_and_signer
        status, body = svc.install_mission({})
        assert status == 400
        assert "bad_request" in body["error"]

    def test_invalid_base64_returns_400(self, crew_and_signer):
        svc, _ = crew_and_signer
        status, body = svc.install_mission({"zip_b64": "!!!invalid!!!"})
        assert status == 400
        assert "invalid_base64" in body["error"]

    def test_wrong_type_field_returns_400(self, crew_and_signer, tmp_path):
        svc, signer = crew_and_signer
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mission.json", json.dumps({"type": "operator", "id": "x"}))
        status, body = svc.install_mission({"_zip_bytes": buf.getvalue()})
        assert status == 400
        assert "bad_package" in body["error"]

    def test_invalid_signature_returns_400(self, crew_and_signer, tmp_path, monkeypatch):
        svc, signer = crew_and_signer
        _, other_verifier = _make_ephemeral_signer_and_verifier("other-key")
        svc._verifier = other_verifier  # wrong verifier → unknown_key_id
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_bytes = _build_signed_zip(signer)
        status, body = self._install(svc, zip_bytes)
        assert status == 400
        assert body["error"] in ("unknown_key_id", "invalid_signature")

    def test_revoked_package_returns_403(self, crew_and_signer, tmp_path, monkeypatch):
        svc, signer = crew_and_signer
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        ks = InMemoryKillSwitchProvider()
        ks.revoke("test_mission", "1.0.0")
        svc._kill_switch = ks
        zip_bytes = _build_signed_zip(signer)
        status, body = self._install(svc, zip_bytes)
        assert status == 403
        assert body["error"] == "package_revoked"

    def test_license_gate_unreachable_returns_503(self, crew_and_signer, tmp_path, monkeypatch):
        svc, signer = crew_and_signer
        import urllib.error
        def _raise(*a, **kw):
            raise ConnectionRefusedError("no server")
        monkeypatch.setattr("cascadia.registry.crew._urllib_request.urlopen", _raise)
        zip_bytes = _build_signed_zip(signer)
        status, body = self._install(svc, zip_bytes)
        assert status == 503
        assert body["error"] == "license_gate_unavailable"

    def test_no_workflows_stitch_registered_immediately(self, crew_and_signer, tmp_path, monkeypatch):
        """Missions with no workflows mark stitch_registered True without calling STITCH."""
        svc, signer = crew_and_signer
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_bytes = _build_signed_zip(signer, workflows={})
        status, body = self._install(svc, zip_bytes)
        assert status == 201
        assert body["stitch_registered"] is True
        assert body["stitch_pending"] is False

    def test_stitch_unreachable_stitch_pending_true(self, crew_and_signer, tmp_path, monkeypatch):
        """When STITCH is unreachable, stitch_pending: True is returned (best-effort)."""
        svc, signer = crew_and_signer
        call_count = [0]
        def _selective_urlopen(req, timeout=None):
            call_count[0] += 1
            if "license" in req.full_url:
                return _fake_license_ok()
            raise ConnectionRefusedError("stitch unreachable")
        monkeypatch.setattr("cascadia.registry.crew._urllib_request.urlopen", _selective_urlopen)
        zip_bytes = _build_signed_zip(signer, workflows={"main": b'{"steps":[]}'})
        status, body = self._install(svc, zip_bytes)
        assert status == 201
        assert body["stitch_registered"] is False
        assert body["stitch_pending"] is True

    def test_stitch_success_stitch_pending_false(self, crew_and_signer, tmp_path, monkeypatch):
        """When STITCH responds 200, stitch_registered: True and stitch_pending: False."""
        svc, signer = crew_and_signer
        def _urlopen(req, timeout=None):
            if "license" in req.full_url:
                return _fake_license_ok()
            return _fake_stitch_ok()
        monkeypatch.setattr("cascadia.registry.crew._urllib_request.urlopen", _urlopen)
        zip_bytes = _build_signed_zip(signer, workflows={"main": b'{"steps":[]}'})
        status, body = self._install(svc, zip_bytes)
        assert status == 201
        assert body["stitch_registered"] is True
        assert body["stitch_pending"] is False

    def test_mission_written_to_registry(self, crew_and_signer, tmp_path, monkeypatch):
        svc, signer = crew_and_signer
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_bytes = _build_signed_zip(signer)
        self._install(svc, zip_bytes)
        from cascadia.missions.registry import MissionRegistry
        missions_root = str(tmp_path / "missions")
        reg_file = str(tmp_path / "missions" / "missions_registry.json")
        reg = MissionRegistry(packages_root=missions_root, registry_file=reg_file)
        installed = reg.list_installed()
        assert any(m.get("id") == "test_mission" for m in installed)

    def test_missing_required_operator_returns_422(self, crew_and_signer, tmp_path, monkeypatch):
        svc, signer = crew_and_signer
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        # Build a mission that requires "scout" operator (not in registry)
        zip_bytes = _build_signed_zip_with_required_op(signer, required_op="scout")
        status, body = self._install(svc, zip_bytes)
        assert status == 422
        assert body["error"] == "missing_operator"


# ── uninstall_mission tests ───────────────────────────────────────────────────

class TestUninstallMission:

    def _install_then_uninstall(self, svc, signer, monkeypatch, dry_run=True, confirmed=False):
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        zip_bytes = _build_signed_zip(signer)
        svc.install_mission({"_zip_bytes": zip_bytes})
        return svc.uninstall_mission({
            "mission_id": "test_mission",
            "dry_run": dry_run,
            "confirmed": confirmed,
        })

    def test_dry_run_returns_200_files_intact(self, crew_and_signer, tmp_path, monkeypatch):
        svc, signer = crew_and_signer
        status, body = self._install_then_uninstall(svc, signer, monkeypatch, dry_run=True)
        assert status == 200, body
        assert body["dry_run"] is True
        assert body["mission_id"] == "test_mission"
        assert (tmp_path / "missions" / "test_mission").exists()

    def test_confirmed_uninstall_removes_files(self, crew_and_signer, tmp_path, monkeypatch):
        svc, signer = crew_and_signer
        status, body = self._install_then_uninstall(
            svc, signer, monkeypatch, dry_run=False, confirmed=True
        )
        assert status == 200, body
        assert body["uninstalled"] == "test_mission"
        assert not (tmp_path / "missions" / "test_mission").exists()

    def test_confirmed_uninstall_removes_from_registry(self, crew_and_signer, tmp_path, monkeypatch):
        svc, signer = crew_and_signer
        self._install_then_uninstall(svc, signer, monkeypatch, dry_run=False, confirmed=True)
        from cascadia.missions.registry import MissionRegistry
        missions_root = str(tmp_path / "missions")
        reg_file = str(tmp_path / "missions" / "missions_registry.json")
        reg = MissionRegistry(packages_root=missions_root, registry_file=reg_file)
        installed = reg.list_installed()
        assert not any(m.get("id") == "test_mission" for m in installed)

    def test_unconfirmed_non_dryrun_returns_400(self, crew_and_signer, tmp_path, monkeypatch):
        svc, signer = crew_and_signer
        monkeypatch.setattr(
            "cascadia.registry.crew._urllib_request.urlopen",
            lambda req, timeout=None: _fake_license_ok(),
        )
        svc.install_mission({"_zip_bytes": _build_signed_zip(signer)})
        status, body = svc.uninstall_mission({
            "mission_id": "test_mission", "dry_run": False, "confirmed": False
        })
        assert status == 400
        assert body["error"] == "confirmation_required"

    def test_uninstall_not_found_returns_404(self, crew_and_signer):
        svc, _ = crew_and_signer
        status, body = svc.uninstall_mission({
            "mission_id": "nonexistent", "dry_run": False, "confirmed": True
        })
        assert status == 404
        assert body["error"] == "not_found"

    def test_uninstall_missing_mission_id_returns_400(self, crew_and_signer):
        svc, _ = crew_and_signer
        status, body = svc.uninstall_mission({})
        assert status == 400
        assert body["error"] == "bad_request"


# ── Fake HTTP helpers ─────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status = status
        self._data = json.dumps(body).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _fake_license_ok():
    return _FakeResponse(200, {"ok": True})


def _fake_stitch_ok():
    return _FakeResponse(200, {"registered_workflow_ids": [], "failed": []})


# ── Helper for required-operator test ────────────────────────────────────────

def _build_signed_zip_with_required_op(signer, required_op: str) -> bytes:
    """Build a zip whose manifest declares a required operator dependency."""
    manifest_unsigned = {
        "type": "mission",
        "id": "test_mission",
        "version": "1.0.0",
        "name": "Test Mission",
        "description": "Dep test.",
        "tier_required": "lite",
        "runtime": "server",
        "author": "zyrcon-labs",
        "signed_by": signer.key_id(),
        "signature_algorithm": "Ed25519",
        "key_id": signer.key_id(),
        "package_digest": compute_package_digest({}),
        "files": [],
        "capabilities": [],
        "requires_approval": [],
        "risk_level": "low",
        "industries": [],
        "operators": {"required": [required_op], "optional": []},
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
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mission.json", json.dumps(manifest))
    return buf.getvalue()


if __name__ == "__main__":
    import unittest
    unittest.main()
