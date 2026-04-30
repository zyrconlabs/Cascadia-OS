#!/usr/bin/env python3
"""
Reset (remove) all demo-seeded data.
Run from cascadia-os root:
    python3 scripts/reset_demo.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

DEMO_RUN_ID    = "run-demo-hvac-20260430"
DEMO_NAMESPACE = "demo"

def _load_config() -> dict:
    p = REPO_ROOT / "config.json"
    return json.loads(p.read_text()) if p.exists() else {}

CFG      = _load_config()
DB_PATH  = str(REPO_ROOT / CFG.get("database_path", "data/runtime/cascadia.db"))
VAULT_DB = str(REPO_ROOT / "data/runtime/cascadia_vault.db")
AUDIT_DB = str(REPO_ROOT / "data/runtime/audit.db")


def reset_main_db() -> None:
    if not Path(DB_PATH).exists():
        print("  — cascadia.db not found, skipping")
        return
    conn = sqlite3.connect(DB_PATH)
    for table in ("run_trace", "side_effects", "steps", "approvals", "runs"):
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (DEMO_RUN_ID,))
            if cur.rowcount:
                print(f"  ✓ Deleted {cur.rowcount} row(s) from {table}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def reset_audit_db() -> None:
    if not Path(AUDIT_DB).exists():
        print("  — audit.db not found, skipping")
        return
    conn = sqlite3.connect(AUDIT_DB)
    try:
        cur = conn.execute(
            "DELETE FROM audit_events WHERE run_id = ?", (DEMO_RUN_ID,)
        )
        if cur.rowcount:
            print(f"  ✓ Deleted {cur.rowcount} audit event(s)")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def reset_vault_db() -> None:
    if not Path(VAULT_DB).exists():
        print("  — cascadia_vault.db not found, skipping")
        return
    conn = sqlite3.connect(VAULT_DB)
    try:
        cur = conn.execute(
            "DELETE FROM vault WHERE namespace = ?", (DEMO_NAMESPACE,)
        )
        if cur.rowcount:
            print(f"  ✓ Deleted {cur.rowcount} vault entry/entries")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def main() -> None:
    print("\nCascadia OS — removing demo data\n")
    reset_main_db()
    reset_audit_db()
    reset_vault_db()
    print("\n  Done. Run seed_demo_data.py to re-seed.\n")


if __name__ == "__main__":
    main()
