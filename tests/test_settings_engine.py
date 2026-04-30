"""Tests for cascadia.settings.engine (Phase 3)."""
from __future__ import annotations

import pytest
from cascadia.settings.engine import SettingsEngine
from cascadia.shared.manifest_schema import validate_manifest

BASE_MANIFEST_DATA = {
    "id": "test_op",
    "name": "Test Op",
    "version": "1.0.0",
    "type": "skill",
    "capabilities": [],
    "required_dependencies": [],
    "requested_permissions": [],
    "autonomy_level": "manual_only",
    "health_hook": "/health",
    "description": "Test.",
    "setup_fields": [
        {"name": "business_name", "label": "Business Name", "type": "string",
         "required": True, "default": "Demo Business"},
        {"name": "lead_source", "label": "Lead Source", "type": "select",
         "options": ["gmail", "webhook"], "default": "gmail"},
        {"name": "ask_before_sending", "label": "Ask Before Sending",
         "type": "boolean", "default": True,
         "requires_approval_if_enabled": ["email.send"]},
        {"name": "api_key", "label": "API Key", "type": "secret",
         "secret": True, "vault_key": "test_op:api_key"},
    ],
}


@pytest.fixture
def engine(tmp_path):
    return SettingsEngine(
        settings_db=str(tmp_path / "settings.db"),
        vault_db=str(tmp_path / "vault.db"),
    )


@pytest.fixture
def manifest():
    return validate_manifest(BASE_MANIFEST_DATA)


# ── get_settings ──────────────────────────────────────────────────────────────

def test_get_returns_defaults_when_nothing_saved(engine, manifest):
    result = engine.get_settings("operator", "test_op", manifest)
    assert result["business_name"] == "Demo Business"
    assert result["lead_source"] == "gmail"
    assert result["ask_before_sending"] is True


def test_get_merges_saved_over_defaults(engine, manifest):
    engine._store.set_setting("operator", "test_op", "business_name", "Acme", "wizard")
    result = engine.get_settings("operator", "test_op", manifest)
    assert result["business_name"] == "Acme"
    assert result["lead_source"] == "gmail"  # still default


def test_secret_field_returns_masked_not_configured(engine, manifest):
    result = engine.get_settings("operator", "test_op", manifest)
    assert result["api_key"]["configured"] is False
    assert result["api_key"]["masked"] is None


def test_secret_field_returns_masked_configured(engine, manifest):
    engine._vault.write("test_op:api_key", "secret_value", created_by="test", namespace="secrets")
    result = engine.get_settings("operator", "test_op", manifest)
    assert result["api_key"]["configured"] is True
    assert result["api_key"]["masked"] == "••••••••"


def test_secret_field_never_returns_raw_value(engine, manifest):
    engine._vault.write("test_op:api_key", "super_secret", created_by="test", namespace="secrets")
    result = engine.get_settings("operator", "test_op", manifest)
    assert "super_secret" not in str(result)


def test_get_without_manifest_returns_raw_saved(engine):
    engine._store.set_setting("operator", "test_op", "business_name", "X", "wizard")
    result = engine.get_settings("operator", "test_op")
    assert result == {"business_name": "X"}


# ── preview_patch ─────────────────────────────────────────────────────────────

def test_preview_shows_before_after(engine, manifest):
    engine._store.set_setting("operator", "test_op", "business_name", "Old", "wizard")
    preview = engine.preview_patch("operator", "test_op",
                                   {"business_name": "New"}, manifest)
    changes = preview["settings_changes"]
    assert len(changes) == 1
    assert changes[0]["field"] == "business_name"
    assert changes[0]["before"] == "Old"
    assert changes[0]["after"] == "New"


def test_preview_does_not_save(engine, manifest):
    engine.preview_patch("operator", "test_op", {"business_name": "X"}, manifest)
    assert engine._store.get_setting("operator", "test_op", "business_name") is None


