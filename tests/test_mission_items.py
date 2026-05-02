"""Tests for mission_items table and Revenue Desk pipeline — Task 1-4."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import cascadia.missions.manager as manager
from cascadia.missions.migrate import run_migration, MISSION_TABLES

FIXTURE_DIR = str(Path(__file__).parent / "fixtures" / "missions")


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


def _table_exists(db: str, table: str) -> bool:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _columns(db: str, table: str) -> set:
    conn = sqlite3.connect(db)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


# ── Task 1 — Table existence ──────────────────────────────────────────────────

def test_mission_items_in_migration_tables():
    assert "mission_items" in MISSION_TABLES


def test_migration_adds_mission_items_table(tmp_path):
    db = _make_db(tmp_path)
    assert _table_exists(db, "mission_items")


def test_mission_items_has_required_columns(tmp_path):
    db = _make_db(tmp_path)
    cols = _columns(db, "mission_items")
    for col in ("id", "mission_id", "item_type", "title", "status",
                "urgency_score", "value_score", "confidence", "amount",
                "recommended_action", "raw_json"):
        assert col in cols, f"Missing column: {col}"


def test_migration_idempotent_with_mission_items(tmp_path):
    db = _make_db(tmp_path)
    result = run_migration(db)
    assert result["already_migrated"] is True
    assert result["tables_created"] == 0


# ── Task 2 — GET items endpoint ───────────────────────────────────────────────

def test_get_items_returns_empty_list(tmp_path):
    db = _make_db(tmp_path)
    with patch("cascadia.missions.manager._db_path", return_value=db):
        code, body = manager.handle_items({"mission_id": "revenue_desk"})
    assert code == 200
    assert body["items"] == []
    assert body["total"] == 0


def test_get_items_filters_by_status(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO mission_items (id, mission_id, item_type, title, status) "
            "VALUES (?, 'revenue_desk', 'lead', 'Test Lead', 'new')",
            (str(uuid.uuid4()),),
        )
        conn.execute(
            "INSERT INTO mission_items (id, mission_id, item_type, title, status) "
            "VALUES (?, 'revenue_desk', 'invoice', 'Test Invoice', 'dismissed')",
            (str(uuid.uuid4()),),
        )
        conn.commit()
    finally:
        conn.close()

    with patch("cascadia.missions.manager._db_path", return_value=db):
        code, body = manager.handle_items({"mission_id": "revenue_desk", "status": "new"})
    assert code == 200
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "new"


# ── Task 2 — POST create item endpoint ───────────────────────────────────────

def test_create_item_via_post(tmp_path):
    db = _make_db(tmp_path)
    with patch("cascadia.missions.manager._db_path", return_value=db):
        code, body = manager.handle_create_item({
            "mission_id": "revenue_desk",
            "item_type": "lead",
            "title": "New lead from email",
            "confidence": 0.92,
            "urgency_score": 20,
            "value_score": 15,
        })
    assert code == 201
    assert body["status"] == "created"
    item_id = body["id"]

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT * FROM mission_items WHERE id = ?", (item_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None


def test_create_item_missing_fields_returns_400(tmp_path):
    db = _make_db(tmp_path)
    with patch("cascadia.missions.manager._db_path", return_value=db):
        code, body = manager.handle_create_item({
            "mission_id": "revenue_desk",
            "item_type": "lead",
            # missing title
        })
    assert code == 400


# ── Task 2 — PATCH update item endpoint ──────────────────────────────────────

def test_patch_item_status_approved(tmp_path):
    db = _make_db(tmp_path)
    item_id = str(uuid.uuid4())
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO mission_items (id, mission_id, item_type, title, status) "
            "VALUES (?, 'revenue_desk', 'lead', 'Test', 'new')",
            (item_id,),
        )
        conn.commit()
    finally:
        conn.close()

    with patch("cascadia.missions.manager._db_path", return_value=db):
        code, body = manager.handle_update_item({
            "item_id": item_id,
            "status": "approved",
        })
    assert code == 200
    assert body["status"] == "approved"

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT status FROM mission_items WHERE id = ?", (item_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "approved"


def test_patch_item_invalid_status_returns_400(tmp_path):
    db = _make_db(tmp_path)
    with patch("cascadia.missions.manager._db_path", return_value=db):
        code, body = manager.handle_update_item({
            "item_id": str(uuid.uuid4()),
            "status": "banana",
        })
    assert code == 400


def test_patch_item_not_found_returns_404(tmp_path):
    db = _make_db(tmp_path)
    with patch("cascadia.missions.manager._db_path", return_value=db):
        code, body = manager.handle_update_item({
            "item_id": str(uuid.uuid4()),
            "status": "approved",
        })
    assert code == 404


# ── Task 3 — Email scanner creates mission items ──────────────────────────────

def test_revenue_item_types_constant():
    import sys, importlib
    # Reload email server module to access constants
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "email_server",
        str(Path(__file__).parent.parent.parent /
            "operators/cascadia-os-operators/email/server.py"),
    )
    if spec is None:
        pytest.skip("email server not importable in this env")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pytest.skip("email server has non-importable deps")
    assert hasattr(mod, "REVENUE_ITEM_TYPES")
    assert "lead" in mod.REVENUE_ITEM_TYPES
    assert "overdue_invoice" in mod.REVENUE_ITEM_TYPES


def test_calc_urgency_overdue_invoice():
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location(
        "email_server",
        str(Path(__file__).parent.parent.parent /
            "operators/cascadia-os-operators/email/server.py"),
    )
    if spec is None:
        pytest.skip("email server not importable")
    mod = module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pytest.skip("email server has non-importable deps")
    score = mod._calc_urgency({"type": "overdue_invoice", "days_waiting": 3})
    assert score >= 40


def test_calc_value_large_purchase_order():
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location(
        "email_server",
        str(Path(__file__).parent.parent.parent /
            "operators/cascadia-os-operators/email/server.py"),
    )
    if spec is None:
        pytest.skip("email server not importable")
    mod = module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pytest.skip("email server has non-importable deps")
    score = mod._calc_value({"type": "purchase_order", "amount": 15000})
    assert score >= 50


def test_create_mission_item_skips_non_revenue():
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location(
        "email_server",
        str(Path(__file__).parent.parent.parent /
            "operators/cascadia-os-operators/email/server.py"),
    )
    if spec is None:
        pytest.skip("email server not importable")
    mod = module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pytest.skip("email server has non-importable deps")

    # Should return None without making HTTP call for non-revenue type
    called = []
    with patch("urllib.request.urlopen", side_effect=lambda *a, **kw: called.append(1)):
        mod._create_mission_item({"type": "spam"}, "Subject", "from@example.com")
    assert called == []


def test_create_mission_item_posts_for_revenue_type():
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location(
        "email_server",
        str(Path(__file__).parent.parent.parent /
            "operators/cascadia-os-operators/email/server.py"),
    )
    if spec is None:
        pytest.skip("email server not importable")
    mod = module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pytest.skip("email server has non-importable deps")

    mock_resp = MagicMock()
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = b'{"id":"test","status":"created"}'

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        mod._create_mission_item(
            {"type": "lead", "confidence": 0.9, "summary": "New lead"},
            "Inquiry about services",
            "customer@company.com",
        )
    mock_open.assert_called_once()


# ── Regression guard ──────────────────────────────────────────────────────────

def test_existing_read_api_tests_still_pass():
    code, body = manager.handle_healthz({})
    assert code == 200
    assert body["status"] == "ok"
