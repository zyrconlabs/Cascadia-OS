"""Tests for cascadia.settings.validator (Phase 4)."""
from __future__ import annotations

import pytest
from cascadia.settings.validator import (
    validate_patch, validate_field, validate_approval_coverage,
    is_safe_mode_satisfied, ValidationResult, FieldResult, RISKY_ACTIONS,
)
from cascadia.shared.manifest_schema import validate_manifest, SetupField

MANIFEST_DATA = {
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
         "options": ["gmail", "webhook", "hubspot"], "default": "gmail"},
        {"name": "retry_limit", "label": "Retry Limit", "type": "number",
         "default": 3, "min": 1, "max": 10},
        {"name": "ask_before_sending", "label": "Ask Before Sending",
         "type": "boolean", "default": True,
         "requires_approval_if_enabled": ["email.send"]},
        {"name": "reply_behavior", "label": "Reply Behavior", "type": "select",
         "options": ["ask_before_sending", "auto_send", "draft_only"],
         "default": "ask_before_sending"},
        {"name": "api_key", "label": "API Key", "type": "secret",
         "secret": True, "vault_key": "test_op:api_key"},
    ],
}


@pytest.fixture
def manifest():
    return validate_manifest(MANIFEST_DATA)


# ── Valid patch passes ────────────────────────────────────────────────────────

def test_valid_patch_passes(manifest):
    result = validate_patch({"business_name": "Acme", "lead_source": "gmail"}, manifest)
    assert result.valid is True
    assert result.errors == []


def test_empty_patch_passes_when_all_required_have_defaults(manifest):
    result = validate_patch({}, manifest)
    assert result.valid is True


# ── Unknown field fails ───────────────────────────────────────────────────────

def test_unknown_field_fails(manifest):
    result = validate_patch({"nonexistent": "val"}, manifest)
    assert result.valid is False
    assert any("nonexistent" in e for e in result.errors)


# ── Missing required field fails ──────────────────────────────────────────────

def test_missing_required_no_default_fails():
    data = {**MANIFEST_DATA, "setup_fields": [
        {"name": "webhook_url", "label": "Webhook URL", "type": "string",
         "required": True, "default": None},
    ]}
    m = validate_manifest(data)
    result = validate_patch({}, m)
    assert result.valid is False
    assert any("webhook_url" in e for e in result.errors)


def test_required_field_with_default_not_flagged(manifest):
    result = validate_patch({}, manifest)
    assert result.valid is True


# ── Type validation ───────────────────────────────────────────────────────────

def test_number_outside_range_fails(manifest):
    result = validate_patch({"retry_limit": 20}, manifest)
    assert result.valid is False
    assert any("retry_limit" in e for e in result.errors)


def test_number_at_boundary_passes(manifest):
    result = validate_patch({"retry_limit": 10}, manifest)
    assert result.valid is True


def test_select_invalid_option_fails(manifest):
    result = validate_patch({"lead_source": "carrier_pigeon"}, manifest)
    assert result.valid is False
    assert any("lead_source" in e for e in result.errors)


def test_select_valid_option_passes(manifest):
    result = validate_patch({"lead_source": "webhook"}, manifest)
    assert result.valid is True


# ── Secret field protection ───────────────────────────────────────────────────

def test_secret_field_raw_value_fails(manifest):
    result = validate_patch({"api_key": "plaintext_secret"}, manifest)
    assert result.valid is False
    assert any("api_key" in e for e in result.errors)


# ── Auto-send without approval gate fails ────────────────────────────────────

def test_auto_send_without_approval_fails(manifest):
    result = validate_patch({"reply_behavior": "auto_send"}, manifest)
    assert result.valid is False
    assert any("auto_send" in e for e in result.errors)


def test_non_auto_send_reply_behavior_passes(manifest):
    result = validate_patch({"reply_behavior": "ask_before_sending"}, manifest)
    assert result.valid is True


# ── Safe Mode applied for risky actions ──────────────────────────────────────

def test_safe_mode_applied_when_approval_gate_disabled(manifest):
    # Disabling ask_before_sending removes the approval gate for email.send → safe mode
    result = validate_patch({"ask_before_sending": False}, manifest)
    assert len(result.safe_mode_applied) > 0


def test_safe_mode_not_applied_when_gate_enabled(manifest):
    result = validate_patch({"ask_before_sending": True}, manifest)
    assert len(result.safe_mode_applied) == 0


def test_safe_mode_message_is_descriptive(manifest):
    result = validate_patch({"ask_before_sending": False}, manifest)
    assert any("email.send" in msg for msg in result.safe_mode_applied)


# ── validate_field unit tests ─────────────────────────────────────────────────

def test_validate_field_number_below_min():
    f = SetupField(name="x", label="X", type="number", min=5, max=10)
    result = validate_field(f, 2)
    assert result.valid is False
    assert "minimum" in result.error


def test_validate_field_number_above_max():
    f = SetupField(name="x", label="X", type="number", min=1, max=5)
    result = validate_field(f, 99)
    assert result.valid is False
    assert "maximum" in result.error


def test_validate_field_select_invalid():
    f = SetupField(name="x", label="X", type="select", options=["a", "b"])
    result = validate_field(f, "c")
    assert result.valid is False


def test_validate_field_boolean_non_bool():
    f = SetupField(name="x", label="X", type="boolean")
    result = validate_field(f, "yes")
    assert result.valid is False


def test_validate_field_valid_string():
    f = SetupField(name="x", label="X", type="string")
    result = validate_field(f, "hello")
    assert result.valid is True


def test_validate_field_none_on_non_required():
    f = SetupField(name="x", label="X", type="string", required=False)
    result = validate_field(f, None)
    assert result.valid is True


def test_validate_field_none_on_required_no_default():
    f = SetupField(name="x", label="X", type="string", required=True, default=None)
    result = validate_field(f, None)
    assert result.valid is False


# ── validate_approval_coverage ────────────────────────────────────────────────

def test_risky_actions_constant_populated():
    assert "email.send" in RISKY_ACTIONS
    assert "payment.charge" in RISKY_ACTIONS
    assert "crm.write" in RISKY_ACTIONS
