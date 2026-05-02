from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cascadia.missions import constants
from cascadia.missions.migrate import MISSION_TABLES, run_migration


def _tables(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    finally:
        conn.close()


def _columns(db_path: str, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    finally:
        conn.close()


class MissionsSchemaTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / 'missions_test.db')

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    # ──────────────────────────────────────────────────────────
    # test_migration_runs_clean
    # ──────────────────────────────────────────────────────────
    def test_migration_runs_clean(self) -> None:
        result = run_migration(self.db_path)
        tables = _tables(self.db_path)
        # All 15 mission tables must exist
        for expected in MISSION_TABLES:
            self.assertIn(expected, tables, f"Missing table: {expected}")
        self.assertEqual(len(MISSION_TABLES), 16)
        # Migration reported it created tables
        self.assertGreater(result['tables_created'], 0)

    # ──────────────────────────────────────────────────────────
    # test_migration_idempotent
    # ──────────────────────────────────────────────────────────
    def test_migration_idempotent(self) -> None:
        run_migration(self.db_path)
        # Second run must not raise and must report already_migrated
        result2 = run_migration(self.db_path)
        self.assertTrue(result2['already_migrated'])
        self.assertEqual(result2['tables_created'], 0)

    # ──────────────────────────────────────────────────────────
    # test_default_organization_exists
    # ──────────────────────────────────────────────────────────
    def test_default_organization_exists(self) -> None:
        run_migration(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute('SELECT * FROM organizations').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['id'], constants.DEFAULT_ORGANIZATION_ID)
        finally:
            conn.close()

    # ──────────────────────────────────────────────────────────
    # test_approvals_table_extended
    # ──────────────────────────────────────────────────────────
    def test_approvals_table_extended(self) -> None:
        # Pre-create a minimal approvals table (simulating existing cascadia DB)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                '''CREATE TABLE approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    decision TEXT
                )'''
            )
            conn.commit()
        finally:
            conn.close()

        run_migration(self.db_path)
        cols = _columns(self.db_path, 'approvals')
        self.assertIn('mission_id', cols)
        self.assertIn('mission_run_id', cols)

    # ──────────────────────────────────────────────────────────
    # test_approvals_columns_are_nullable
    # ──────────────────────────────────────────────────────────
    def test_approvals_columns_are_nullable(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                '''CREATE TABLE approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    decision TEXT
                )'''
            )
            conn.commit()
        finally:
            conn.close()

        run_migration(self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            # Inserting with NULL mission_id and mission_run_id must succeed
            conn.execute(
                "INSERT INTO approvals (run_id, decision, mission_id, mission_run_id)"
                " VALUES ('run-1', 'approved', NULL, NULL)"
            )
            conn.commit()
            row = conn.execute('SELECT * FROM approvals WHERE run_id=?', ('run-1',)).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    # ──────────────────────────────────────────────────────────
    # test_constants_match_schema
    # ──────────────────────────────────────────────────────────
    def test_constants_match_schema(self) -> None:
        run_migration(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            for status in constants.VALID_MISSION_RUN_STATUSES:
                # Each status must be insertable without error
                conn.execute(
                    "INSERT INTO mission_runs (id, mission_id, status)"
                    " VALUES (?, 'dummy-mission', ?)",
                    (f'test-run-{status}', status),
                )
            conn.commit()
            rows = conn.execute('SELECT DISTINCT status FROM mission_runs').fetchall()
            stored = {r['status'] for r in rows}
            self.assertEqual(stored, constants.VALID_MISSION_RUN_STATUSES)
        finally:
            conn.close()

    # ──────────────────────────────────────────────────────────
    # test_existing_approvals_unaffected
    # ──────────────────────────────────────────────────────────
    def test_existing_approvals_unaffected(self) -> None:
        # Pre-seed approvals table and a row before migration
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                '''CREATE TABLE approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    decision TEXT
                )'''
            )
            conn.execute("INSERT INTO approvals (run_id, decision) VALUES ('pre-run', 'approved')")
            conn.commit()
        finally:
            conn.close()

        run_migration(self.db_path)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM approvals WHERE run_id = 'pre-run'"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['decision'], 'approved')
            # The pre-existing row has NULL for the new columns
            self.assertIsNone(row['mission_id'])
            self.assertIsNone(row['mission_run_id'])
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
