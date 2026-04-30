"""
cascadia/settings/profiles.py — Business type default profiles.

Each profile provides opinionated defaults for operators running in that
vertical. Safe Mode is always the default: approval_before_customer_message
is True in every profile. Callers may override individual fields after
applying a profile.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------

CONTRACTOR_DEFAULTS: Dict[str, Any] = {
    "approval_before_customer_message": True,
    "lead_source": "webhook",
    "follow_up_delay_hours": 2,
    "quote_requires_approval": True,
    "default_currency": "USD",
    "service_area_radius_miles": 50,
    "auto_schedule_jobs": False,
    "send_job_reminders": True,
    "reminder_hours_before": 24,
}

PROFESSIONAL_SERVICES_DEFAULTS: Dict[str, Any] = {
    "approval_before_customer_message": True,
    "lead_source": "gmail",
    "follow_up_delay_hours": 4,
    "quote_requires_approval": True,
    "default_currency": "USD",
    "invoice_on_completion": False,
    "auto_schedule_meetings": False,
    "send_meeting_reminders": True,
    "reminder_hours_before": 24,
    "track_billable_hours": True,
}

MEDICAL_CLINIC_DEFAULTS: Dict[str, Any] = {
    "approval_before_customer_message": True,
    "lead_source": "webhook",
    "follow_up_delay_hours": 1,
    "appointment_requires_approval": True,
    "default_currency": "USD",
    "hipaa_mode": True,
    "auto_send_appointment_reminders": False,
    "reminder_hours_before": 48,
    "collect_insurance_info": True,
    "no_auto_diagnosis": True,
}

RETAIL_ECOMMERCE_DEFAULTS: Dict[str, Any] = {
    "approval_before_customer_message": True,
    "lead_source": "webhook",
    "follow_up_delay_hours": 1,
    "order_requires_approval": False,
    "default_currency": "USD",
    "auto_send_order_confirmation": False,
    "auto_send_shipping_updates": False,
    "refund_requires_approval": True,
    "abandoned_cart_followup": True,
    "abandoned_cart_delay_hours": 2,
}

WAREHOUSE_INDUSTRIAL_DEFAULTS: Dict[str, Any] = {
    "approval_before_customer_message": True,
    "lead_source": "webhook",
    "follow_up_delay_hours": 4,
    "quote_requires_approval": True,
    "default_currency": "USD",
    "auto_create_purchase_orders": False,
    "low_stock_alerts": True,
    "low_stock_threshold_units": 10,
    "shift_scheduling": False,
    "safety_incident_log": True,
}

GENERAL_SMALL_BUSINESS_DEFAULTS: Dict[str, Any] = {
    "approval_before_customer_message": True,
    "lead_source": "gmail",
    "follow_up_delay_hours": 2,
    "quote_requires_approval": True,
    "default_currency": "USD",
    "auto_send_receipts": False,
    "send_appointment_reminders": True,
    "reminder_hours_before": 24,
}

_PROFILES: Dict[str, Dict[str, Any]] = {
    "contractor": CONTRACTOR_DEFAULTS,
    "professional_services": PROFESSIONAL_SERVICES_DEFAULTS,
    "medical_clinic": MEDICAL_CLINIC_DEFAULTS,
    "retail_ecommerce": RETAIL_ECOMMERCE_DEFAULTS,
    "warehouse_industrial": WAREHOUSE_INDUSTRIAL_DEFAULTS,
    "general_small_business": GENERAL_SMALL_BUSINESS_DEFAULTS,
}

_PROFILE_LABELS: Dict[str, str] = {
    "contractor": "Contractor / Field Services",
    "professional_services": "Professional Services",
    "medical_clinic": "Medical / Healthcare Clinic",
    "retail_ecommerce": "Retail / E-commerce",
    "warehouse_industrial": "Warehouse / Industrial",
    "general_small_business": "General Small Business",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_profiles() -> List[Dict[str, Any]]:
    """Return all profile IDs and labels."""
    return [
        {"id": pid, "label": label}
        for pid, label in _PROFILE_LABELS.items()
    ]


def get_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    """Return a copy of the defaults dict for *profile_id*, or None if unknown."""
    raw = _PROFILES.get(profile_id)
    if raw is None:
        return None
    return dict(raw)


def apply_profile(
    profile_id: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Return profile defaults merged with *overrides*.

    approval_before_customer_message is always forced to True regardless of
    overrides — Safe Mode is non-negotiable at profile-apply time. Callers
    that need to disable it must do so explicitly via cfg_save after applying.

    Raises ValueError for an unknown profile_id.
    """
    base = get_profile(profile_id)
    if base is None:
        raise ValueError(f"Unknown profile: {profile_id!r}. "
                         f"Valid profiles: {list(_PROFILES)}")
    if overrides:
        base.update(overrides)
    # Safe Mode invariant — always on after profile apply.
    base["approval_before_customer_message"] = True
    return base
