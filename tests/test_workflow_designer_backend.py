"""
tests/test_workflow_designer_backend.py
Tests for Workflow Designer backend — Task 15.
"""
import pytest
import sqlite3
from pathlib import Path


# ----- FEATURE_TIERS tests -----

def test_feature_tiers_workflow_designer_is_pro():
    from cascadia.licensing.tier_validator import FEATURE_TIERS
    assert FEATURE_TIERS['workflow_designer'] == 'pro'


def test_feature_tiers_workflow_viewer_is_lite():
    from cascadia.licensing.tier_validator import FEATURE_TIERS
    assert FEATURE_TIERS['workflow_viewer'] == 'lite'


def test_can_access_workflow_designer_lite_false():
    from cascadia.licensing.tier_validator import TierValidator
    v = TierValidator('secret', tier='lite')
    assert v.can_access('workflow_designer') is False


def test_can_access_workflow_designer_pro_true():
    from cascadia.licensing.tier_validator import TierValidator
    v = TierValidator('secret', tier='pro')
    assert v.can_access('workflow_designer') is True


def test_can_access_workflow_designer_enterprise_true():
    from cascadia.licensing.tier_validator import TierValidator
    v = TierValidator('secret', tier='enterprise')
    assert v.can_access('workflow_designer') is True


def test_can_access_workflow_viewer_lite_true():
    from cascadia.licensing.tier_validator import TierValidator
    v = TierValidator('secret', tier='lite')
    assert v.can_access('workflow_viewer') is True


# ----- WorkflowStore tests -----

@pytest.fixture
def wf_db(tmp_path):
    """Provide a WorkflowStore backed by a temp SQLite DB with the right schema."""
    from cascadia.automation.stitch import WorkflowStore
    db_path = str(tmp_path / 'test_wf.db')
    # Create the workflow_definitions table
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS workflow_definitions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                nodes TEXT NOT NULL DEFAULT '[]',
                edges TEXT NOT NULL DEFAULT '[]',
                viewport TEXT NOT NULL DEFAULT '{}',
                created_by TEXT DEFAULT 'user',
                is_template INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
    return WorkflowStore(db_path)


def test_workflow_store_save_and_get(wf_db):
    result = wf_db.save('wf_001', 'Test Workflow', [{'id': 'n1'}], [])
    assert result is not None
    fetched = wf_db.get('wf_001')
    assert fetched is not None
    assert fetched['name'] == 'Test Workflow'
    assert fetched['nodes'] == [{'id': 'n1'}]


def test_workflow_store_list_all(wf_db):
    wf_db.save('wf_a', 'Alpha', [], [])
    wf_db.save('wf_b', 'Beta', [], [])
    all_wf = wf_db.list_all()
    ids = [w['id'] for w in all_wf]
    assert 'wf_a' in ids
    assert 'wf_b' in ids


def test_workflow_store_delete_soft(wf_db):
    wf_db.save('wf_del', 'To Delete', [], [])
    deleted = wf_db.delete('wf_del')
    assert deleted is True
    all_wf = wf_db.list_all()
    ids = [w['id'] for w in all_wf]
    assert 'wf_del' not in ids


def test_workflow_store_get_nonexistent(wf_db):
    result = wf_db.get('nonexistent_id')
    assert result is None


def test_workflow_store_list_templates(wf_db):
    # Manually insert a template
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(wf_db._db) as conn:
        conn.execute(
            "INSERT INTO workflow_definitions (id, name, nodes, edges, viewport, is_template, created_at, updated_at) "
            "VALUES ('tmpl_1', 'My Template', '[]', '[]', '{}', 1, ?, ?)",
            (now, now)
        )
    templates = wf_db.list_templates()
    assert len(templates) == 1
    assert templates[0]['id'] == 'tmpl_1'
    assert templates[0]['is_template'] == 1
