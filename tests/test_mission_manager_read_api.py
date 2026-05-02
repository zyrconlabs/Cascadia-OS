"""Tests for Mission Manager read API — port 6207."""
from __future__ import annotations

from pathlib import Path

import cascadia.missions.manager as manager
from cascadia.missions.registry import MissionRegistry

FIXTURE_DIR = str(Path(__file__).parent / "fixtures" / "missions")
FIXTURE_ID = "test_growth_desk"


def _set_registry(packages_root: str = FIXTURE_DIR) -> None:
    manager._registry = MissionRegistry(packages_root=packages_root)


def _clear_registry() -> None:
    manager._registry = None


# ── /healthz ──────────────────────────────────────────────────────────────────

def test_healthz_returns_ok():
    code, body = manager.handle_healthz({})
    assert code == 200
    assert body["status"] == "ok"
    assert body["service"] == "mission_manager"
    assert body["port"] == 6207


# ── /api/missions/catalog ─────────────────────────────────────────────────────

def test_catalog_returns_discovered_missions():
    _set_registry()
    try:
        code, body = manager.handle_catalog({})
        assert code == 200
        assert isinstance(body["missions"], list)
        assert len(body["missions"]) >= 1
        ids = [m["id"] for m in body["missions"]]
        assert FIXTURE_ID in ids
    finally:
        _clear_registry()


def test_catalog_installed_flag_is_false_initially():
    _set_registry()
    try:
        code, body = manager.handle_catalog({})
        assert code == 200
        for m in body["missions"]:
            assert m["installed"] is False
    finally:
        _clear_registry()


def test_catalog_missing_root_returns_empty_not_crash():
    manager._registry = MissionRegistry(packages_root="/nonexistent/path/xyz")
    try:
        code, body = manager.handle_catalog({})
        assert code == 200
        assert body == {"missions": []}
    finally:
        _clear_registry()


# ── /api/missions/installed ───────────────────────────────────────────────────

def test_installed_returns_empty_initially():
    _set_registry()
    try:
        code, body = manager.handle_installed({})
        assert code == 200
        assert body["missions"] == []
    finally:
        _clear_registry()


# ── /api/missions/<id> ────────────────────────────────────────────────────────

def test_mission_detail_returns_manifest_summary():
    _set_registry()
    try:
        code, body = manager.handle_mission_detail({"mission_id": FIXTURE_ID})
        assert code == 200
        assert body["id"] == FIXTURE_ID
        assert "name" in body
        assert "version" in body
        assert "tier_required" in body
        assert "installed" in body
        assert "status" in body
    finally:
        _clear_registry()


def test_mission_detail_unknown_returns_404():
    _set_registry()
    try:
        code, body = manager.handle_mission_detail({"mission_id": "does_not_exist"})
        assert code == 404
        assert body["error"] == "mission_not_found"
        assert body["mission_id"] == "does_not_exist"
    finally:
        _clear_registry()


# ── /api/missions/<id>/status ─────────────────────────────────────────────────

def test_mission_status_returns_static_status():
    _set_registry()
    try:
        code, body = manager.handle_status({"mission_id": FIXTURE_ID})
        assert code == 200
        assert body["mission_id"] == FIXTURE_ID
        assert "status" in body
        assert "installed" in body
        assert isinstance(body["pending_approvals"], int)
        assert isinstance(body["active_runs"], int)
        assert isinstance(body["failed_runs_24h"], int)
        assert isinstance(body["required_operators"], dict)
        assert isinstance(body["required_connectors"], dict)
        assert "tier_required" in body
    finally:
        _clear_registry()


def test_mission_status_unknown_returns_404():
    _set_registry()
    try:
        code, body = manager.handle_status({"mission_id": "does_not_exist"})
        assert code == 404
        assert body["error"] == "mission_not_found"
    finally:
        _clear_registry()


# ── /api/missions/<id>/mobile_schema ─────────────────────────────────────────

def test_mobile_schema_returns_json_content():
    _set_registry()
    try:
        code, body = manager.handle_mobile_schema({"mission_id": FIXTURE_ID})
        assert code == 200
        assert isinstance(body, dict)
    finally:
        _clear_registry()


def test_mobile_schema_unknown_mission_returns_404():
    _set_registry()
    try:
        code, body = manager.handle_mobile_schema({"mission_id": "does_not_exist"})
        assert code == 404
        assert body["error"] == "mobile_schema_not_found"
        assert body["mission_id"] == "does_not_exist"
    finally:
        _clear_registry()


# ── /api/missions/<id>/prism_schema ──────────────────────────────────────────

def test_prism_schema_returns_json_content():
    _set_registry()
    try:
        code, body = manager.handle_prism_schema({"mission_id": FIXTURE_ID})
        assert code == 200
        assert isinstance(body, dict)
    finally:
        _clear_registry()


def test_prism_schema_unknown_mission_returns_404():
    _set_registry()
    try:
        code, body = manager.handle_prism_schema({"mission_id": "does_not_exist"})
        assert code == 404
        assert body["error"] == "prism_schema_not_found"
        assert body["mission_id"] == "does_not_exist"
    finally:
        _clear_registry()


# ── /api/missions/<id>/health ─────────────────────────────────────────────────

def test_health_returns_checks():
    _set_registry()
    try:
        code, body = manager.handle_health({"mission_id": FIXTURE_ID})
        assert code == 200
        assert "checks" in body
        assert isinstance(body["checks"], list)
        assert len(body["checks"]) > 0
        check_ids = {c["id"] for c in body["checks"]}
        assert "manifest_valid" in check_ids
        assert "installed" in check_ids
    finally:
        _clear_registry()


def test_health_score_is_numeric():
    _set_registry()
    try:
        code, body = manager.handle_health({"mission_id": FIXTURE_ID})
        assert code == 200
        assert isinstance(body["score"], (int, float))
        assert body["score"] >= 0
        assert "status" in body
    finally:
        _clear_registry()


def test_health_unknown_mission_returns_404():
    _set_registry()
    try:
        code, body = manager.handle_health({"mission_id": "does_not_exist"})
        assert code == 404
        assert body["error"] == "mission_not_found"
    finally:
        _clear_registry()


# ── /api/missions/<id>/runs ───────────────────────────────────────────────────

def test_runs_returns_empty_list_initially():
    _set_registry()
    try:
        code, body = manager.handle_runs({"mission_id": FIXTURE_ID})
        assert code == 200
        assert body["mission_id"] == FIXTURE_ID
        assert body["runs"] == []
    finally:
        _clear_registry()


def test_runs_unknown_mission_returns_404():
    _set_registry()
    try:
        code, body = manager.handle_runs({"mission_id": "does_not_exist"})
        assert code == 404
        assert body["error"] == "mission_not_found"
    finally:
        _clear_registry()
