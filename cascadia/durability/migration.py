# MATURITY: PRODUCTION — Idempotent. Handles legacy DB upgrades.
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f'PRAGMA table_info({table})'))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def migrate(conn: sqlite3.Connection) -> None:
    """Owns idempotent schema migration. Does not own runtime query behavior."""
    conn.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
    if not _table_exists(conn, 'runs'):
        conn.execute(
            """
            CREATE TABLE runs (
              run_id TEXT PRIMARY KEY,
              operator_id TEXT,
              tenant_id TEXT,
              goal TEXT,
              current_step TEXT,
              input_snapshot TEXT,
              state_snapshot TEXT,
              retry_count INTEGER DEFAULT 0,
              last_checkpoint TEXT,
              process_state TEXT,
              run_state TEXT,
              blocked_reason TEXT,
              blocking_entity TEXT,
              dependency_request TEXT,
              created_at TEXT,
              updated_at TEXT
            )
            """
        )
    else:
        for name, kind in {
            'process_state': 'TEXT',
            'run_state': 'TEXT',
            'blocked_reason': 'TEXT',
            'blocking_entity': 'TEXT',
            'dependency_request': 'TEXT',
            'lead_received_at': 'TEXT',
            'outcome': 'TEXT',
            'outcome_recorded_at': 'TEXT',
        }.items():
            if not _column_exists(conn, 'runs', name):
                conn.execute(f'ALTER TABLE runs ADD COLUMN {name} {kind}')
        if _column_exists(conn, 'runs', 'resume_status') and _column_exists(conn, 'runs', 'run_state'):
            conn.execute("UPDATE runs SET run_state = COALESCE(run_state, resume_status, 'pending')")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS steps (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT NOT NULL,
          step_name TEXT,
          step_index INTEGER,
          started_at TEXT,
          completed_at TEXT,
          input_state TEXT,
          output_state TEXT,
          failure_reason TEXT,
          FOREIGN KEY(run_id) REFERENCES runs(run_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS side_effects (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT NOT NULL,
          step_index INTEGER,
          effect_type TEXT,
          effect_key TEXT UNIQUE,
          status TEXT,
          target TEXT,
          payload TEXT,
          created_at TEXT,
          committed_at TEXT,
          FOREIGN KEY(run_id) REFERENCES runs(run_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS approvals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT NOT NULL,
          step_index INTEGER,
          action_key TEXT,
          decision TEXT,
          actor TEXT,
          reason TEXT,
          created_at TEXT,
          decided_at TEXT,
          FOREIGN KEY(run_id) REFERENCES runs(run_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_trace (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT NOT NULL,
          event_type TEXT,
          step_index INTEGER,
          payload TEXT,
          created_at TEXT,
          FOREIGN KEY(run_id) REFERENCES runs(run_id)
        )
        """
    )
    # Sprint v2 approval extensions — safe idempotent migrations
    for col, kind in {
        'risk_level':      'TEXT DEFAULT "MEDIUM"',
        'edited_content':  'TEXT',
        'edit_summary':    'TEXT',
    }.items():
        if not _column_exists(conn, 'approvals', col):
            try:
                conn.execute(f'ALTER TABLE approvals ADD COLUMN {col} {kind}')
            except Exception:
                pass

    _add_workflow_definitions_table(conn)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


def _add_workflow_definitions_table(conn: sqlite3.Connection) -> None:
    conn.execute('''
        CREATE TABLE IF NOT EXISTS workflow_definitions (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            nodes       TEXT NOT NULL DEFAULT '[]',
            edges       TEXT NOT NULL DEFAULT '[]',
            viewport    TEXT NOT NULL DEFAULT '{}',
            created_by  TEXT DEFAULT 'user',
            is_template INTEGER NOT NULL DEFAULT 0,
            deleted_at  TEXT DEFAULT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    ''')
