"""Tests for cascadia.settings.store (Phase 2)."""
from __future__ import annotations

import pytest
import tempfile
import os

from cascadia.settings.store import SettingsStore, SecretFieldError
from cascadia.shared.manifest_schema import validate_manifest, SetupField


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return SettingsStore(str(tmp_path / "settings.db"))


BASE_MANIFEST = {
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
         "type": "boolean", "default": True},
        {"name": "api_key", "label": "API Key", "type": "secret",
         "secret": True, "vault_key": "test_op:api_key"},
    ],
}


@pytest.fixture
def manifest():
    return validate_manifest(BASE_MANIFEST)


# ── Basic CRUD ────────────────────────────────────────────────────────────────

def test_get_returns_none_when_not_saved(store):
    result = store.get_setting("operator", "test_op", "business_name")
    assert result is None


def test_set_stores_value(store):
    store.set_setting("operator", "test_op", "business_name", "Acme Co.", "wizard")
    result = store.get_setting("operator", "test_op", "business_name")
    assert result == "Acme Co."


def test_set_overwrites_existing_value(store):
    store.set_setting("operator", "test_op", "business_name", "First", "wizard")
    store.set_setting("operator", "test_op", "business_name", "Second", "wizard")
    assert store.get_setting("operator", "test_op", "business_name") == "Second"


def test_get_all_settings_returns_dict(store):
    store.set_setting("operator", "test_op", "business_name", "Acme", "wizard")
    store.set_setting("operator", "test_op", "lead_source", "gmail", "wizard")
    result = store.get_all_settings("operator", "test_op")
    assert result == {"business_name": "Acme", "lead_source": "gmail"}


def test_get_all_returns_empty_when_nothing_saved(store):
    result = store.get_all_settings("operator", "test_op")
    assert result == {}


# ── set_many_settings ─────────────────────────────────────────────────────────

def test_set_many_stores_all_values(store):
    changes = {"business_name": "Acme", "lead_source": "webhook"}
    store.set_many_settings("operator", "test_op", changes, "wizard")
    assert store.get_setting("operator", "test_op", "business_name") == "Acme"
    assert store.get_setting("operator", "test_op", "lead_source") == "webhook"


def test_set_many_is_atomic(store):
    store.set_setting("operator", "test_op", "business_name", "Before", "wizard")
    changes = {"business_name": "After", "lead_source": "gmail"}
    store.set_many_settings("operator", "test_op", changes, "wizard")
    assert store.get_setting("operator", "test_op", "business_name") == "After"
    assert store.get_setting("operator", "test_op", "lead_source") == "gmail"


# ── Secret field protection ───────────────────────────────────────────────────

def test_secret_field_raises_error(store, manifest):
    with pytest.raises(SecretFieldError, match="secret=True"):
        store.set_setting("operator", "test_op", "api_key", "secret_val", "wizard", manifest)


def test_secret_field_blocked_in_set_many(store, manifest):
    with pytest.raises(SecretFieldError, match="secret=True"):
        store.set_many_settings("operator", "test_op", {"api_key": "val"}, "wizard", manifest)


def test_non_secret_field_allowed_with_manifest(store, manifest):
    store.set_setting("operator", "test_op", "business_name", "Acme", "wizard", manifest)
    assert store.get_setting("operator", "test_op", "business_name") == "Acme"


# ── get_defaults ──────────────────────────────────────────────────────────────

def test_get_defaults_returns_non_secret_defaults(store, manifest):
    defaults = store.get_defaults("operator", "test_op", manifest)
    assert defaults["business_name"] == "Demo Business"
    assert defaults["lead_source"] == "gmail"
    assert defaults["ask_before_sending"] is True
    assert "api_key" not in defaults  # secret field excluded


def test_secret_field_excluded_from_defaults(store, manifest):
    defaults = store.get_defaults("operator", "test_op", manifest)
    assert "api_key" not in defaults


# ── reset_to_defaults ─────────────────────────────────────────────────────────

def test_reset_restores_defaults(store, manifest):
    store.set_setting("operator", "test_op", "business_name", "Changed", "wizard")
    store.reset_to_defaults("operator", "test_op", manifest, "reset")
    assert store.get_setting("operator", "test_op", "business_name") == "Demo Business"
    assert store.get_setting("operator", "test_op", "lead_source") == "gmail"


# ── Revision tracking ─────────────────────────────────────────────────────────

def test_revision_created_on_save(store):
    store.set_setting("operator", "test_op", "business_name", "Acme", "wizard")
    revs = store.get_revisions("operator", "test_op")
    assert len(revs) == 1
    assert revs[0]["field_name"] == "business_name"
    assert revs[0]["new_value"] == "Acme"


def test_revision_has_correct_source(store):
    store.set_setting("operator", "test_op", "business_name", "X", "configuration_button")
    revs = store.get_revisions("operator", "test_op")
    assert revs[0]["source"] == "configuration_button"


def test_revision_records_old_value(store):
    store.set_setting("operator", "test_op", "business_name", "First", "wizard")
    store.set_setting("operator", "test_op", "business_name", "Second", "wizard")
    revs = store.get_revisions("operator", "test_op", limit=10)
    # Most recent first
    assert revs[0]["new_value"] == "Second"
    assert revs[0]["old_value"] == "First"


def test_set_many_creates_revision_per_field(store):
    store.set_many_settings("operator", "test_op",
                            {"business_name": "A", "lead_source": "webhook"},
                            "wizard")
    revs = store.get_revisions("operator", "test_op")
    assert len(revs) == 2
    field_names = {r["field_name"] for r in revs}
    assert field_names == {"business_name", "lead_source"}


# ── Value types preserved ─────────────────────────────────────────────────────

def test_boolean_value_preserved(store):
    store.set_setting("operator", "test_op", "ask_before_sending", False, "wizard")
    assert store.get_setting("operator", "test_op", "ask_before_sending") is False


def test_int_value_preserved(store):
    store.set_setting("operator", "test_op", "retry_limit", 7, "wizard")
    assert store.get_setting("operator", "test_op", "retry_limit") == 7


def test_none_value_stored_and_retrieved(store):
    store.set_setting("operator", "test_op", "business_name", None, "wizard")
    assert store.get_setting("operator", "test_op", "business_name") is None
