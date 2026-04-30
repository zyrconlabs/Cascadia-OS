"""Tests for cascadia/settings/profiles.py — business type default profiles."""
from __future__ import annotations

import pytest
from cascadia.settings.profiles import (
    apply_profile,
    get_profile,
    list_profiles,
    CONTRACTOR_DEFAULTS,
    PROFESSIONAL_SERVICES_DEFAULTS,
    MEDICAL_CLINIC_DEFAULTS,
    RETAIL_ECOMMERCE_DEFAULTS,
    WAREHOUSE_INDUSTRIAL_DEFAULTS,
    GENERAL_SMALL_BUSINESS_DEFAULTS,
)


# ── list_profiles ─────────────────────────────────────────────────────────────

class TestListProfiles:

    def test_returns_six_profiles(self):
        profiles = list_profiles()
        assert len(profiles) == 6

    def test_all_have_id_and_label(self):
        for p in list_profiles():
            assert "id" in p
            assert "label" in p
            assert isinstance(p["id"], str)
            assert isinstance(p["label"], str)

    def test_known_ids_present(self):
        ids = {p["id"] for p in list_profiles()}
        assert "contractor" in ids
        assert "medical_clinic" in ids
        assert "general_small_business" in ids


# ── get_profile ───────────────────────────────────────────────────────────────

class TestGetProfile:

    def test_returns_dict_for_valid_id(self):
        result = get_profile("contractor")
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_returns_none_for_unknown_id(self):
        assert get_profile("nonexistent_vertical") is None

    def test_returns_copy_not_reference(self):
        a = get_profile("contractor")
        b = get_profile("contractor")
        a["extra_key"] = "mutated"
        assert "extra_key" not in b
        assert "extra_key" not in CONTRACTOR_DEFAULTS

    def test_all_six_profiles_retrievable(self):
        for pid in ["contractor", "professional_services", "medical_clinic",
                    "retail_ecommerce", "warehouse_industrial", "general_small_business"]:
            result = get_profile(pid)
            assert result is not None, f"get_profile({pid!r}) returned None"


# ── Safe Mode invariant ───────────────────────────────────────────────────────

class TestSafeModeInvariant:

    def test_contractor_has_approval_true(self):
        assert CONTRACTOR_DEFAULTS["approval_before_customer_message"] is True

    def test_professional_services_has_approval_true(self):
        assert PROFESSIONAL_SERVICES_DEFAULTS["approval_before_customer_message"] is True

    def test_medical_clinic_has_approval_true(self):
        assert MEDICAL_CLINIC_DEFAULTS["approval_before_customer_message"] is True

    def test_retail_ecommerce_has_approval_true(self):
        assert RETAIL_ECOMMERCE_DEFAULTS["approval_before_customer_message"] is True

    def test_warehouse_industrial_has_approval_true(self):
        assert WAREHOUSE_INDUSTRIAL_DEFAULTS["approval_before_customer_message"] is True

    def test_general_small_business_has_approval_true(self):
        assert GENERAL_SMALL_BUSINESS_DEFAULTS["approval_before_customer_message"] is True


# ── apply_profile ─────────────────────────────────────────────────────────────

class TestApplyProfile:

    def test_returns_dict_for_valid_profile(self):
        result = apply_profile("contractor")
        assert isinstance(result, dict)

    def test_raises_for_unknown_profile(self):
        with pytest.raises(ValueError, match="Unknown profile"):
            apply_profile("bogus_industry")

    def test_overrides_are_applied(self):
        result = apply_profile("contractor", overrides={"default_currency": "CAD"})
        assert result["default_currency"] == "CAD"

    def test_safe_mode_not_overridable(self):
        result = apply_profile("contractor", overrides={"approval_before_customer_message": False})
        assert result["approval_before_customer_message"] is True

    def test_none_overrides_ok(self):
        result = apply_profile("general_small_business", overrides=None)
        assert "approval_before_customer_message" in result

    def test_empty_overrides_ok(self):
        result = apply_profile("retail_ecommerce", overrides={})
        assert "approval_before_customer_message" in result

    def test_result_is_independent_copy(self):
        r1 = apply_profile("contractor")
        r2 = apply_profile("contractor")
        r1["sentinel"] = True
        assert "sentinel" not in r2

    def test_medical_clinic_has_hipaa_mode(self):
        result = apply_profile("medical_clinic")
        assert result.get("hipaa_mode") is True

    def test_warehouse_has_safety_incident_log(self):
        result = apply_profile("warehouse_industrial")
        assert result.get("safety_incident_log") is True

    def test_retail_has_abandoned_cart_followup(self):
        result = apply_profile("retail_ecommerce")
        assert result.get("abandoned_cart_followup") is True
