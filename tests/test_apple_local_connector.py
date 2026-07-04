from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from cascadia.connectors.apple_local.apple_bridge import AppleBridge
from cascadia.connectors.apple_local.connector import (
    _HealthHandler,
    execute_call,
    health_payload,
)


BASE = Path(__file__).parent.parent / "cascadia" / "connectors" / "apple_local"


def test_manifest_loads_with_expected_fields():
    data = json.loads((BASE / "manifest.json").read_text())

    assert data["id"] == "apple-local-connector"
    assert data["name"] == "Apple Local Connector"
    assert data["type"] == "connector"
    assert data["version"] == "1.0.0"
    assert data["category"] == "productivity"
    assert data["tier_required"] == "pro"
    assert data["port"] == 9601
    assert data["entry_point"] == "connector.py"
    assert data["auth_type"] == "none"
    assert data["nats_subjects"] == ["cascadia.connectors.apple-local-connector.>"]
    assert data["writes_external_systems"] is True
    assert data["network_access"] is False


def test_manifest_permissions_and_approvals():
    data = json.loads((BASE / "manifest.json").read_text())

    assert "apple.calendar.read" in data["permissions"]
    assert "apple.reminders.write" in data["permissions"]
    assert "apple.notes.delete" in data["permissions"]
    assert "calendar.create_event" in data["requires_approval_for"]
    assert "reminders.complete_item" in data["requires_approval_for"]
    assert "notes.delete_note" in data["requires_approval_for"]


def test_notes_hard_delete_disabled_by_default():
    data = json.loads((BASE / "manifest.json").read_text())
    setup = {field["name"]: field for field in data["setup_fields"]}

    assert setup["notes_hard_delete_enabled"]["default"] is False


def test_health_payload_shape_works():
    body = health_payload(AppleBridge(platform_name="Darwin"))

    assert body["ok"] is True
    assert body["status"] == "degraded"
    assert body["connector"] == "apple-local-connector"
    assert body["port"] == 9601
    assert body["phase"] == 1
    assert body["readiness"]["platform"] == "Darwin"
    assert body["readiness"]["calendar"]["available"] is False


def test_http_health_and_api_health_same_shape():
    health = _handler_body("/health")
    api_health = _handler_body("/api/health")

    assert health.keys() == api_health.keys()
    assert health["connector"] == api_health["connector"]
    assert health["readiness"].keys() == api_health["readiness"].keys()


def _handler_body(path: str) -> dict:
    handler = object.__new__(_HealthHandler)
    handler.path = path
    handler.wfile = BytesIO()
    handler.send_response = lambda _status: None
    handler.send_header = lambda _name, _value: None
    handler.end_headers = lambda: None

    handler.do_GET()

    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_non_macos_degradation_does_not_crash():
    body = health_payload(AppleBridge(platform_name="Linux"))

    assert body["status"] == "degraded"
    assert body["readiness"]["is_macos"] is False
    assert body["readiness"]["calendar"]["available"] is False
    assert "only available on macOS" in body["readiness"]["notes"]["reason"]


def test_read_only_actions_return_safely():
    bridge = AppleBridge(platform_name="Linux")

    for action in (
        "calendar.list_calendars",
        "calendar.list_events",
        "calendar.get_event",
        "reminders.list_lists",
        "reminders.list_items",
        "reminders.get_item",
        "notes.list_folders",
        "notes.search",
        "notes.get_note",
    ):
        result = execute_call({"action": action}, bridge=bridge)
        assert result["status"] == "unavailable"
        assert result["ok"] is False


def test_mutating_actions_do_not_mutate_without_approval():
    for action in (
        "calendar.create_event",
        "calendar.update_event",
        "calendar.delete_event",
        "reminders.create_item",
        "reminders.update_item",
        "reminders.complete_item",
        "reminders.delete_item",
        "notes.create_note",
        "notes.update_note",
        "notes.archive_note",
        "notes.delete_note",
    ):
        result = execute_call({"action": action})
        assert result["ok"] is False
        assert result["status"] == "approval_required"


def test_mutating_actions_execute_when_approved():
    """An approved mutating action is dispatched to the real bridge adapter
    method (verified here against a mocked adapter — no real macOS calls)."""

    class _RecordingNotes:
        def __init__(self):
            self.called_with = None

        def delete_note(self, **payload):
            self.called_with = payload
            return {"ok": True, "deleted": True, "note_id": payload.get("note_id")}

    bridge = AppleBridge(platform_name="Darwin", notes=_RecordingNotes())
    result = execute_call(
        {"action": "notes.delete_note", "approved": True, "note_id": "abc123"},
        bridge=bridge,
    )

    # No longer the phase-1 stub — the adapter actually ran.
    assert result["ok"] is True
    assert result["deleted"] is True
    assert result["note_id"] == "abc123"
    assert bridge.notes.called_with["note_id"] == "abc123"
    assert result.get("status") != "phase_1_not_implemented"
