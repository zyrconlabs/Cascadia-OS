"""Mission database migration runner.

CLI: python -m cascadia.missions.migrate
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger(__name__)

MISSION_TABLES = {
    'organizations', 'missions', 'mission_runs', 'mission_run_steps',
    'leads', 'lead_enrichments', 'quotes', 'purchase_orders', 'invoices',
    'campaigns', 'campaign_items', 'review_requests', 'tasks', 'blockers', 'briefs',
    'mission_items',
}

_SCHEMA_PATH = Path(__file__).parent / 'schema.sql'

# ALTER TABLE statements parsed out of schema.sql and handled in Python
# (IF NOT EXISTS in ALTER TABLE is not portable across SQLite builds)
_ALTER_COLUMNS = [
    ('approvals',      'mission_id',     'TEXT'),
    ('approvals',      'mission_run_id', 'TEXT'),
    ('mission_runs',   'workflow_id',    'TEXT'),
    ('mission_runs',   'trigger_type',   'TEXT'),
    ('mission_runs',   'parent_run_id',  'TEXT'),
]

# Indexes on approvals columns — created after ALTERs, need the columns to exist first
_APPROVALS_INDEX_RE = re.compile(r'ON\s+approvals\s*\(', re.IGNORECASE)


def _resolve_db_path(connection_string: str | None) -> str:
    if connection_string:
        return connection_string
    config_path = Path(__file__).parent.parent.parent / 'config.json'
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding='utf-8'))
        if 'database_path' in cfg:
            return cfg['database_path']
        if isinstance(cfg.get('database'), dict):
            return cfg['database'].get('url', './data/runtime/cascadia.db')
    return './data/runtime/cascadia.db'


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def _existing_indexes(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f'PRAGMA table_info({table})'))


def _approvals_columns(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, 'approvals'):
        return set()
    return {r[1] for r in conn.execute('PRAGMA table_info(approvals)').fetchall()}


def _strip_sql_comments(stmt: str) -> str:
    """Remove leading/trailing comment lines from a SQL fragment."""
    lines = [ln for ln in stmt.splitlines() if not ln.strip().startswith('--')]
    return '\n'.join(lines).strip()


def _raw_statements(sql: str) -> list[str]:
    """Split SQL on ';' and return non-empty, non-pure-comment fragments."""
    parts = []
    for fragment in sql.split(';'):
        stripped = _strip_sql_comments(fragment)
        if stripped:
            parts.append(stripped)
    return parts


def run_migration(connection_string: str | None = None) -> dict:
    """Run the missions schema migration against the configured database.

    Safe to call multiple times — fully idempotent.
    Returns a summary dict describing what changed.
    """
    db_path = _resolve_db_path(connection_string)
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        return _run(conn)
    finally:
        conn.close()


def _run(conn: sqlite3.Connection) -> dict:
    tables_before = _existing_tables(conn)
    indexes_before = _existing_indexes(conn)
    cols_before = _approvals_columns(conn)
    all_mission_tables_exist = MISSION_TABLES.issubset(tables_before)

    sql = _SCHEMA_PATH.read_text(encoding='utf-8')

    # Separate ALTER TABLE and approvals-index statements from the rest.
    # ALTER TABLE is handled via Python column-existence checks (portable across
    # SQLite builds that may not support ADD COLUMN IF NOT EXISTS).
    alter_fragments: list[str] = []
    approvals_index_fragments: list[str] = []
    regular_fragments: list[str] = []

    for frag in _raw_statements(sql):
        upper = frag.upper().lstrip()
        if upper.startswith('ALTER TABLE'):
            alter_fragments.append(frag)
        elif upper.startswith('CREATE') and _APPROVALS_INDEX_RE.search(frag):
            approvals_index_fragments.append(frag)
        else:
            regular_fragments.append(frag)

    # Run CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, INSERT ... ON CONFLICT
    # via executescript (issues implicit COMMIT, then autocommit per statement).
    if regular_fragments:
        conn.executescript(';\n'.join(regular_fragments) + ';')

    # ALTER TABLE: check column existence first (portable idempotence)
    for table, col, col_type in _ALTER_COLUMNS:
        if not _table_exists(conn, table):
            log.debug('skipping ALTER: table %s does not exist', table)
            continue
        if _column_exists(conn, table, col):
            log.debug('skipping ALTER: %s.%s already exists', table, col)
            continue
        try:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}')
        except sqlite3.OperationalError as exc:
            if 'duplicate column name' in str(exc).lower():
                log.debug('column already exists (idempotent): %s', exc)
            else:
                raise

    # Approvals indexes: create only if the columns now exist
    for frag in approvals_index_fragments:
        if _table_exists(conn, 'approvals'):
            try:
                conn.execute(frag)
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if any(p in msg for p in ('already exists', 'no such column', 'no such table')):
                    log.debug('skipping approvals index (expected): %s', exc)
                else:
                    raise

    # --- snapshot after ---
    tables_after = _existing_tables(conn)
    indexes_after = _existing_indexes(conn)
    cols_after = _approvals_columns(conn)

    org_row = conn.execute(
        "SELECT id FROM organizations WHERE id = ?",
        ('00000000-0000-0000-0000-000000000001',),
    ).fetchone()

    result = {
        'tables_created': len(tables_after - tables_before),
        'columns_added': len(cols_after - cols_before),
        'indexes_created': len(indexes_after - indexes_before),
        'default_org_inserted': org_row is not None,
        'already_migrated': all_mission_tables_exist,
    }
    log.info('missions migration complete: %s', result)
    return result


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    result = run_migration()
    print(json.dumps(result, indent=2))
    sys.exit(0)
