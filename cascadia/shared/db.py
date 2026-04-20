# MATURITY: PRODUCTION — SQLite connection and migration bootstrap.
from __future__ import annotations

import sqlite3
from pathlib import Path

from cascadia.durability.migration import migrate


def connect(database_path: str) -> sqlite3.Connection:
    """Owns opening SQLite connections. Does not own business-level queries."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_database(database_path: str) -> None:
    """Owns migration bootstrapping. Does not own runtime data access."""
    conn = connect(database_path)
    try:
        migrate(conn)
        conn.commit()
    finally:
        conn.close()
