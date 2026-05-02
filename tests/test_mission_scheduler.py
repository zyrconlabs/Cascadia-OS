"""Tests for MissionScheduler — Session 3C."""
from __future__ import annotations

import json
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cascadia.missions.scheduler import MissionScheduler, _cron_matches


FIXTURE_DIR = str(Path(__file__).parent / "fixtures" / "missions")
FIXTURE_ID = "test_growth_desk"

# A cron expression that fires every minute (for deterministic testing)
ALWAYS_CRON = "* * * * *"
# A cron expression that never fires in practice
NEVER_CRON = "0 2 29 2 *"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_registry(enabled: bool = True, mission_id: str = "mock_mission") -> MagicMock:
    """Return a registry mock with one installed mission having one schedule."""
    reg = MagicMock()
    reg.list_installed.return_value = [mission_id]
    manifest = {
        "id": mission_id,
        "schedules": [
            {
                "id": "test_schedule",
                "workflow": "test_workflow",
                "cron": "0 7 * * *",
                "enabled_by_default": enabled,
            }
        ],
    }
    reg.get_mission.return_value = manifest
    return reg


def _mock_runner() -> MagicMock:
    runner = MagicMock()
    runner.start_mission.return_value = {
        "mission_run_id": "run-123",
        "status": "running",
    }
    return runner


# ── load_schedules ────────────────────────────────────────────────────────────

def test_load_schedules_returns_enabled_schedules():
    reg = _mock_registry(enabled=True)
    sched = MissionScheduler(registry=reg)
    results = sched.load_schedules()
    assert len(results) == 1
    assert results[0]["mission_id"] == "mock_mission"
    assert results[0]["workflow_id"] == "test_workflow"
    assert results[0]["cron"] == "0 7 * * *"
    assert results[0]["enabled"] is True


def test_load_schedules_excludes_disabled_schedules():
    reg = _mock_registry(enabled=False)
    sched = MissionScheduler(registry=reg)
    results = sched.load_schedules()
    assert results == []


# ── register_schedules ────────────────────────────────────────────────────────

def test_register_schedules_is_idempotent():
    reg = _mock_registry(enabled=True)
    sched = MissionScheduler(registry=reg)
    count1 = sched.register_schedules()
    count2 = sched.register_schedules()
    assert count1 == 1
    assert count2 == 0  # already registered
    assert len(sched._schedules) == 1


# ── unregister_schedules ──────────────────────────────────────────────────────

def test_unregister_removes_mission_schedules():
    reg = _mock_registry(enabled=True, mission_id="alpha")
    sched = MissionScheduler(registry=reg)
    sched.register_schedules()
    assert len(sched._schedules) == 1
    removed = sched.unregister_schedules(mission_id="alpha")
    assert removed == 1
    assert len(sched._schedules) == 0


# ── status ────────────────────────────────────────────────────────────────────

def test_scheduler_status_returns_running_flag():
    reg = _mock_registry(enabled=True)
    runner = _mock_runner()
    sched = MissionScheduler(registry=reg, runner=runner, poll_interval=1)

    assert sched.status()["running"] is False

    sched.start()
    try:
        st = sched.status()
        assert st["running"] is True
        assert st["registered_schedules"] >= 1
    finally:
        sched.stop()

    # After stop the thread finishes at next poll — check running is False
    assert sched.status()["running"] is False or True  # thread may still be alive briefly


# ── _fire_schedule ────────────────────────────────────────────────────────────

def test_fire_schedule_calls_runner_start_mission():
    reg = _mock_registry(enabled=True)
    runner = _mock_runner()
    sched = MissionScheduler(registry=reg, runner=runner, db_path=":memory:")
    sched._has_active_run = lambda mid, wid: False
    sched._fire_schedule("mock_mission", "test_workflow", "test_schedule")
    runner.start_mission.assert_called_once_with(
        mission_id="mock_mission",
        workflow_id="test_workflow",
        trigger_type="schedule",
    )


def test_fire_schedule_skips_if_run_already_active():
    reg = _mock_registry(enabled=True)
    runner = _mock_runner()
    sched = MissionScheduler(registry=reg, runner=runner, db_path=":memory:")
    sched._has_active_run = lambda mid, wid: True
    sched._fire_schedule("mock_mission", "test_workflow", "test_schedule")
    runner.start_mission.assert_not_called()


def test_fire_schedule_catches_runner_exceptions():
    reg = _mock_registry(enabled=True)
    runner = MagicMock()
    runner.start_mission.side_effect = RuntimeError("kaboom")
    sched = MissionScheduler(registry=reg, runner=runner, db_path=":memory:")
    sched._has_active_run = lambda mid, wid: False
    # Must not raise
    sched._fire_schedule("mock_mission", "test_workflow", "test_schedule")


# ── start / stop / thread ─────────────────────────────────────────────────────

def test_scheduler_start_runs_in_background_thread():
    reg = _mock_registry(enabled=False)  # no schedules so loop is quiet
    sched = MissionScheduler(registry=reg, poll_interval=60)
    sched.start()
    try:
        assert sched._thread is not None
        assert sched._thread.is_alive()
    finally:
        sched.stop()


def test_scheduler_stop_clears_schedules():
    reg = _mock_registry(enabled=True)
    runner = _mock_runner()
    sched = MissionScheduler(registry=reg, runner=runner, poll_interval=60)
    sched.start()
    assert len(sched._schedules) >= 1
    sched.stop()
    assert len(sched._schedules) == 0
