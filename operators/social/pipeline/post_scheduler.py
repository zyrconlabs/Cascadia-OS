"""
post_scheduler.py — Zyrcon Social Operator v1.1
Manages a queue of approved posts waiting to be published at a scheduled time.
Owns: post queue persistence (SQLite), time-based publication dispatch.
Does not own: content generation, QC scoring, platform publishing (connectors),
              approval gates (SENTINEL/ApprovalStore).
"""
# MATURITY: PRODUCTION — SQLite-backed queue. Idempotent dispatch. Marks failures.
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from cascadia.shared.logger import get_logger

logger = get_logger('post_scheduler')

DB_PATH = Path(__file__).parent.parent / 'data' / 'scheduled_posts.db'


class PostScheduler:
    """
    Owns the scheduled post queue.
    Does not own publishing (connectors handle that).
    """

    def __init__(self, publish_fn: Callable[[str, str], Dict[str, Any]],
                 db_path: Optional[Path] = None) -> None:
        """publish_fn(platform, content) → {'success': bool, ...}"""
        self._publish_fn = publish_fn
        self._db_path = db_path or DB_PATH
        self._lock = threading.Lock()
        self._running = False
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    content TEXT NOT NULL,
                    publish_at TEXT NOT NULL,
                    approval_id INTEGER,
                    status TEXT DEFAULT 'queued',
                    published_at TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL
                )
            ''')

    def schedule(self, platform: str, content: str, publish_at: str,
                 approval_id: Optional[int] = None) -> int:
        """Add a post to the queue. publish_at is ISO 8601 UTC. Returns post_id."""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute('''
                INSERT INTO scheduled_posts
                (platform, content, publish_at, approval_id, status, created_at)
                VALUES (?, ?, ?, ?, 'queued', ?)
            ''', (platform, content, publish_at, approval_id,
                  datetime.now(timezone.utc).isoformat()))
            post_id = cursor.lastrowid
        logger.info('PostScheduler: queued %s post %s for %s', platform, post_id, publish_at)
        return post_id

    def get_due(self) -> List[Dict[str, Any]]:
        """Return posts due for publishing (publish_at <= now, status='queued')."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute('''
                SELECT * FROM scheduled_posts
                WHERE publish_at <= ? AND status = 'queued'
                ORDER BY publish_at ASC
            ''', (now,)).fetchall()]

    def get_queue(self) -> List[Dict[str, Any]]:
        """Return all queued and failed posts for dashboard display."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute('''
                SELECT * FROM scheduled_posts
                WHERE status IN ('queued', 'failed')
                ORDER BY publish_at ASC
            ''').fetchall()]

    def cancel(self, post_id: int) -> bool:
        """Cancel a queued post. Returns True if cancelled."""
        with sqlite3.connect(self._db_path) as conn:
            affected = conn.execute(
                "UPDATE scheduled_posts SET status='cancelled' WHERE id=? AND status='queued'",
                (post_id,)
            ).rowcount
        return affected > 0

    def mark_published(self, post_id: int) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute('''
                UPDATE scheduled_posts
                SET status='published', published_at=?
                WHERE id=?
            ''', (datetime.now(timezone.utc).isoformat(), post_id))

    def mark_failed(self, post_id: int, error: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                'UPDATE scheduled_posts SET status=\'failed\', error=? WHERE id=?',
                (error, post_id)
            )

    def start(self) -> None:
        self._running = True
        t = threading.Thread(target=self._loop, name='post-scheduler', daemon=True)
        t.start()
        logger.info('PostScheduler: dispatch loop started')

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            self._dispatch_due()
            time.sleep(30)

    def _dispatch_due(self) -> None:
        for post in self.get_due():
            try:
                result = self._publish_fn(post['platform'], post['content'])
                if result.get('success'):
                    self.mark_published(post['id'])
                    logger.info('PostScheduler: published post %s', post['id'])
                else:
                    self.mark_failed(post['id'], result.get('error', 'unknown'))
                    logger.error('PostScheduler: post %s failed: %s',
                                 post['id'], result.get('error'))
            except Exception as e:
                self.mark_failed(post['id'], str(e))
                logger.error('PostScheduler: dispatch error for %s: %s', post['id'], e)
