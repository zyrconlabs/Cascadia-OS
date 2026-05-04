"""
first_run.py — First-run detection and setup for Cascadia OS.
Runs once on a fresh install. Safe to call on every startup — no-ops if already complete.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SENTINEL = Path('data/runtime/.first_run_complete')


def is_first_run() -> bool:
    return not _SENTINEL.exists()


def mark_complete() -> None:
    _SENTINEL.parent.mkdir(parents=True, exist_ok=True)
    _SENTINEL.touch()


def run_first_time_setup(config: dict) -> None:
    """Auto-seed demo data on first run. Idempotent — seed script checks run_id before inserting."""
    if not is_first_run():
        return
    db_path = config.get('database_path', './data/runtime/cascadia.db')
    seed_script = Path('scripts/seed_demo_data.py')
    if not seed_script.exists():
        return
    try:
        subprocess.run(
            [sys.executable, str(seed_script), '--db', db_path],
            timeout=30,
            check=False,
        )
    except Exception:
        pass
    mark_complete()
