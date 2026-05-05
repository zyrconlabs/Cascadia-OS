"""
daily_backup.py — Cascadia OS
CLI entrypoint for database backup. Delegates to BackupManager.
Can be run directly (`python3 -m cascadia.backup.daily_backup`)
or called as a function from STITCH scheduler.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger('daily_backup')


def backup_database(db_path: str | None = None,
                    backup_dir: str | None = None,
                    retention_days: int = 7) -> bool:
    """
    Back up the Cascadia SQLite database with gzip compression.
    Keeps the last `retention_days` backups and deletes older ones.
    Returns True on success, False on failure.
    """
    if db_path is None or backup_dir is None:
        try:
            from cascadia.shared.config import load_config
            config = load_config()
            if db_path is None:
                db_path = config.get('database_path', './data/runtime/cascadia.db')
            if backup_dir is None:
                backup_dir = config.get('backup_dir', './data/backups')
        except Exception:
            db_path = db_path or './data/runtime/cascadia.db'
            backup_dir = backup_dir or './data/backups'

    db = Path(db_path)
    if not db.exists():
        log.warning('[Backup] Database not found: %s', db)
        return False

    try:
        from cascadia.durability.backup import BackupManager
        mgr = BackupManager(str(db), backup_dir, retention_days)
        backup_path = mgr.create_backup()
        mgr.purge_old()
        size_kb = backup_path.stat().st_size // 1024
        log.info('[Backup] Saved %dKB → %s', size_kb, backup_path)
        print(f'[Backup] Saved {size_kb}KB → {backup_path}')
        return True
    except Exception as exc:
        log.error('[Backup] Failed: %s', exc)
        print(f'[Backup] Failed: {exc}')
        return False


if __name__ == '__main__':
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format='%(asctime)s %(message)s')
    ok = backup_database()
    raise SystemExit(0 if ok else 1)
