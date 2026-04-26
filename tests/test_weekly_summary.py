from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cascadia.reports.weekly_summary import WeeklySummaryReport


class WeeklySummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / 'test.db')
        self.reports_dir = str(Path(self.tmpdir.name) / 'reports')

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _make_report(self, email: str = '') -> WeeklySummaryReport:
        return WeeklySummaryReport(
            database_path=self.db_path,
            reports_dir=self.reports_dir,
            delivery_email=email,
        )

    def test_build_returns_html(self) -> None:
        rpt = self._make_report()
        html = rpt.build()
        self.assertIn('<!DOCTYPE html>', html)
        self.assertIn('Weekly Summary', html)

    def test_build_includes_date(self) -> None:
        rpt = self._make_report()
        html = rpt.build()
        from datetime import datetime
        year = str(datetime.now().year)
        self.assertIn(year, html)

    def test_deliver_writes_file_when_no_email(self) -> None:
        rpt = self._make_report()
        dest = rpt.deliver()
        self.assertTrue(Path(dest).exists())
        self.assertTrue(dest.endswith('.html'))

    def test_deliver_file_is_valid_html(self) -> None:
        rpt = self._make_report()
        dest = rpt.deliver()
        content = Path(dest).read_text(encoding='utf-8')
        self.assertIn('<html>', content)

    def test_deliver_creates_weekly_subdir(self) -> None:
        rpt = self._make_report()
        rpt.deliver()
        weekly_dir = Path(self.reports_dir) / 'weekly'
        self.assertTrue(weekly_dir.exists())
        files = list(weekly_dir.glob('report_*.html'))
        self.assertEqual(len(files), 1)

    def test_build_with_empty_db(self) -> None:
        rpt = self._make_report()
        html = rpt.build()
        self.assertIn('No runs this week', html)

    def test_build_with_runs(self) -> None:
        import sqlite3
        from datetime import datetime, timezone, timedelta
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE runs (run_id TEXT, operator_id TEXT, run_state TEXT, "
            "outcome TEXT, created_at TEXT, updated_at TEXT)"
        )
        now = datetime.now(timezone.utc)
        yesterday = (now - timedelta(days=1)).isoformat()
        conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                     ('r1', 'quote', 'completed', 'won', yesterday, yesterday))
        conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                     ('r2', 'email', 'completed', 'lost', yesterday, yesterday))
        conn.commit()
        conn.close()

        rpt = self._make_report()
        html = rpt.build()
        self.assertIn('2', html)  # 2 total runs


if __name__ == '__main__':
    unittest.main()
