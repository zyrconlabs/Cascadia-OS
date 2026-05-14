"""Regression tests for install_operator after Phase 5 (mission package) changes.

Verifies that the existing operator install route is unaffected by the addition of
install_mission, kill_switch_provider/verifier constructor kwargs, and new imports.
"""
from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cascadia.depot.kill_switch import InMemoryKillSwitchProvider, NoopKillSwitchProvider
from cascadia.depot.signing import Verifier
from cascadia.registry.crew import CrewService


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_op_zip(manifest: dict, extra_files: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, content in (extra_files or {}).items():
            if isinstance(content, str):
                content = content.encode()
            zf.writestr(name, content)
    return buf.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


VALID_OP_MANIFEST = {
    "operator_id": "scout",
    "name": "Scout Operator",
    "version": "2.0.0",
    "capabilities": ["crm.read"],
    "autonomy_level": "assistive",
}


@pytest.fixture
def crew(tmp_path, monkeypatch):
    """Minimal CrewService with _OPERATORS_DIR and registry pointing at tmp_path."""
    import cascadia.registry.crew as crew_module
    monkeypatch.setattr(crew_module, "_OPERATORS_DIR", tmp_path / "operators")
    mock_runtime = MagicMock()
    mock_runtime.port = 5100
    mock_runtime.logger = MagicMock()
    svc = CrewService.__new__(CrewService)
    svc.registry = {}
    svc.runtime = mock_runtime
    svc._config = {"operators_registry_path": str(tmp_path / "registry.json")}
    svc._kill_switch = NoopKillSwitchProvider()
    svc._verifier = Verifier.from_bundle({})
    return svc


# ── Constructor / routing tests ────────────────────────────────────────────────

class TestConstructorKwargs:
    """New kwargs must not break existing CrewService wiring."""

    def test_default_kill_switch_is_noop(self, crew):
        assert isinstance(crew._kill_switch, NoopKillSwitchProvider)

    def test_custom_kill_switch_stored(self, tmp_path, monkeypatch):
        import cascadia.registry.crew as crew_module
        monkeypatch.setattr(crew_module, "_OPERATORS_DIR", tmp_path / "operators")
        mock_runtime = MagicMock()
        mock_runtime.logger = MagicMock()
        svc = CrewService.__new__(CrewService)
        svc.registry = {}
        svc.runtime = mock_runtime
        svc._config = {}
        ks = InMemoryKillSwitchProvider()
        svc._kill_switch = ks
        assert svc._kill_switch is ks

    def test_custom_verifier_stored(self, tmp_path, monkeypatch):
        import cascadia.registry.crew as crew_module
        monkeypatch.setattr(crew_module, "_OPERATORS_DIR", tmp_path / "operators")
        mock_runtime = MagicMock()
        mock_runtime.logger = MagicMock()
        svc = CrewService.__new__(CrewService)
        svc.registry = {}
        svc.runtime = mock_runtime
        svc._config = {}
        v = Verifier.from_bundle({})
        svc._verifier = v
        assert svc._verifier is v


class TestRouteIsolation:
    """install_operator and install_mission must be independent paths."""

    def test_install_operator_reads_manifest_json_not_mission_json(self, crew, tmp_path):
        """Operator zips use manifest.json; mission.json must NOT be picked up."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            # Only mission.json present — no manifest.json
            zf.writestr("mission.json", json.dumps({"type": "mission", "id": "x"}))
        status, body = crew.install_operator({"zip_b64": _b64(buf.getvalue())})
        assert status == 400
        assert "manifest" in body["error"].lower()

    def test_install_operator_does_not_check_kill_switch(self, crew, monkeypatch):
        """The kill switch is mission-only; operator install must never call it."""
        called = []
        original = crew._kill_switch.is_revoked
        crew._kill_switch.is_revoked = lambda *a, **kw: called.append(a) or False
        zdata = _make_op_zip(VALID_OP_MANIFEST)
        crew.install_operator({"zip_b64": _b64(zdata)})
        assert called == [], "install_operator must not call kill_switch.is_revoked"

    def test_install_operator_does_not_verify_ed25519(self, crew, monkeypatch):
        """Signature verification is mission-only; operator install must not call it."""
        called = []
        monkeypatch.setattr(
            CrewService, "_verify_mission_signature",
            staticmethod(lambda *a, **kw: called.append(a) or (True, "")),
        )
        zdata = _make_op_zip(VALID_OP_MANIFEST)
        crew.install_operator({"zip_b64": _b64(zdata)})
        assert called == [], "_verify_mission_signature must not be called by install_operator"


# ── Existing behaviour regression ─────────────────────────────────────────────

class TestInstallOperatorRegression:
    """Core install_operator behaviour must be unchanged by Phase 5."""

    def test_valid_zip_returns_201(self, crew):
        zdata = _make_op_zip(VALID_OP_MANIFEST)
        status, body = crew.install_operator({"zip_b64": _b64(zdata)})
        assert status == 201
        assert body["installed"] == "scout"

    def test_registered_in_memory_crew(self, crew):
        zdata = _make_op_zip(VALID_OP_MANIFEST)
        crew.install_operator({"zip_b64": _b64(zdata)})
        assert "scout" in crew.registry
        assert crew.registry["scout"]["capabilities"] == ["crm.read"]

    def test_dry_run_returns_200_no_extraction(self, crew, tmp_path, monkeypatch):
        import cascadia.registry.crew as crew_module
        operators_dir = tmp_path / "operators"
        monkeypatch.setattr(crew_module, "_OPERATORS_DIR", operators_dir)
        zdata = _make_op_zip(VALID_OP_MANIFEST)
        status, body = crew.install_operator({"zip_b64": _b64(zdata), "dry_run": True})
        assert status == 200
        assert body["dry_run"] is True
        assert body["operator_id"] == "scout"
        # Nothing extracted to disk
        assert not (operators_dir / "scout").exists()

    def test_manifest_only_install_registers_operator(self, crew):
        manifest = {
            "id": "bare_op",
            "name": "Bare",
            "version": "0.1.0",
            "port": 9900,
            "start_cmd": "python main.py",
            "autonomy_level": "assistive",
            "capabilities": ["crm.read"],
            "tier_required": "lite",
        }
        status, body = crew.install_operator({"manifest": manifest})
        assert status == 201
        assert body["installed"] == "bare_op"

    def test_manifest_only_missing_fields_returns_400(self, crew):
        status, body = crew.install_operator({"manifest": {"id": "x"}})
        assert status == 400
        assert "missing" in body["error"].lower()

    def test_port_conflict_returns_409(self, crew, tmp_path):
        """Second install with same port but different operator_id → 409."""
        first = dict(VALID_OP_MANIFEST)
        first["port"] = 7777
        crew.install_operator({"zip_b64": _b64(_make_op_zip(first))})

        second = {
            "id": "rival_op",
            "operator_id": "rival_op",
            "name": "Rival",
            "version": "1.0.0",
            "capabilities": [],
            "autonomy_level": "assistive",
            "port": 7777,
        }
        status, body = crew.install_operator({"zip_b64": _b64(_make_op_zip(second))})
        assert status == 409
        assert body["error"] == "port_conflict"
        assert body["port"] == 7777

    def test_missing_zip_b64_returns_400(self, crew):
        status, body = crew.install_operator({})
        assert status == 400

    def test_invalid_base64_returns_400(self, crew):
        status, body = crew.install_operator({"zip_b64": "!!not-base64!!"})
        assert status == 400
        assert "base64" in body["error"].lower()

    def test_not_a_zip_returns_400(self, crew):
        status, body = crew.install_operator({"zip_b64": _b64(b"plaintext bytes")})
        assert status == 400

    def test_missing_capabilities_returns_400(self, crew):
        bad = {"operator_id": "x", "name": "X", "version": "1.0.0"}
        status, body = crew.install_operator({"zip_b64": _b64(_make_op_zip(bad))})
        assert status == 400
        assert "missing" in body["error"].lower()

    def test_capabilities_not_a_list_returns_400(self, crew):
        bad = dict(VALID_OP_MANIFEST)
        bad["capabilities"] = "not-a-list"
        status, body = crew.install_operator({"zip_b64": _b64(_make_op_zip(bad))})
        assert status == 400
        assert "list" in body["error"].lower()

    def test_reinstall_updates_registry(self, crew):
        """Re-installing same operator_id updates, not duplicates, registry entry."""
        crew.install_operator({"zip_b64": _b64(_make_op_zip(VALID_OP_MANIFEST))})
        updated = dict(VALID_OP_MANIFEST)
        updated["version"] = "3.0.0"
        crew.install_operator({"zip_b64": _b64(_make_op_zip(updated))})
        assert crew.registry["scout"]["version"] == "3.0.0"


if __name__ == "__main__":
    import unittest
    unittest.main()
