"""
backup.py — Cascadia OS v0.47
BackupManager: SQLite database backup with gzip compression and integrity verification.
Owns: backup creation, retention, and verification. Does not own restoration.
"""
from __future__ import annotations

import gzip
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')


class BackupManager:
    """
    Owns SQLite database backup lifecycle.
    Does not own database queries, schema, or restoration.
    """

    SQLITE_MAGIC = b'SQLite format 3'

    def __init__(self, db_path: str, backup_dir: str, retention_days: int = 30) -> None:
        self._db_path = Path(db_path)
        self._backup_dir = Path(backup_dir)
        self._retention_days = retention_days
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self) -> Path:
        """
        Create a gzip-compressed backup of the SQLite database.
        Uses sqlite3 backup API for consistency.
        Returns the path to the backup file.
        """
        if not self._db_path.exists():
            raise FileNotFoundError(f'Database not found: {self._db_path}')

        backup_name = f'cascadia_{_now_str()}.db.gz'
        backup_path = self._backup_dir / backup_name

        # Use sqlite3 backup API to get consistent snapshot
        with sqlite3.connect(str(self._db_path)) as src_conn:
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
                tmp_path = tmp.name
            try:
                with sqlite3.connect(tmp_path) as dst_conn:
                    src_conn.backup(dst_conn)
                # Compress the snapshot
                with open(tmp_path, 'rb') as f_in:
                    with gzip.open(str(backup_path), 'wb') as f_out:
                        f_out.write(f_in.read())
            finally:
                os.unlink(tmp_path)

        return backup_path

    def list_backups(self) -> List[Dict]:
        """Return list of backups sorted by creation time (newest first)."""
        backups = []
        for path in sorted(self._backup_dir.glob('cascadia_*.db.gz'), reverse=True):
            stat = path.stat()
            # Extract timestamp from filename
            name = path.name  # cascadia_20260426T030000.db.gz
            created_at = ''
            try:
                ts_part = name.replace('cascadia_', '').replace('.db.gz', '')
                dt = datetime.strptime(ts_part, '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
                created_at = dt.isoformat()
            except Exception:
                created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            backups.append({
                'name': name,
                'path': str(path),
                'size_kb': round(stat.st_size / 1024, 1),
                'created_at': created_at,
            })
        return backups

    def purge_old(self) -> int:
        """Delete backup files older than retention_days. Returns count deleted."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        deleted = 0
        for path in self._backup_dir.glob('cascadia_*.db.gz'):
            try:
                ts_part = path.name.replace('cascadia_', '').replace('.db.gz', '')
                dt = datetime.strptime(ts_part, '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    path.unlink()
                    deleted += 1
            except Exception:
                # Fall back to mtime
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    path.unlink()
                    deleted += 1
        return deleted

    def verify_latest(self) -> bool:
        """
        Verify the most recent backup is readable and contains a valid SQLite database.
        Checks the SQLite magic bytes in the gzip payload.
        Returns True if valid, False if not found or corrupted.
        """
        backups = self.list_backups()
        if not backups:
            return False
        latest_path = Path(backups[0]['path'])
        try:
            with gzip.open(str(latest_path), 'rb') as f:
                header = f.read(16)
            return header[:15] == self.SQLITE_MAGIC
        except Exception:
            return False
