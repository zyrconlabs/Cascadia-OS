import os
import importlib
import pytest

from cascadia.memory_governor import (
    MEMORY_GOVERNOR_ENABLED,
    OUTBOX_ENABLED,
    RAM_LOG_BUFFER_ENABLED,
    MISSION_COMPACTION_ENABLED,
    classify_event,
    should_persist,
    RingBuffer,
    Outbox,
    compact_mission,
    enforce_retention,
    VERSION,
)


# ── Module imports ──────────────────────────────────────────────────────────

def test_module_imports():
    """All public symbols are importable."""
    assert VERSION.endswith("-skeleton")


# ── FIX 2 — verifies actual module constants, not env ──────────────────────

def test_all_flags_default_false(monkeypatch):
    """No flag defaults to true. Reload module to verify."""
    import cascadia.memory_governor as mg
    import cascadia.memory_governor.flags as mg_flags

    for flag in [
        "MEMORY_GOVERNOR_ENABLED",
        "OUTBOX_ENABLED",
        "RAM_LOG_BUFFER_ENABLED",
        "MISSION_COMPACTION_ENABLED",
    ]:
        monkeypatch.delenv(flag, raising=False)

    mg_flags = importlib.reload(mg_flags)
    mg = importlib.reload(mg)

    assert mg.MEMORY_GOVERNOR_ENABLED is False
    assert mg.OUTBOX_ENABLED is False
    assert mg.RAM_LOG_BUFFER_ENABLED is False
    assert mg.MISSION_COMPACTION_ENABLED is False


# ── Classifier tests ────────────────────────────────────────────────────────

def test_classifier_known_persistent_events():
    assert classify_event("approval_required") == "checkpoint"
    assert classify_event("security_event") == "security"
    assert classify_event("run_completed") == "audit"


def test_classifier_known_ephemeral_events():
    assert classify_event("heartbeat_ok") == "ephemeral"
    assert classify_event("health_check_ok") == "ephemeral"


def test_classifier_unknown_defaults_to_ephemeral():
    assert classify_event("totally_made_up_event") == "ephemeral"


# ── Policy tests ────────────────────────────────────────────────────────────

def test_should_persist_audit_yes():
    assert should_persist("run_completed") is True
    assert should_persist("security_event") is True


def test_should_persist_ephemeral_no():
    assert should_persist("heartbeat_ok") is False
    assert should_persist("unknown_event") is False


# ── Ring buffer tests ───────────────────────────────────────────────────────

def test_ring_buffer_capacity_enforced():
    rb = RingBuffer(capacity=3)
    for i in range(5):
        rb.append({"event": i})
    stats = rb.stats()
    assert stats["current_size"] == 3
    assert stats["dropped_count"] == 2


def test_ring_buffer_snapshot():
    rb = RingBuffer(capacity=10)
    rb.append({"event": "a"})
    rb.append({"event": "b"})
    snap = rb.snapshot()
    assert len(snap) == 2
    assert snap[0]["event"] == "a"


def test_ring_buffer_clear():
    rb = RingBuffer(capacity=5)
    rb.append({"event": 1})
    rb.clear()
    assert rb.stats()["current_size"] == 0


# ── Outbox tests (skeleton — flag off) ─────────────────────────────────────

def test_outbox_disabled_returns_none():
    """When OUTBOX_ENABLED=false, enqueue returns None."""
    ob = Outbox(db_path=":memory:")
    result = ob.enqueue(
        run_id="r1",
        action_type="email",
        payload_hash="abc",
        idempotency_key="key1",
    )
    assert result is None


def test_outbox_disabled_get_pending_empty():
    ob = Outbox(db_path=":memory:")
    assert ob.get_pending() == []


# ── Compactor tests ─────────────────────────────────────────────────────────

def test_compact_mission_pass_through_when_disabled():
    """Skeleton: returns input trace unchanged."""
    result = compact_mission(
        mission_id="m1",
        full_trace=[{"step": 1}, {"step": 2}],
    )
    assert result["compacted"] is False
    assert "trace" in result


# ── Retention tests ─────────────────────────────────────────────────────────

def test_enforce_retention_noop_when_disabled():
    result = enforce_retention(
        directory="/tmp/anywhere",
        max_bytes=1000,
        high_water_bytes=800,
    )
    assert result["enforced"] is False
    assert result["bytes_freed"] == 0
