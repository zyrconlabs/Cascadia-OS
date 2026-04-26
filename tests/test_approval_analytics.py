"""Tests for approval_analytics() in RunStore (Task 6 — Sprint v2)."""
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta

from cascadia.durability.run_store import RunStore
from cascadia.durability.migration import migrate
from cascadia.system.approval_store import ApprovalStore


def _make_store(tmp_path):
    db = str(tmp_path / "analytics.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    conn.commit()
    conn.close()
    return RunStore(db)


def _now_iso(offset_minutes=0):
    dt = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
    return dt.isoformat()


def _seed_run(store, run_id):
    now = _now_iso()
    store.create_run({"run_id": run_id, "operator_id": "op", "created_at": now, "updated_at": now})
    return run_id


@pytest.fixture
def store(tmp_path):
    return _make_store(tmp_path)


def test_analytics_empty(store):
    result = store.approval_analytics()
    assert result["total"] == 0
    assert result["approved"] == 0
    assert result["rejected"] == 0
    assert result["edited"] == 0
    assert result["timed_out"] == 0
    assert result["avg_decision_minutes"] is None
    assert result["by_risk"] == {}


def test_analytics_counts_approved(store):
    run_id = _seed_run(store, "r1")
    appr = ApprovalStore(store)
    aid = appr.request_approval(run_id, 0, "send_email")
    appr.record_decision(aid, "approved", "alice")

    result = store.approval_analytics()
    assert result["total"] == 1
    assert result["approved"] == 1
    assert result["rejected"] == 0


def test_analytics_counts_denied(store):
    run_id = _seed_run(store, "r2")
    appr = ApprovalStore(store)
    aid = appr.request_approval(run_id, 0, "send_email")
    appr.record_decision(aid, "denied", "bob")

    result = store.approval_analytics()
    assert result["rejected"] == 1
    assert result["approved"] == 0


def test_analytics_counts_timed_out(store):
    run_id = _seed_run(store, "r3")
    appr = ApprovalStore(store)
    aid = appr.request_approval(run_id, 0, "send_email")
    appr.record_decision(aid, "approved", "system:timeout")

    result = store.approval_analytics()
    # timed_out are not counted in approved
    assert result["timed_out"] == 1
    assert result["approved"] == 0


def test_analytics_counts_edited(store):
    run_id = _seed_run(store, "r4")
    appr = ApprovalStore(store)
    aid = appr.request_approval(run_id, 0, "send_email")
    appr.edit_and_approve(aid, "carol", "Edited body")

    result = store.approval_analytics()
    assert result["edited"] == 1
    assert result["approved"] == 1


def test_analytics_avg_decision_minutes(store):
    run_id = _seed_run(store, "r5")
    appr = ApprovalStore(store)
    aid = appr.request_approval(run_id, 0, "send_email")
    # Manually set created_at 10 minutes ago
    created = _now_iso(-10)
    with store.connection() as conn:
        conn.execute("UPDATE approvals SET created_at=? WHERE id=?", (created, aid))
    appr.record_decision(aid, "approved", "dave")

    result = store.approval_analytics()
    assert result["avg_decision_minutes"] is not None
    assert result["avg_decision_minutes"] >= 9.0  # at least ~10 min


def test_analytics_by_risk(store):
    run_id = _seed_run(store, "r6")
    appr = ApprovalStore(store)
    aid = appr.request_approval(run_id, 0, "send_email")
    with store.connection() as conn:
        conn.execute("UPDATE approvals SET risk_level='HIGH' WHERE id=?", (aid,))

    result = store.approval_analytics()
    assert result["by_risk"].get("HIGH", 0) >= 1


def test_analytics_days_field(store):
    result = store.approval_analytics(days=7)
    assert result["days"] == 7


def test_analytics_multiple_mixed(store):
    for i, decision in enumerate(["approved", "denied", "approved"]):
        rid = _seed_run(store, f"rm{i}")
        appr = ApprovalStore(store)
        aid = appr.request_approval(rid, 0, "action")
        appr.record_decision(aid, decision, "tester")

    result = store.approval_analytics()
    assert result["total"] == 3
    assert result["approved"] == 2
    assert result["rejected"] == 1
