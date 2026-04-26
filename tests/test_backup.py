"""
tests/test_backup.py
Tests for BackupManager — Task 21.
"""
import gzip
import sqlite3
import pytest
from pathlib import Path


@pytest.fixture
def test_db(tmp_path):
    """Create a minimal SQLite database for testing."""
    db_path = str(tmp_path / 'test.db')
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)')
        conn.execute("INSERT INTO test VALUES (1, 'hello')")
    return db_path


@pytest.fixture
def backup_dir(tmp_path):
    return str(tmp_path / 'backups')


@pytest.fixture
def mgr(test_db, backup_dir):
    from cascadia.durability.backup import BackupManager
    return BackupManager(test_db, backup_dir, retention_days=30)


def test_create_backup_returns_path(mgr):
    path = mgr.create_backup()
    assert path.exists()
    assert path.suffix == '.gz'


def test_list_backups_after_create(mgr):
    mgr.create_backup()
    backups = mgr.list_backups()
    assert len(backups) == 1
    assert 'cascadia_' in backups[0]['name']
    assert backups[0]['size_kb'] > 0


def test_purge_old_removes_expired(tmp_path):
    """purge_old() removes files older than retention_days."""
    from cascadia.durability.backup import BackupManager
    import time

    db_path = str(tmp_path / 'purge_test.db')
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE t (id INTEGER PRIMARY KEY)')

    backup_dir = str(tmp_path / 'bkp')
    mgr = BackupManager(db_path, backup_dir, retention_days=0)

    path = mgr.create_backup()
    assert path.exists()

    # With retention_days=0, all backups should be purged
    deleted = mgr.purge_old()
    assert deleted >= 1
    assert not path.exists()


def test_verify_latest_valid(mgr):
    mgr.create_backup()
    assert mgr.verify_latest() is True


def test_verify_latest_no_backup(backup_dir):
    from cascadia.durability.backup import BackupManager
    mgr = BackupManager('/nonexistent.db', backup_dir, retention_days=30)
    assert mgr.verify_latest() is False


def test_list_backups_empty_initially(backup_dir, test_db):
    from cascadia.durability.backup import BackupManager
    mgr = BackupManager(test_db, backup_dir, retention_days=30)
    assert mgr.list_backups() == []


def test_backup_is_valid_gzip(mgr):
    path = mgr.create_backup()
    with gzip.open(str(path), 'rb') as f:
        header = f.read(16)
    assert header[:15] == b'SQLite format 3'
