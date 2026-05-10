"""
SQLite schemas for Memory Governor durable tables.

Schemas defined but no migration runs yet. Phase 3 wires
these into VAULT or a dedicated DB.
"""

OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL,
    action_type         TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    payload_hash        TEXT NOT NULL,
    idempotency_key     TEXT NOT NULL UNIQUE,
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          REAL NOT NULL,
    executed_at         REAL,
    external_result_id  TEXT,
    error_message       TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_status
    ON outbox(status);
CREATE INDEX IF NOT EXISTS idx_outbox_run_id
    ON outbox(run_id);
"""

MISSION_SUMMARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS mission_summary (
    mission_id      TEXT PRIMARY KEY,
    started_at      TIMESTAMP NOT NULL,
    completed_at    TIMESTAMP,
    summary_json    TEXT NOT NULL,
    compacted       INTEGER NOT NULL DEFAULT 0
);
"""


def all_schemas() -> list:
    return [OUTBOX_SCHEMA, MISSION_SUMMARY_SCHEMA]
