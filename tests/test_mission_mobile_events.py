"""Tests for MobileMissionEventBridge — Session 3C."""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import cascadia.missions.manager as manager
from cascadia.missions.mobile_events import MobileMissionEventBridge


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fresh_bridge() -> MobileMissionEventBridge:
    """Return a brand-new bridge with empty queue (no singleton contamination)."""
    return MobileMissionEventBridge()


def _iso_ago(seconds: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return dt.isoformat()


# ── publish → queue ───────────────────────────────────────────────────────────

def test_publish_adds_to_pending_queue():
    bridge = _fresh_bridge()
    bridge.publish("mission.started", {"mission_id": "growth_desk", "mission_run_id": "r1"})
    events = bridge.get_pending_events()
    assert len(events) == 1
    assert events[0]["event"] == "mission.started"


def test_get_pending_events_filters_by_timestamp():
    bridge = _fresh_bridge()
    bridge.publish("mission.started", {"mission_id": "gd", "mission_run_id": "r1"})
    cutoff = datetime.now(timezone.utc).isoformat()
    time.sleep(0.01)
    bridge.publish("mission.completed", {"mission_id": "gd", "mission_run_id": "r1"})

    events = bridge.get_pending_events(since_timestamp=cutoff)
    assert len(events) == 1
    assert events[0]["event"] == "mission.completed"


def test_clear_delivered_removes_events():
    bridge = _fresh_bridge()
    bridge.publish("mission.started", {"mission_id": "gd"})
    events = bridge.get_pending_events()
    assert len(events) == 1
    event_ids = [e["event_id"] for e in events]
    removed = bridge.clear_delivered(event_ids)
    assert removed == 1
    assert bridge.get_pending_events() == []


def test_queue_max_size_drops_oldest():
    bridge = _fresh_bridge()
    for i in range(101):
        bridge.publish("mission.started", {"mission_id": f"m{i}"})
    events = bridge.get_pending_events()
    assert len(events) == 100
    # Oldest (mission_id="m0") should be dropped
    mission_ids = [e["mission_id"] for e in events]
    assert "m0" not in mission_ids
    assert "m100" in mission_ids


def test_publish_formats_mobile_safe_payload():
    bridge = _fresh_bridge()
    bridge.publish("mission.approval_requested", {
        "mission_id": "growth_desk",
        "mission_run_id": "run-abc",
        "title": "Campaign needs approval",
        "summary": "Please review",
    })
    events = bridge.get_pending_events()
    e = events[0]
    assert "event_id" in e
    assert "event" in e
    assert "mission_id" in e
    assert "mission_run_id" in e
    assert "timestamp" in e
    assert "data" in e
    assert e["mission_id"] == "growth_desk"
    assert e["title"] == "Campaign needs approval"


def test_publish_does_not_raise_on_delivery_failure():
    bridge = _fresh_bridge()
    bad_runtime = MagicMock()
    bad_runtime.broadcast_event.side_effect = RuntimeError("socket closed")
    bridge.set_ws_runtime(bad_runtime)
    # Must not raise even with a failing WS runtime
    bridge.publish("mission.started", {"mission_id": "gd"})
    # Event still lands in queue despite WS failure
    assert len(bridge.get_pending_events()) == 1


# ── Manager endpoints ─────────────────────────────────────────────────────────

def test_pending_events_endpoint_returns_events():
    bridge = _fresh_bridge()
    bridge.publish("mission.started", {"mission_id": "gd"})
    manager._bridge = bridge
    try:
        code, body = manager.handle_pending_events({})
        assert code == 200
        assert "events" in body
        assert len(body["events"]) >= 1
    finally:
        manager._bridge = None


def test_delivered_endpoint_clears_events():
    bridge = _fresh_bridge()
    bridge.publish("mission.started", {"mission_id": "gd"})
    events = bridge.get_pending_events()
    event_ids = [e["event_id"] for e in events]
    manager._bridge = bridge
    try:
        code, body = manager.handle_delivered_events({"event_ids": event_ids})
        assert code == 200
        assert body["cleared"] == 1
        assert bridge.get_pending_events() == []
    finally:
        manager._bridge = None
