"""Tests for edit-and-approve flow (Task 3 — Sprint v2)."""
import sqlite3
import tempfile
import os
import pytest

from cascadia.durability.run_store import RunStore
from cascadia.durability.migration import migrate
from cascadia.system.approval_store import ApprovalStore


def _make_store(tmp_path):
    db = str(tmp_path / "test.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    conn.commit()
    conn.close()
    return RunStore(db)


@pytest.fixture
def store(tmp_path):
    return _make_store(tmp_path)


@pytest.fixture
def approval_store(store):
    return ApprovalStore(store)


def _seed_run(store, run_id="run_001"):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    store.create_run({
        "run_id": run_id,
        "operator_id": "test_op",
        "created_at": now,
        "updated_at": now,
    })
    return run_id


def test_edit_and_approve_stores_content(store, approval_store):
    run_id = _seed_run(store)
    appr_id = approval_store.request_approval(run_id, step_index=0, action_key="send_email")

    approval_store.edit_and_approve(
        appr_id, actor="alice", edited_content="Edited body", edit_summary="Fixed typo"
    )

    with store.connection() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (appr_id,)).fetchone()

    assert row["decision"] == "approved"
    assert row["actor"] == "alice"
    assert row["edited_content"] == "Edited body"
    assert row["edit_summary"] == "Fixed typo"
    assert row["decided_at"] is not None


def test_edit_and_approve_wakes_run(store, approval_store):
    run_id = _seed_run(store)
    appr_id = approval_store.request_approval(run_id, step_index=0, action_key="send_email")

    run_before = store.get_run(run_id)
    assert run_before["run_state"] == "waiting_human"

    approval_store.edit_and_approve(appr_id, actor="bob", edited_content="Final version")

    run_after = store.get_run(run_id)
    assert run_after["run_state"] == "retrying"


def test_edit_and_approve_missing_row_does_not_raise(store, approval_store):
    # Non-existent approval_id — edit_and_approve should not crash
    # (row lookup returns None; wake is skipped)
    approval_store.edit_and_approve(9999, actor="bob", edited_content="content")


def test_record_decision_approved_does_not_store_edit(store, approval_store):
    run_id = _seed_run(store)
    appr_id = approval_store.request_approval(run_id, step_index=0, action_key="send_proposal")
    approval_store.record_decision(appr_id, "approved", "carol", "looks good")

    with store.connection() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (appr_id,)).fetchone()

    assert row["decision"] == "approved"
    assert row["edited_content"] is None


def test_edit_and_approve_sets_decided_at(store, approval_store):
    run_id = _seed_run(store)
    appr_id = approval_store.request_approval(run_id, step_index=1, action_key="create_quote")

    approval_store.edit_and_approve(appr_id, actor="dave", edited_content="Revised quote")

    with store.connection() as conn:
        row = conn.execute("SELECT decided_at FROM approvals WHERE id = ?", (appr_id,)).fetchone()
    assert row["decided_at"] is not None


def test_edit_and_approve_empty_summary_allowed(store, approval_store):
    run_id = _seed_run(store)
    appr_id = approval_store.request_approval(run_id, step_index=0, action_key="send_sms")

    approval_store.edit_and_approve(appr_id, actor="eve", edited_content="SMS text", edit_summary="")

    with store.connection() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (appr_id,)).fetchone()
    assert row["decision"] == "approved"
    assert row["edit_summary"] == ""


def test_migration_adds_edit_columns(tmp_path):
    db = str(tmp_path / "migrate_test.db")
    conn = sqlite3.connect(db)
    migrate(conn)
    conn.commit()
    cols = [row[1] for row in conn.execute("PRAGMA table_info(approvals)")]
    conn.close()
    assert "edited_content" in cols
    assert "edit_summary" in cols
    assert "risk_level" in cols
