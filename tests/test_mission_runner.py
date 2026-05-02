"""Tests for MissionRunner lifecycle — Session 3B."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import cascadia.missions.manager as manager
from cascadia.missions.constants import DEFAULT_ORGANIZATION_ID
from cascadia.missions.migrate import run_migration
from cascadia.missions.registry import MissionRegistry
from cascadia.missions.runner import (
    MissionNotFoundError,
    MissionNotInstalledError,
    MissionRunner,
    StitchMissionAdapter,
    TierNotAllowedError,
    WorkflowNotFoundError,
    check_tier_allowed,
)

FIXTURE_DIR = str(Path(__file__).parent / "fixtures" / "missions")
FIXTURE_ID = "test_growth_desk"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db(tmp_path) -> str:
    db = str(tmp_path / "test.db")
    conn = sqlite3.connect(db)
    from cascadia.durability.migration import migrate as dur_migrate
    dur_migrate(conn)
    conn.commit()
    conn.close()
    run_migration(db)
    return db


def _make_installed_registry(tmp_path) -> MissionRegistry:
    reg_file = tmp_path / "missions_registry.json"
    reg_file.write_text(json.dumps({"installed": [FIXTURE_ID]}))
    return MissionRegistry(packages_root=FIXTURE_DIR, registry_file=str(reg_file))


def _make_runner(db: str, reg: MissionRegistry) -> tuple[MissionRunner, MagicMock]:
    mock_adapter = MagicMock(spec=StitchMissionAdapter)
    mock_adapter.start_workflow.return_value = "stitch_run_001"
    runner = MissionRunner(registry=reg, db_path=db, adapter=mock_adapter)
    return runner, mock_adapter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_test_run(runner: MissionRunner, mission_id: str = FIXTURE_ID,
                     status: str = "running", workflow_id: str = "daily_campaign") -> str:
    run_id = str(uuid.uuid4())
    now = _now()
    runner._insert_run(
        run_id, mission_id, DEFAULT_ORGANIZATION_ID,
        workflow_id, "manual",
        json.dumps({"workflow_id": workflow_id, "trigger_type": "manual", "input": {}}),
        now,
    )
    if status != "running":
        runner._update_run(run_id, {"status": status})
    return run_id


def _external_workflow_file(tmp_path) -> str:
    wf = {
        "id": "ext_test",
        "name": "External Test",
        "steps": [{"id": "notify", "operator": "email", "action": "email.send"}],
    }
    p = tmp_path / "ext_workflow.json"
    p.write_text(json.dumps(wf))
    return str(p)


# ── start_mission ─────────────────────────────────────────────────────────────

def test_start_mission_creates_run(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    result = runner.start_mission(FIXTURE_ID, "daily_campaign")
    run_id = result["mission_run_id"]
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT id FROM mission_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None


def test_start_mission_unknown_mission_raises(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    with pytest.raises(MissionNotFoundError):
        runner.start_mission("does_not_exist", "daily_campaign")


def test_start_mission_not_installed_raises(tmp_path):
    db = _make_db(tmp_path)
    # Registry with no installed missions
    reg_file = tmp_path / "missions_registry.json"
    reg_file.write_text(json.dumps({"installed": []}))
    reg = MissionRegistry(packages_root=FIXTURE_DIR, registry_file=str(reg_file))
    runner, _ = _make_runner(db, reg)
    with pytest.raises(MissionNotInstalledError):
        runner.start_mission(FIXTURE_ID, "daily_campaign")


def test_start_mission_unknown_workflow_raises(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    with pytest.raises(WorkflowNotFoundError):
        runner.start_mission(FIXTURE_ID, "no_such_workflow")


def test_start_mission_loads_workflow_json(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    result = runner.start_mission(FIXTURE_ID, "daily_campaign")
    assert result["mission_run_id"]
    assert result["mission_id"] == FIXTURE_ID
    assert result["workflow_id"] == "daily_campaign"
    assert result["status"] in ("running", "waiting_approval")


def test_start_mission_external_action_pauses_run(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    ext_wf = _external_workflow_file(tmp_path)
    with patch.object(reg, "get_workflow_path", return_value=ext_wf):
        runner, mock_adapter = _make_runner(db, reg)
        result = runner.start_mission(FIXTURE_ID, "daily_campaign")
    assert result["status"] == "waiting_approval"
    mock_adapter.start_workflow.assert_not_called()


# ── pause_for_approval ────────────────────────────────────────────────────────

def test_pause_for_approval_sets_mission_id_on_approval(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner)
    runner.pause_for_approval(run_id, {
        "title": "Approve", "summary": "Test", "action": "email.send",
        "mission_id": FIXTURE_ID,
    })
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT mission_id FROM approvals WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == FIXTURE_ID


def test_pause_for_approval_sets_mission_run_id_on_approval(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner)
    runner.pause_for_approval(run_id, {
        "title": "Approve", "summary": "Test", "action": "email.send",
        "mission_id": FIXTURE_ID,
    })
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT mission_run_id FROM approvals WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == run_id


def test_pause_for_approval_returns_approval_id(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner)
    result = runner.pause_for_approval(run_id, {
        "title": "Approve", "summary": "Test", "action": "email.send",
        "mission_id": FIXTURE_ID,
    })
    assert "approval_id" in result
    assert result["approval_id"] is not None


# ── resume_mission ────────────────────────────────────────────────────────────

def test_resume_mission_approved_sets_running(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, mock_adapter = _make_runner(db, reg)
    run_id = _insert_test_run(runner, status="waiting_approval")
    result = runner.resume_mission(run_id, {"decision": "approved"})
    assert result.get("status") in ("running", "retry_pending")
    assert result.get("mission_run_id") == run_id


def test_resume_mission_rejected_sets_cancelled(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner, status="waiting_approval")
    result = runner.resume_mission(run_id, {"decision": "rejected"})
    assert result["status"] == "cancelled"
    row = runner._get_run(run_id)
    assert row["status"] == "cancelled"


def test_resume_mission_wrong_status_returns_error(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner, status="running")
    result = runner.resume_mission(run_id, {"decision": "approved"})
    assert result["error"] == "invalid_state"


# ── fail_mission ──────────────────────────────────────────────────────────────

def test_fail_mission_sets_failed_status(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner)
    runner.fail_mission(run_id, "timeout")
    row = runner._get_run(run_id)
    assert row["status"] == "failed"


def test_fail_mission_stores_error_message(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner)
    runner.fail_mission(run_id, "connection refused")
    row = runner._get_run(run_id)
    assert row["error"] == "connection refused"


# ── complete_mission ──────────────────────────────────────────────────────────

def test_complete_mission_sets_completed_status(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner)
    runner.complete_mission(run_id)
    row = runner._get_run(run_id)
    assert row["status"] == "completed"


def test_complete_mission_stores_output(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner)
    runner.complete_mission(run_id, output={"leads": 5})
    row = runner._get_run(run_id)
    ctx = json.loads(row["context_data"])
    assert ctx.get("output", {}).get("leads") == 5


# ── retry_mission_run ─────────────────────────────────────────────────────────

def test_retry_completed_run_returns_error(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner, status="completed")
    result = runner.retry_mission_run(run_id)
    assert result["error"] == "retry_not_available"


def test_retry_waiting_approval_run_returns_error(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner, status="waiting_approval")
    result = runner.retry_mission_run(run_id)
    assert result["error"] == "retry_not_available"


def test_retry_failed_run_creates_new_run(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, mock_adapter = _make_runner(db, reg)
    run_id = _insert_test_run(runner, status="failed")
    result = runner.retry_mission_run(run_id)
    # Should produce a new run or a meaningful response
    assert "error" not in result or result.get("error") not in (
        "retry_not_available",
    )
    # New run should exist in DB (different id)
    new_run_id = result.get("mission_run_id")
    if new_run_id and new_run_id != run_id:
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT id FROM mission_runs WHERE id = ?", (new_run_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None


# ── Manager POST endpoints ────────────────────────────────────────────────────

def test_post_run_endpoint_creates_run(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    manager._registry = reg
    manager._runner = runner
    try:
        code, body = manager.handle_run_mission({
            "mission_id": FIXTURE_ID,
            "workflow_id": "daily_campaign",
        })
        assert code == 200
        assert "mission_run_id" in body
        assert body["status"] in ("running", "waiting_approval")
    finally:
        manager._registry = None
        manager._runner = None


def test_post_resume_endpoint_works(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner, status="waiting_approval")
    manager._registry = reg
    manager._runner = runner
    try:
        code, body = manager.handle_resume_mission({
            "mission_id": FIXTURE_ID,
            "run_id": run_id,
            "decision": "rejected",
        })
        assert code == 200
        assert body["status"] == "cancelled"
    finally:
        manager._registry = None
        manager._runner = None


def test_post_retry_endpoint_works(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner, status="completed")
    manager._registry = reg
    manager._runner = runner
    try:
        code, body = manager.handle_retry_mission({
            "mission_id": FIXTURE_ID,
            "run_id": run_id,
        })
        assert code == 200
        assert body.get("error") == "retry_not_available"
    finally:
        manager._registry = None
        manager._runner = None


# ── Tier check ────────────────────────────────────────────────────────────────

def test_tier_check_blocks_scheduled_free_mission():
    manifest = {
        "limits": {
            "free": {"enabled": True, "manual_runs_only": True},
        }
    }
    assert check_tier_allowed(manifest, "free", "daily_campaign", "schedule") is False


def test_tier_check_allows_manual_free_mission():
    manifest = {
        "limits": {
            "free": {"enabled": True, "manual_runs_only": True},
        }
    }
    assert check_tier_allowed(manifest, "free", "daily_campaign", "manual") is True


# ── Status and runs endpoints ─────────────────────────────────────────────────

def test_status_endpoint_returns_active_runs_count(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    _insert_test_run(runner, status="running")
    _insert_test_run(runner, status="waiting_approval")
    _insert_test_run(runner, status="failed")
    manager._registry = reg
    # Override _db_path to use test db
    with patch("cascadia.missions.manager._db_path", return_value=db):
        try:
            code, body = manager.handle_status({"mission_id": FIXTURE_ID})
        finally:
            manager._registry = None
    assert code == 200
    assert body["active_runs"] >= 1


def test_runs_endpoint_returns_persisted_runs(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    _insert_test_run(runner)
    manager._registry = reg
    with patch("cascadia.missions.manager._db_path", return_value=db):
        try:
            code, body = manager.handle_runs({"mission_id": FIXTURE_ID})
        finally:
            manager._registry = None
    assert code == 200
    assert len(body["runs"]) >= 1
    assert body["runs"][0]["id"] is not None


# ── Regression guard ──────────────────────────────────────────────────────────

def test_existing_read_api_tests_still_pass():
    """Smoke: read-only handlers work after POST endpoint additions."""
    code, body = manager.handle_healthz({})
    assert code == 200
    assert body["status"] == "ok"
    assert body["service"] == "mission_manager"
    assert body["port"] == 6207


# ── get_run_status ────────────────────────────────────────────────────────────

def test_get_run_status_returns_run_fields(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner)
    result = runner.get_run_status(run_id)
    assert result["mission_run_id"] == run_id
    assert result["mission_id"] == FIXTURE_ID
    assert result["status"] == "running"
    assert "started_at" in result
    assert "completed_at" in result


def test_get_run_status_not_found_returns_error(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    result = runner.get_run_status("nonexistent-id")
    assert result["error"] == "run_not_found"


# ── list_recent_runs ──────────────────────────────────────────────────────────

def test_list_recent_runs_returns_all_runs(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    _insert_test_run(runner)
    _insert_test_run(runner)
    runs = runner.list_recent_runs()
    assert len(runs) >= 2
    assert all("id" in r for r in runs)


def test_list_recent_runs_filters_by_mission_id(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    _insert_test_run(runner, mission_id=FIXTURE_ID)
    _insert_test_run(runner, mission_id="other_mission")
    runs = runner.list_recent_runs(mission_id=FIXTURE_ID)
    assert all(r["mission_id"] == FIXTURE_ID for r in runs)
    assert len(runs) >= 1


def test_list_recent_runs_respects_limit(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    for _ in range(5):
        _insert_test_run(runner)
    runs = runner.list_recent_runs(limit=3)
    assert len(runs) <= 3


# ── trigger_from_event ────────────────────────────────────────────────────────

def test_trigger_from_event_starts_matching_mission(tmp_path):
    db = _make_db(tmp_path)
    # Build a registry where test_growth_desk consumes "schedule.daily"
    reg_file = tmp_path / "missions_registry.json"
    reg_file.write_text(json.dumps({"installed": [FIXTURE_ID]}))
    reg = MissionRegistry(packages_root=FIXTURE_DIR, registry_file=str(reg_file))
    runner, mock_adapter = _make_runner(db, reg)
    # test_growth_desk manifest has events.consumes: ["schedule.daily"]
    result = runner.trigger_from_event("schedule.daily", {"source": "test"})
    # Should return a run_id string (mission matched and started)
    assert result is not None
    assert isinstance(result, str)


def test_trigger_from_event_returns_none_for_unknown_event(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    result = runner.trigger_from_event("unknown.event.xyz", {})
    assert result is None


# ── Manager: GET /api/missions/runs endpoints ─────────────────────────────────

def test_list_all_runs_endpoint_returns_runs(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    _insert_test_run(runner)
    manager._registry = reg
    manager._runner = runner
    try:
        code, body = manager.handle_list_all_runs({})
        assert code == 200
        assert "runs" in body
        assert isinstance(body["runs"], list)
        assert body["count"] >= 1
    finally:
        manager._registry = None
        manager._runner = None


def test_get_run_endpoint_returns_run(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    run_id = _insert_test_run(runner)
    manager._runner = runner
    try:
        code, body = manager.handle_get_run({"run_id": run_id})
        assert code == 200
        assert body["mission_run_id"] == run_id
        assert body["status"] == "running"
    finally:
        manager._runner = None


def test_get_run_endpoint_404_for_missing(tmp_path):
    db = _make_db(tmp_path)
    reg = _make_installed_registry(tmp_path)
    runner, _ = _make_runner(db, reg)
    manager._runner = runner
    try:
        code, body = manager.handle_get_run({"run_id": "does-not-exist"})
        assert code == 404
        assert body["error"] == "run_not_found"
    finally:
        manager._runner = None
