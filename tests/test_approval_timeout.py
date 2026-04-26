from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from cascadia.system.approval_timeout import ApprovalTimeoutDaemon, DEFAULT_TIMEOUTS


def _make_db(path: str) -> None:
    """Create a minimal approvals table for testing."""
    conn = sqlite3.connect(path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, step_index INTEGER, action_key TEXT,
            decision TEXT DEFAULT 'pending',
            actor TEXT, reason TEXT,
            risk_level TEXT DEFAULT 'MEDIUM',
            created_at TEXT, decided_at TEXT
        )
    ''')
    conn.commit()
    conn.close()


class ApprovalTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / 'test.db')
        _make_db(self.db_path)
        self.daemon = ApprovalTimeoutDaemon(
            db_path=self.db_path,
            handshake_port=9999,  # won't connect in tests
            owner_email='owner@test.com',
            timeouts={'HIGH': 30, 'MEDIUM': 120, 'LOW': 480},
        )

    def tearDown(self) -> None:
        self.daemon.stop()
        self.tmp.cleanup()

    def _insert_approval(self, risk: str = 'MEDIUM',
                          age_minutes: float = 0.0,
                          decision: str = 'pending') -> int:
        from datetime import datetime, timezone, timedelta
        created = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            'INSERT INTO approvals (run_id, step_index, action_key, decision, '
            'risk_level, created_at) VALUES (?,?,?,?,?,?)',
            ('run_001', 0, 'email.send', decision, risk, created.isoformat())
        )
        aid = cursor.lastrowid
        conn.commit()
        conn.close()
        return aid

    def _get_decision(self, aid: int) -> str:
        conn = sqlite3.connect(self.db_path)
        row = conn.execute('SELECT decision FROM approvals WHERE id=?', (aid,)).fetchone()
        conn.close()
        return row[0] if row else ''

    def test_pending_approval_not_touched_before_timeout(self) -> None:
        aid = self._insert_approval(risk='HIGH', age_minutes=10)
        self.daemon._check_all_pending()
        self.assertEqual(self._get_decision(aid), 'pending')

    def test_high_risk_auto_rejected_at_double_threshold(self) -> None:
        # HIGH escalate=30, reject=60
        aid = self._insert_approval(risk='HIGH', age_minutes=61)
        self.daemon._check_all_pending()
        self.assertEqual(self._get_decision(aid), 'rejected')

    def test_medium_risk_auto_rejected(self) -> None:
        # MEDIUM escalate=120, reject=240
        aid = self._insert_approval(risk='MEDIUM', age_minutes=241)
        self.daemon._check_all_pending()
        self.assertEqual(self._get_decision(aid), 'rejected')

    def test_escalation_fires_once(self) -> None:
        # age=35 > escalate_at=30 for HIGH
        aid = self._insert_approval(risk='HIGH', age_minutes=35)
        self.daemon._check_all_pending()
        self.assertIn(aid, self.daemon._escalated)
        # Second check should not re-escalate
        initial_size = len(self.daemon._escalated)
        self.daemon._check_all_pending()
        self.assertEqual(len(self.daemon._escalated), initial_size)

    def test_already_decided_not_touched(self) -> None:
        aid = self._insert_approval(risk='HIGH', age_minutes=200, decision='approved')
        self.daemon._check_all_pending()
        # Should still be approved, not changed by daemon
        self.assertEqual(self._get_decision(aid), 'approved')

    def test_time_remaining_calculation(self) -> None:
        from datetime import datetime, timezone, timedelta
        created = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        remaining = self.daemon.time_remaining(1, created, 'HIGH')
        # escalate_at=30, age=20 → 10 min remaining
        self.assertGreaterEqual(remaining['escalate_in'], 9)
        self.assertLessEqual(remaining['escalate_in'], 11)

    def test_default_timeouts_present(self) -> None:
        self.assertIn('HIGH', DEFAULT_TIMEOUTS)
        self.assertIn('MEDIUM', DEFAULT_TIMEOUTS)
        self.assertIn('LOW', DEFAULT_TIMEOUTS)
        self.assertLess(DEFAULT_TIMEOUTS['HIGH'], DEFAULT_TIMEOUTS['MEDIUM'])


if __name__ == '__main__':
    unittest.main()
