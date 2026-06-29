"""Lightweight response helpers for the Apple Local connector."""
from __future__ import annotations

from typing import Any


def ok_response(**fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": True}
    body.update(fields)
    return body


def unavailable_response(domain: str, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "unavailable",
        "domain": domain,
        "reason": reason,
    }


def approval_required_response(action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "approval_required",
        "action": action,
        "reason": "This action can write to or delete from Apple apps and requires approval.",
        "phase": 1,
    }


def phase_1_not_implemented_response(action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "phase_1_not_implemented",
        "action": action,
        "reason": "Apple mutations are intentionally disabled in Phase 1.",
        "phase": 1,
    }


def unknown_action_response(action: str | None) -> dict[str, Any]:
    return {"ok": False, "status": "unknown_action", "action": action}
