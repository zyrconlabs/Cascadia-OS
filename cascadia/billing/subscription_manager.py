"""
subscription_manager.py — Cascadia OS
Manages customer subscription state in SQLite.
Owns: customer record CRUD, tier read/write, subscription status tracking.
Does not own: Stripe API calls (stripe_handler), license key generation (license_generator),
              email delivery (email_delivery).
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from cascadia.licensing.tier_validator import get_max_users
from cascadia.shared.logger import get_logger

logger = get_logger('subscription_manager')
DB_PATH = Path('./data/runtime/subscriptions.db')


class SubscriptionManager:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS customers (
                    stripe_customer_id TEXT PRIMARY KEY,
                    email              TEXT NOT NULL,
                    tier               TEXT NOT NULL DEFAULT 'lite',
                    license_key        TEXT,
                    stripe_sub_id      TEXT,
                    status             TEXT DEFAULT 'active',
                    subscribed_at      TEXT,
                    renewed_at         TEXT,
                    cancelled_at       TEXT,
                    created_at         TEXT NOT NULL,
                    updated_at         TEXT NOT NULL
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_email ON customers (email)
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id   TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                )
            ''')
            self._migrate_add_seats(conn)

    def _migrate_add_seats(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(customers)").fetchall()}
        if 'max_users' not in existing:
            conn.execute("ALTER TABLE customers ADD COLUMN max_users INTEGER NOT NULL DEFAULT 1")
        if 'seat_count' not in existing:
            conn.execute("ALTER TABLE customers ADD COLUMN seat_count INTEGER NOT NULL DEFAULT 0")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_customer(self, stripe_customer_id: str) -> dict | None:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT * FROM customers WHERE stripe_customer_id = ?',
                (stripe_customer_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_customer_by_email(self, email: str) -> dict | None:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT * FROM customers WHERE email = ? ORDER BY created_at DESC LIMIT 1',
                (email,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_customer(self, stripe_customer_id: str, email: str, tier: str,
                        license_key: str = None, stripe_sub_id: str = None) -> None:
        now = self._now()
        max_u = get_max_users(tier)
        existing = self.get_customer(stripe_customer_id)
        with sqlite3.connect(self._db) as conn:
            if existing:
                conn.execute('''
                    UPDATE customers SET
                        email = ?, tier = ?, license_key = ?, stripe_sub_id = ?,
                        status = 'active', renewed_at = ?, updated_at = ?, max_users = ?
                    WHERE stripe_customer_id = ?
                ''', (email, tier, license_key, stripe_sub_id, now, now, max_u, stripe_customer_id))
                logger.info('SubscriptionManager: updated %s → %s', stripe_customer_id, tier)
            else:
                conn.execute('''
                    INSERT INTO customers
                    (stripe_customer_id, email, tier, license_key, stripe_sub_id,
                     status, subscribed_at, created_at, updated_at, max_users)
                    VALUES (?,?,?,?,?,'active',?,?,?,?)
                ''', (stripe_customer_id, email, tier, license_key, stripe_sub_id,
                      now, now, now, max_u))
                logger.info('SubscriptionManager: created %s tier=%s', stripe_customer_id, tier)

    def get_max_users(self, stripe_customer_id: str) -> int:
        customer = self.get_customer(stripe_customer_id)
        if not customer:
            return 1
        return customer.get('max_users') or get_max_users(customer['tier'])

    def can_add_user(self, stripe_customer_id: str) -> bool:
        customer = self.get_customer(stripe_customer_id)
        if not customer:
            return False
        max_u = customer.get('max_users') or get_max_users(customer['tier'])
        seat_count = customer.get('seat_count') or 0
        return seat_count < max_u

    def update_tier(self, stripe_customer_id: str, new_tier: str) -> None:
        now = self._now()
        with sqlite3.connect(self._db) as conn:
            conn.execute('''
                UPDATE customers
                SET tier = ?, status = 'active', renewed_at = ?, updated_at = ?
                WHERE stripe_customer_id = ?
            ''', (new_tier, now, now, stripe_customer_id))
        logger.info('SubscriptionManager: tier update %s → %s', stripe_customer_id, new_tier)

    def downgrade_to_lite(self, stripe_customer_id: str) -> None:
        now = self._now()
        with sqlite3.connect(self._db) as conn:
            conn.execute('''
                UPDATE customers
                SET tier = 'lite', status = 'cancelled', cancelled_at = ?, updated_at = ?
                WHERE stripe_customer_id = ?
            ''', (now, now, stripe_customer_id))
        logger.info('SubscriptionManager: downgraded %s to lite', stripe_customer_id)

    def get_tier(self, stripe_customer_id: str) -> str:
        """Returns 'lite' if customer not found."""
        customer = self.get_customer(stripe_customer_id)
        return customer['tier'] if customer else 'lite'

    def list_customers(self, tier: str = None) -> list[dict]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            if tier:
                rows = conn.execute(
                    'SELECT * FROM customers WHERE tier = ? ORDER BY created_at DESC',
                    (tier,)
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM customers ORDER BY created_at DESC'
                ).fetchall()
        return [dict(r) for r in rows]

    def is_event_processed(self, event_id: str) -> bool:
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                'SELECT 1 FROM processed_events WHERE event_id = ?', (event_id,)
            ).fetchone()
        return row is not None

    def mark_event_processed(self, event_id: str) -> None:
        now = self._now()
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                'INSERT OR IGNORE INTO processed_events (event_id, processed_at) VALUES (?, ?)',
                (event_id, now),
            )

    def get_stats(self) -> dict:
        """Summary for PRISM billing dashboard."""
        with sqlite3.connect(self._db) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM customers WHERE status = 'active'"
            ).fetchone()[0]
            by_tier = conn.execute(
                "SELECT tier, COUNT(*) as n FROM customers WHERE status = 'active' GROUP BY tier"
            ).fetchall()
        return {
            'total_active': total,
            'by_tier': {r[0]: r[1] for r in by_tier},
        }
