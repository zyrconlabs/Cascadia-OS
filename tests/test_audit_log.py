from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cascadia.system.audit_log import AuditLog


class AuditLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / 'audit.db'
        self.log = AuditLog(db_path=db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_record_creates_entry(self) -> None:
        self.log.record('approval_requested', approval_id=1, run_id='r1',
                        action_key='email.send')
        rows = self.log.query(days=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['event_type'], 'approval_requested')
        self.assertEqual(rows[0]['action_key'], 'email.send')

    def test_chain_hash_set(self) -> None:
        self.log.record('approval_requested', approval_id=1)
        rows = self.log.query(days=1)
        self.assertIsNotNone(rows[0]['chain_hash'])
        self.assertEqual(len(rows[0]['chain_hash']), 64)  # SHA-256 hex

    def test_verify_chain_intact(self) -> None:
        self.log.record('approval_requested', approval_id=1)
        self.log.record('approval_decided', approval_id=1, decision='approved', actor='owner')
        self.assertTrue(self.log.verify_chain())

    def test_verify_chain_detects_tamper(self) -> None:
        self.log.record('approval_requested', approval_id=1)
        self.log.record('approval_decided', approval_id=1, decision='approved')
        # Tamper with the first record
        with sqlite3.connect(self.log._db_path) as conn:
            conn.execute(
                "UPDATE audit_events SET decision='rejected' WHERE id=1"
            )
        self.assertFalse(self.log.verify_chain())

    def test_empty_chain_valid(self) -> None:
        self.assertTrue(self.log.verify_chain())

    def test_query_filter_by_decision(self) -> None:
        self.log.record('approval_decided', approval_id=1, decision='approved')
        self.log.record('approval_decided', approval_id=2, decision='rejected')
        approved = self.log.query(days=1, decision='approved')
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]['decision'], 'approved')

    def test_export_csv_produces_header(self) -> None:
        self.log.record('test_event', approval_id=42, actor='system')
        csv_out = self.log.export_csv(days=1)
        self.assertIn('event_type', csv_out)
        self.assertIn('test_event', csv_out)

    def test_export_csv_empty_when_no_events(self) -> None:
        self.assertEqual(self.log.export_csv(days=1), '')

    def test_multiple_records_chain_links(self) -> None:
        for i in range(5):
            self.log.record('event', approval_id=i)
        self.assertTrue(self.log.verify_chain())

    def test_edited_flag_stored(self) -> None:
        self.log.record('approval_decided', approval_id=1, edited=True,
                        edit_summary='Changed amount')
        rows = self.log.query(days=1)
        self.assertEqual(rows[0]['edited'], 1)
        self.assertEqual(rows[0]['edit_summary'], 'Changed amount')


if __name__ == '__main__':
    unittest.main()
