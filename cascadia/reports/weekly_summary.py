"""
weekly_summary.py — Cascadia OS Task 12
Friday 17:00 weekly summary report.
Aggregates audit log events and run outcomes for the past 7 days.
Delivers via HANDSHAKE POST /call, falls back to file in data/reports/weekly/.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _http_post(port: int, path: str, payload: Dict[str, Any], timeout: float = 5.0) -> Optional[Dict]:
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            f'http://127.0.0.1:{port}{path}',
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


class WeeklySummaryReport:
    """
    Builds and delivers a weekly summary email/file on Fridays at 17:00.
    Reads audit log and run outcomes from the database.
    """

    def __init__(self, database_path: str, reports_dir: str,
                 handshake_port: int = 6203, delivery_email: str = '') -> None:
        self.database_path = database_path
        self.reports_dir = Path(reports_dir)
        self.handshake_port = handshake_port
        self.delivery_email = delivery_email

    def build(self) -> str:
        """Build the HTML weekly summary. Returns HTML string."""
        week_ago = (_now() - timedelta(days=7)).isoformat()
        runs = self._query_runs(week_ago)
        outcomes = self._tally_outcomes(runs)
        top_operators = self._top_operators(runs)

        now_str = _now().strftime('%B %d, %Y')
        week_of = (_now() - timedelta(days=6)).strftime('%b %d')
        week_end = _now().strftime('%b %d')

        rows_html = ''.join(
            f'<tr><td>{op}</td><td>{cnt}</td></tr>'
            for op, cnt in top_operators
        )

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body{{font-family:-apple-system,sans-serif;background:#f5f7fa;margin:0;padding:20px}}
  .wrap{{max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08)}}
  .head{{background:linear-gradient(135deg,#b8860b,#D4AF37);padding:24px;color:#fff}}
  .head h1{{margin:0;font-size:20px;font-weight:700}}
  .head p{{margin:6px 0 0;font-size:12px;opacity:.85}}
  .body{{padding:24px}}
  .stat-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px}}
  .stat{{background:#f5f7fa;border-radius:8px;padding:12px;text-align:center}}
  .stat .val{{font-size:24px;font-weight:700;color:#0a0a0a}}
  .stat .lbl{{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:.1em;margin-top:2px}}
  table{{width:100%;border-collapse:collapse;margin-top:12px}}
  th{{font-size:10px;text-transform:uppercase;color:#94a3b8;letter-spacing:.08em;padding:8px 12px;text-align:left;border-bottom:1px solid #e2e8f0;background:#f5f7fa}}
  td{{font-size:12px;padding:8px 12px;border-bottom:1px solid #e2e8f0}}
  .footer{{padding:16px 24px;background:#f5f7fa;font-size:10px;color:#94a3b8;text-align:center}}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <h1>Cascadia OS — Weekly Summary</h1>
    <p>{week_of} – {week_end} &nbsp;·&nbsp; Generated {now_str}</p>
  </div>
  <div class="body">
    <div class="stat-row">
      <div class="stat"><div class="val">{len(runs)}</div><div class="lbl">Total Runs</div></div>
      <div class="stat"><div class="val">{outcomes.get('won', 0)}</div><div class="lbl">Won</div></div>
      <div class="stat"><div class="val">{outcomes.get('lost', 0)}</div><div class="lbl">Lost</div></div>
    </div>
    <h3 style="font-size:12px;font-weight:700;margin:0 0 8px">Top Operators This Week</h3>
    <table>
      <thead><tr><th>Operator</th><th>Runs</th></tr></thead>
      <tbody>{rows_html or '<tr><td colspan="2" style="color:#94a3b8">No runs this week</td></tr>'}</tbody>
    </table>
  </div>
  <div class="footer">Cascadia OS · Local AI Automation · This report was generated automatically.</div>
</div>
</body>
</html>"""

    def _query_runs(self, since: str) -> List[Dict[str, Any]]:
        try:
            import sqlite3
            conn = sqlite3.connect(self.database_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT run_id, operator_id, run_state, outcome, created_at, updated_at "
                "FROM runs WHERE created_at >= ? ORDER BY created_at DESC",
                (since,)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _tally_outcomes(self, runs: List[Dict[str, Any]]) -> Dict[str, int]:
        tally: Dict[str, int] = {}
        for r in runs:
            o = r.get('outcome') or 'no_decision'
            tally[o] = tally.get(o, 0) + 1
        return tally

    def _top_operators(self, runs: List[Dict[str, Any]], top_n: int = 5) -> List[tuple]:
        counts: Dict[str, int] = {}
        for r in runs:
            op = r.get('operator_id') or 'unknown'
            counts[op] = counts.get(op, 0) + 1
        return sorted(counts.items(), key=lambda x: -x[1])[:top_n]

    def deliver(self, subject: Optional[str] = None) -> str:
        """Build and deliver the weekly summary. Returns delivery destination."""
        html = self.build()
        if subject is None:
            subject = f'Cascadia OS Weekly Summary — {_now().strftime("%B %d, %Y")}'

        if self.delivery_email:
            result = _http_post(self.handshake_port, '/call', {
                'connection_id': 'email',
                'to': self.delivery_email,
                'subject': subject,
                'body': html,
                'content_type': 'text/html',
            })
            if result and result.get('ok'):
                return f'email:{self.delivery_email}'

        # File fallback
        out_dir = self.reports_dir / 'weekly'
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f'report_{_now().strftime("%Y-%m-%d_%H%M")}.html'
        (out_dir / filename).write_text(html, encoding='utf-8')
        return str(out_dir / filename)
