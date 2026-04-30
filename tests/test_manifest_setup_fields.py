"""Tests for SetupField integration in manifest_schema.py."""
from __future__ import annotations

import pytest
from cascadia.shared.manifest_schema import (
    Manifest, SetupField, validate_manifest, ManifestValidationError,
    VALID_FIELD_TYPES,
)

# ── Shared base manifest ──────────────────────────────────────────────────────

BASE = {
    "id": "test_op",
    "name": "Test Operator",
    "version": "1.0.0",
    "type": "skill",
    "capabilities": [],
    "required_dependencies": [],
    "requested_permissions": [],
    "autonomy_level": "manual_only",
    "health_hook": "/health",
    "description": "Test operator for setup field validation.",
}

FULL_FIELD = {
    "name": "business_name",
    "label": "Business Name",
    "type": "string",
    "required": True,
    "default": "My Business",
    "help_text": "Your public business name",
    "placeholder": "Acme Co.",
    "simple_mode": True,
}

SECRET_FIELD = {
    "name": "api_key",
    "label": "API Key",
    "type": "secret",
    "secret": True,
    "vault_key": "test_op:api_key",
    "required": False,
}

SELECT_FIELD = {
    "name": "lead_source",
    "label": "Lead Source",
    "type": "select",
    "options": ["gmail", "google_sheets", "webhook"],
    "default": "gmail",
    "simple_mode": True,
}

NUMBER_FIELD = {
    "name": "retry_limit",
    "label": "Retry Limit",
    "type": "number",
    "default": 3,
    "min": 1,
    "max": 10,
    "advanced_mode": True,
}

BOOL_FIELD = {
    "name": "ask_before_sending",
    "label": "Ask Before Sending",
    "type": "boolean",
    "default": True,
    "simple_mode": True,
    "requires_approval_if_enabled": ["email.send"],
}

DEV_FIELD = {
    "name": "raw_prompt_override",
    "label": "Raw Prompt Override",
    "type": "string",
    "default": None,
    "developer_mode": True,
    "simple_mode": False,
}


def _manifest(**overrides):
    return {**BASE, **overrides}


# ── Manifest without setup_fields loads fine ─────────────────────────────────

def test_manifest_without_setup_fields_loads():
    m = validate_manifest(BASE)
    assert m.setup_fields == []


def test_manifest_with_empty_setup_fields_loads():
    m = validate_manifest(_manifest(setup_fields=[]))
    assert m.setup_fields == []


# ── Manifest with setup_fields loads all fields ──────────────────────────────

def test_manifest_with_setup_fields_loaded():
    m = validate_manifest(_manifest(setup_fields=[FULL_FIELD, SECRET_FIELD, SELECT_FIELD]))
    assert len(m.setup_fields) == 3
    assert all(isinstance(f, SetupField) for f in m.setup_fields)


def test_field_attributes_preserved():
    m = validate_manifest(_manifest(setup_fields=[FULL_FIELD]))
    f = m.setup_fields[0]
    assert f.name == "business_name"
    assert f.label == "Business Name"
    assert f.type == "string"
    assert f.required is True
    assert f.default == "My Business"
    assert f.help_text == "Your public business name"
    assert f.placeholder == "Acme Co."
    assert f.simple_mode is True


# ── Secret field has vault_key ────────────────────────────────────────────────

def test_secret_field_has_vault_key():
    m = validate_manifest(_manifest(setup_fields=[SECRET_FIELD]))
    f = m.setup_fields[0]
    assert f.secret is True
    assert f.vault_key == "test_op:api_key"
    assert f.type == "secret"


def test_secret_field_not_in_settings_store_flag():
    m = validate_manifest(_manifest(setup_fields=[SECRET_FIELD]))
    f = m.setup_fields[0]
    assert f.secret is True


# ── Select field has options list ─────────────────────────────────────────────

def test_select_field_has_options():
    m = validate_manifest(_manifest(setup_fields=[SELECT_FIELD]))
    f = m.setup_fields[0]
    assert f.options == ["gmail", "google_sheets", "webhook"]
    assert f.default == "gmail"


# ── Number field has min/max ──────────────────────────────────────────────────

def test_number_field_has_min_max():
    m = validate_manifest(_manifest(setup_fields=[NUMBER_FIELD]))
    f = m.setup_fields[0]
    assert f.min == 1
    assert f.max == 10
    assert f.default == 3


# ── Mode visibility flags ─────────────────────────────────────────────────────

def test_advanced_mode_field_not_in_simple():
    m = validate_manifest(_manifest(setup_fields=[NUMBER_FIELD]))
    f = m.setup_fields[0]
    assert f.advanced_mode is True
    assert f.simple_mode is True  # default for simple_mode


def test_developer_mode_field():
    m = validate_manifest(_manifest(setup_fields=[DEV_FIELD]))
    f = m.setup_fields[0]
    assert f.developer_mode is True
    assert f.simple_mode is False


def test_simple_mode_default_true():
    m = validate_manifest(_manifest(setup_fields=[FULL_FIELD]))
    assert m.setup_fields[0].simple_mode is True


# ── requires_approval_if_enabled ─────────────────────────────────────────────

def test_boolean_field_approval_awareness():
    m = validate_manifest(_manifest(setup_fields=[BOOL_FIELD]))
    f = m.setup_fields[0]
    assert f.requires_approval_if_enabled == ["email.send"]


# ── Invalid field type raises error ──────────────────────────────────────────

def test_unknown_field_type_raises_error():
    bad_field = {"name": "x", "label": "X", "type": "dropdown"}
    with pytest.raises(ManifestValidationError, match="invalid type"):
        validate_manifest(_manifest(setup_fields=[bad_field]))


def test_all_valid_field_types_accepted():
    for ftype in VALID_FIELD_TYPES:
        f = {"name": "x", "label": "X", "type": ftype}
        m = validate_manifest(_manifest(setup_fields=[f]))
        assert m.setup_fields[0].type == ftype


# ── Missing required name/label raises error ─────────────────────────────────

def test_setup_field_missing_name_raises():
    bad = {"label": "No Name", "type": "string"}
    with pytest.raises(ManifestValidationError, match="missing required 'name'"):
        validate_manifest(_manifest(setup_fields=[bad]))


def test_setup_field_missing_label_raises():
    bad = {"name": "no_label", "type": "string"}
    with pytest.raises(ManifestValidationError, match="missing required 'label'"):
        validate_manifest(_manifest(setup_fields=[bad]))


# ── Non-list setup_fields raises error ───────────────────────────────────────

def test_setup_fields_not_list_raises():
    with pytest.raises(ManifestValidationError, match="must be a list"):
        validate_manifest(_manifest(setup_fields="string_not_list"))


# ── All field types in one manifest ──────────────────────────────────────────

def test_all_field_types_in_one_manifest():
    all_fields = [FULL_FIELD, SECRET_FIELD, SELECT_FIELD, NUMBER_FIELD, BOOL_FIELD, DEV_FIELD]
    m = validate_manifest(_manifest(setup_fields=all_fields))
    assert len(m.setup_fields) == 6
    types = {f.type for f in m.setup_fields}
    assert types == {"string", "secret", "select", "number", "boolean"}
