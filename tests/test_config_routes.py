"""Tests for /api/config/* routes in PRISM (Phase 6)."""
from __future__ import annotations

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_prism(tmp_path, flag: bool):
    """Build a minimal PrismService-like object with the guided config methods wired up."""
    from cascadia.dashboard.prism import PrismService
    cfg_path = tmp_path / "config.json"
    db_path  = str(tmp_path / "cascadia.db")
    cfg = {
        "log_dir": str(tmp_path / "logs"),
        "database_path": db_path,
        "operators_registry_path": "",
        "operators_dir": "",
        "llm": {"provider": None, "model": None, "base_url": "", "configured": False,
                 "active_model_id": "", "models_dir": "", "llama_bin": ""},
        "flint": {"heartbeat_file": str(tmp_path / "f.heartbeat"),
                  "heartbeat_interval_seconds": 5, "heartbeat_stale_after_seconds": 15,
                  "status_port": 14011, "health_interval_seconds": 5,
                  "drain_timeout_seconds": 10, "max_restart_attempts": 5,
                  "restart_backoff_seconds": [5, 30, 120]},
        "curtain": {"signing_secret": "x"},
        "components": [
            {"name": "prism", "module": "cascadia.dashboard.prism",
             "port": 16300, "tier": 3,
             "heartbeat_file": str(tmp_path / "prism.heartbeat"),
             "depends_on": []}
        ],
        "models": [],
        "guided_configuration_enabled": flag,
    }
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    svc = PrismService.__new__(PrismService)
    svc.config = cfg
    svc.config["__config_path__"] = str(cfg_path)
    return svc


# ── Feature flag OFF → 404 on all routes ─────────────────────────────────────

class TestFeatureFlagOff:

    @pytest.fixture(autouse=True)
    def svc(self, tmp_path):
        self.svc = _make_prism(tmp_path, flag=False)
        self.tmp = tmp_path

    def test_cfg_get_returns_404(self):
        code, _ = self.svc.cfg_get({"target_type": "operator", "target_id": "x"})
        assert code == 404

    def test_cfg_schema_returns_404(self):
        code, _ = self.svc.cfg_schema({"target_type": "operator", "target_id": "x"})
        assert code == 404

    def test_cfg_preview_returns_404(self):
        code, _ = self.svc.cfg_preview({"target_type": "operator", "target_id": "x", "changes": {}})
        assert code == 404

    def test_cfg_save_returns_404(self):
        code, _ = self.svc.cfg_save({"target_type": "operator", "target_id": "x", "changes": {}, "confirmed": True})
        assert code == 404

    def test_cfg_reset_returns_404(self):
        code, _ = self.svc.cfg_reset({"target_type": "operator", "target_id": "x", "confirmed": False})
        assert code == 404

    def test_cfg_test_returns_404(self):
        code, _ = self.svc.cfg_test({"target_type": "operator", "target_id": "x"})
        assert code == 404

    def test_cfg_resources_returns_404(self):
        code, _ = self.svc.cfg_resources({})
        assert code == 404


# ── Feature flag ON ───────────────────────────────────────────────────────────