def test_preview_marks_approval_required(engine, manifest):
    preview = engine.preview_patch("operator", "test_op",
                                   {"ask_before_sending": True}, manifest)
    assert "ask_before_sending" in preview["approval_required"]


# ── save_patch ────────────────────────────────────────────────────────────────

def test_save_with_confirmed_false_returns_preview_only(engine, manifest):
    result = engine.save_patch("operator", "test_op",
                               {"business_name": "X"}, False, "wizard", manifest)
    assert result["saved"] is False
    assert engine._store.get_setting("operator", "test_op", "business_name") is None


def test_save_with_confirmed_true_persists(engine, manifest):
    result = engine.save_patch("operator", "test_op",
                               {"business_name": "Saved"}, True, "wizard", manifest)
    assert result["saved"] is True
    assert engine._store.get_setting("operator", "test_op", "business_name") == "Saved"


def test_secret_value_goes_to_vault_not_store(engine, manifest):
    engine.save_patch("operator", "test_op",
                      {"api_key": "my_secret"}, True, "wizard", manifest)
    # Not in settings store
    assert engine._store.get_setting("operator", "test_op", "api_key") is None
    # Is in vault
    val = engine._vault.read("test_op:api_key", namespace="secrets")
    assert val == "my_secret"


def test_non_secret_value_goes_to_store_not_vault(engine, manifest):
    engine.save_patch("operator", "test_op",
                      {"business_name": "Store Me"}, True, "wizard", manifest)
    assert engine._store.get_setting("operator", "test_op", "business_name") == "Store Me"
    # Not in vault
    assert engine._vault.read("business_name", namespace="secrets") is None


# ── reset_settings ────────────────────────────────────────────────────────────

def test_reset_confirmed_false_returns_preview(engine, manifest):
    result = engine.reset_settings("operator", "test_op", manifest, confirmed=False)
    assert result["reset"] is False


def test_reset_confirmed_true_applies_defaults(engine, manifest):
    engine._store.set_setting("operator", "test_op", "business_name", "Changed", "wizard")
    engine.reset_settings("operator", "test_op", manifest, confirmed=True)
    assert engine._store.get_setting("operator", "test_op", "business_name") == "Demo Business"


# ── test_settings ─────────────────────────────────────────────────────────────

def test_settings_health_passes_with_required_field_set(engine, manifest):
    engine._store.set_setting("operator", "test_op", "business_name", "Acme", "wizard")
    result = engine.test_settings("operator", "test_op", manifest)
    assert result["success"] is True


def test_settings_health_fails_with_missing_required(tmp_path):
    """A required field with no default and no saved value should fail the health check."""
    data = {**BASE_MANIFEST_DATA, "setup_fields": [
        {"name": "webhook_url", "label": "Webhook URL", "type": "string",
         "required": True, "default": None},
    ]}
    m = validate_manifest(data)
    eng = SettingsEngine(
        settings_db=str(tmp_path / "s.db"),
        vault_db=str(tmp_path / "v.db"),
    )
    result = eng.test_settings("operator", "test_op", m)
    assert result["success"] is False
    assert "webhook_url" in result["details"]["missing_required"]


# ── validate_patch ────────────────────────────────────────────────────────────

def test_validate_rejects_unknown_field(engine, manifest):
    result = engine.validate_patch({"nonexistent_field": "val"}, manifest)
    assert result["valid"] is False
    assert any("nonexistent_field" in e for e in result["errors"])


def test_validate_accepts_known_field(engine, manifest):
    result = engine.validate_patch({"business_name": "Valid"}, manifest)
    assert result["valid"] is True


def test_validate_rejects_out_of_range_number():
    data = {**BASE_MANIFEST_DATA, "setup_fields": [
        {"name": "retry", "label": "Retry", "type": "number", "min": 1, "max": 5}
    ]}
    m = validate_manifest(data)
    engine = SettingsEngine.__new__(SettingsEngine)
    result = engine.validate_patch({"retry": 10}, m)
    assert result["valid"] is False