class TestFeatureFlagOn:

    @pytest.fixture(autouse=True)
    def svc(self, tmp_path):
        self.svc = _make_prism(tmp_path, flag=True)
        self.tmp = tmp_path

    def _patch_engine(self, engine):
        """Patch _cfg_engine to return the given engine."""
        self.svc._cfg_engine = lambda: engine

    def test_cfg_get_returns_200_no_manifest(self):
        from cascadia.settings.engine import SettingsEngine
        eng = SettingsEngine(
            settings_db=str(self.tmp / "s.db"),
            vault_db=str(self.tmp / "v.db"),
        )
        self._patch_engine(eng)
        self.svc._load_setup_manifest = lambda *_: None
        code, body = self.svc.cfg_get({"target_type": "operator", "target_id": "x"})
        assert code == 200
        assert "settings" in body

    def test_cfg_preview_returns_200(self):
        from cascadia.settings.engine import SettingsEngine
        eng = SettingsEngine(
            settings_db=str(self.tmp / "s.db"),
            vault_db=str(self.tmp / "v.db"),
        )
        self._patch_engine(eng)
        self.svc._load_setup_manifest = lambda *_: None
        code, body = self.svc.cfg_preview({
            "target_type": "operator", "target_id": "x",
            "changes": {"business_name": "Acme"}
        })
        assert code == 200

    def test_cfg_save_confirmed_false_returns_preview(self):
        from cascadia.settings.engine import SettingsEngine
        eng = SettingsEngine(
            settings_db=str(self.tmp / "s.db"),
            vault_db=str(self.tmp / "v.db"),
        )
        self._patch_engine(eng)
        self.svc._load_setup_manifest = lambda *_: None
        code, body = self.svc.cfg_save({
            "target_type": "operator", "target_id": "x",
            "changes": {"x": "y"}, "confirmed": False, "source": "test",
        })
        assert code == 200
        assert body.get("saved") is False

    def test_cfg_save_confirmed_true_persists(self, tmp_path):
        from cascadia.settings.engine import SettingsEngine
        from cascadia.shared.manifest_schema import validate_manifest
        eng = SettingsEngine(
            settings_db=str(self.tmp / "s.db"),
            vault_db=str(self.tmp / "v.db"),
        )
        m = validate_manifest({
            "id": "test_op", "name": "T", "version": "1.0.0", "type": "skill",
            "capabilities": [], "required_dependencies": [], "requested_permissions": [],
            "autonomy_level": "manual_only", "health_hook": "/h", "description": "t",
            "setup_fields": [{"name": "biz", "label": "Biz", "type": "string", "default": None}],
        })
        self._patch_engine(eng)
        self.svc._load_setup_manifest = lambda *_: m
        code, body = self.svc.cfg_save({
            "target_type": "operator", "target_id": "test_op",
            "changes": {"biz": "Saved Co."}, "confirmed": True, "source": "test",
        })
        assert code == 200
        assert body.get("saved") is True
        assert eng._store.get_setting("operator", "test_op", "biz") == "Saved Co."

    def test_cfg_schema_returns_404_when_no_manifest(self):
        self.svc._load_setup_manifest = lambda *_: None
        code, _ = self.svc.cfg_schema({"target_type": "operator", "target_id": "missing"})
        assert code == 404

    def test_cfg_schema_returns_setup_fields(self):
        from cascadia.shared.manifest_schema import validate_manifest
        m = validate_manifest({
            "id": "test_op", "name": "T", "version": "1.0.0", "type": "skill",
            "capabilities": [], "required_dependencies": [], "requested_permissions": [],
            "autonomy_level": "manual_only", "health_hook": "/h", "description": "t",
            "setup_fields": [{"name": "biz", "label": "Biz", "type": "string"}],
        })
        self.svc._load_setup_manifest = lambda *_: m
        code, body = self.svc.cfg_schema({"target_type": "operator", "target_id": "test_op"})
        assert code == 200
        assert len(body["setup_fields"]) == 1
        assert body["setup_fields"][0]["name"] == "biz"

    def test_cfg_test_returns_success_result(self):
        from cascadia.settings.engine import SettingsEngine
        eng = SettingsEngine(
            settings_db=str(self.tmp / "s.db"),
            vault_db=str(self.tmp / "v.db"),
        )
        self._patch_engine(eng)
        self.svc._load_setup_manifest = lambda *_: None
        code, body = self.svc.cfg_test({"target_type": "operator", "target_id": "x"})
        assert code == 200
        assert "success" in body

    def test_cfg_resources_returns_200(self):
        with patch("cascadia.settings.resource_resolver.get_installed_connectors",
                   return_value=[{"id": "gmail", "type": "connector"}]):
            code, body = self.svc.cfg_resources({})
        assert code == 200
        assert "resources" in body

    def test_secret_field_not_exposed_in_cfg_get(self):
        from cascadia.settings.engine import SettingsEngine
        from cascadia.shared.manifest_schema import validate_manifest
        m = validate_manifest({
            "id": "test_op", "name": "T", "version": "1.0.0", "type": "skill",
            "capabilities": [], "required_dependencies": [], "requested_permissions": [],
            "autonomy_level": "manual_only", "health_hook": "/h", "description": "t",
            "setup_fields": [
                {"name": "api_key", "label": "API Key", "type": "secret",
                 "secret": True, "vault_key": "test_op:api_key"},
            ],
        })
        eng = SettingsEngine(
            settings_db=str(self.tmp / "s.db"),
            vault_db=str(self.tmp / "v.db"),
        )
        eng._vault.write("test_op:api_key", "super_secret", created_by="test", namespace="secrets")
        self._patch_engine(eng)
        self.svc._load_setup_manifest = lambda *_: m
        code, body = self.svc.cfg_get({"target_type": "operator", "target_id": "test_op"})
        assert code == 200
        assert "super_secret" not in json.dumps(body)
        assert body["settings"]["api_key"]["configured"] is True
