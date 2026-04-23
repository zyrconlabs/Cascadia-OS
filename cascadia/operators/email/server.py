"""
operators/email/server.py
--------------------------
Email Operator — general purpose outbound email service.

Called by any operator that needs to send a message:
  - Campaign system: "posts ready for review"
  - RECON: "lead report attached"
  - CHIEF: "morning brief"
  - SCOUT: "hot lead just came in"

Supports:
  - SMTP (Gmail, any mail server)
  - Simulated mode (dev/demo — logs to file, no real send)

Config lives in operators/email/email.config.json — never in the OS.

Routes:
  POST /send        — send one email
  POST /send_report — send a formatted report with sections
  GET  /api/health  — health check
  GET  /api/status  — delivery stats
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

PORT         = int(os.environ.get('EMAIL_OPERATOR_PORT', '8010'))
BASE_DIR     = Path(__file__).parent
CONFIG_FILE  = BASE_DIR / 'email.config.json'
LOG_DIR      = BASE_DIR / 'data'
SENT_LOG     = LOG_DIR / 'sent.json'

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s | EMAIL | %(message)s')
log = logging.getLogger(__name__)

_stats = {
    'started_at':       datetime.now(timezone.utc).isoformat(),
    'emails_sent':      0,
    'emails_simulated': 0,
    'last_sent_at':     None,
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    # Defaults — simulated mode until real SMTP is configured
    return {
        'mode':        'simulated',   # 'simulated' | 'smtp'
        'from_name':   'Zyrcon-X',
        'from_email':  'noreply@zyrconlabs.com',
        'smtp_host':   'smtp.gmail.com',
        'smtp_port':   587,
        'smtp_user':   '',
        'smtp_pass':   '',
        'default_to':  '',
    }


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def _log_sent(record: Dict[str, Any]) -> None:
    log_data: List[Dict] = []
    if SENT_LOG.exists():
        try:
            log_data = json.loads(SENT_LOG.read_text())
        except Exception:
            log_data = []
    log_data.insert(0, record)
    SENT_LOG.write_text(json.dumps(log_data[:200], indent=2))


def send_email(
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Core send function. Used internally by all routes.
    Returns a result dict — never raises.
    """
    cfg     = load_config()
    mode    = cfg.get('mode', 'simulated')
    msg_id  = f'msg_{uuid.uuid4().hex[:12]}'
    ts      = datetime.now(timezone.utc).isoformat()
    sender  = f"{from_name or cfg.get('from_name', 'Zyrcon-X')} <{cfg.get('from_email', 'noreply@zyrconlabs.com')}>"
    to_addr = to or cfg.get('default_to', '')

    if not to_addr:
        return {'ok': False, 'error': 'No recipient address provided and no default_to configured.'}

    record = {
        'msg_id':   msg_id,
        'to':       to_addr,
        'subject':  subject,
        'mode':     mode,
        'sent_at':  ts,
        'status':   'pending',
    }

    if mode == 'simulated':
        # Write to a local log file — no real send
        sim_path = LOG_DIR / f'simulated_{msg_id}.txt'
        sim_path.write_text(
            f"TO: {to_addr}\nFROM: {sender}\nSUBJECT: {subject}\n"
            f"SENT AT: {ts}\n\n{'='*60}\n\n{body}"
        )
        record['status'] = 'simulated'
        _stats['emails_simulated'] += 1
        log.info('EMAIL simulated → %s | %s', to_addr, subject)

    else:
        # Real SMTP send
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = sender
            msg['To']      = to_addr
            if reply_to:
                msg['Reply-To'] = reply_to

            msg.attach(MIMEText(body, 'plain'))
            if html_body:
                msg.attach(MIMEText(html_body, 'html'))

            ctx = ssl.create_default_context()
            with smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port']) as server:
                server.ehlo()
                server.starttls(context=ctx)
                server.login(cfg['smtp_user'], cfg['smtp_pass'])
                server.sendmail(cfg.get('from_email'), to_addr, msg.as_string())

            record['status'] = 'sent'
            _stats['emails_sent'] += 1
            log.info('EMAIL sent → %s | %s', to_addr, subject)

        except Exception as exc:
            record['status'] = 'failed'
            record['error']  = str(exc)
            log.error('EMAIL failed → %s | %s', to_addr, exc)
            _log_sent(record)
            return {'ok': False, 'msg_id': msg_id, 'error': str(exc)}

    _stats['last_sent_at'] = ts
    _log_sent(record)
    return {'ok': True, 'msg_id': msg_id, 'status': record['status'], 'to': to_addr}


def build_campaign_review_email(state: Dict[str, Any]) -> tuple[str, str]:
    """
    Build subject + plain text body for a campaign review notification.
    Called by the social pipeline when QC passes and posts await approval.
    """
    plan       = state.get('campaign_plan') or {}
    qc_report  = state.get('qc_report') or {}
    drafts     = state.get('platform_drafts') or {}
    day_num    = (state.get('current_day_index') or 0) + 1
    total_days = state.get('total_days') or 1
    topic      = plan.get('topic') or state.get('topic') or 'Campaign'
    score_pct  = int((qc_report.get('campaign_score') or 0) * 100)
    iteration  = qc_report.get('iteration') or 1

    subject = f"[Zyrcon-X] Campaign ready for review — {topic} · Day {day_num} of {total_days}"

    lines = [
        f"CAMPAIGN REVIEW READY",
        f"{'='*60}",
        f"Topic:    {topic}",
        f"Day:      {day_num} of {total_days}",
        f"QC Score: {score_pct}%  (passed after {iteration} iteration{'s' if iteration > 1 else ''})",
        f"",
        f"POSTS AWAITING YOUR APPROVAL",
        f"{'='*60}",
    ]

    for platform, draft in drafts.items():
        post_report = (qc_report.get('post_reports') or {}).get(platform, {})
        post_score  = int((post_report.get('total_score') or 0) * 100)
        body        = draft.get('body', '')
        hashtags    = ' '.join(draft.get('hashtags') or [])

        lines += [
            f"",
            f"[ {platform.upper()} ]  Score: {post_score}%",
            f"{'-'*60}",
            body,
            f"",
            hashtags,
            f"{'-'*60}",
        ]

        signals = post_report.get('signals') or {}
        if signals:
            lines.append("Signal scores:")
            for sig, data in signals.items():
                bar = '█' * int(data.get('score', 0) * 10)
                lines.append(f"  {sig:<22} {bar:<10} {int(data.get('score',0)*100)}%")

    lines += [
        f"",
        f"{'='*60}",
        f"To approve: open PRISM at http://localhost:6300 and go to Campaigns.",
        f"Or reply to this email with APPROVE or REJECT.",
        f"",
        f"— Zyrcon-X",
    ]

    return subject, '\n'.join(lines)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/send', methods=['POST'])
def send():
    data    = request.get_json(force=True) or {}
    to      = data.get('to', '')
    subject = data.get('subject', '(no subject)')
    body    = data.get('body', '')
    html    = data.get('html_body')

    if not body:
        return jsonify({'ok': False, 'error': 'body required'}), 400

    result = send_email(to=to, subject=subject, body=body, html_body=html)
    return jsonify(result), 200 if result['ok'] else 500


@app.route('/send_campaign_review', methods=['POST'])
def send_campaign_review():
    """
    Called by the social pipeline when a campaign day passes QC.
    Sends a formatted review email to the configured recipient.
    """
    data    = request.get_json(force=True) or {}
    state   = data.get('state') or data
    cfg     = load_config()
    to      = data.get('to') or cfg.get('default_to', '')

    subject, body = build_campaign_review_email(state)
    result = send_email(to=to, subject=subject, body=body)
    return jsonify(result), 200 if result['ok'] else 500


@app.route('/send_report', methods=['POST'])
def send_report():
    """
    General purpose report sender. Called by RECON, CHIEF, etc.
    Accepts: to, subject, sections=[{title, content}], summary
    """
    data     = request.get_json(force=True) or {}
    to       = data.get('to', '')
    subject  = data.get('subject', 'Zyrcon-X Report')
    summary  = data.get('summary', '')
    sections = data.get('sections') or []

    lines = []
    if summary:
        lines += [summary, '', '='*60, '']
    for section in sections:
        lines += [
            section.get('title', 'Section').upper(),
            '-'*40,
            section.get('content', ''),
            '',
        ]
    body = '\n'.join(lines)

    result = send_email(to=to, subject=subject, body=body)
    return jsonify(result), 200 if result['ok'] else 500


@app.route('/api/health')
def health():
    cfg = load_config()
    return jsonify({
        'status':  'online',
        'service': 'email',
        'version': '1.0.0',
        'port':    PORT,
        'mode':    cfg.get('mode', 'simulated'),
    })


@app.route('/api/status')
def status():
    log_data = []
    if SENT_LOG.exists():
        try:
            log_data = json.loads(SENT_LOG.read_text())[:10]
        except Exception:
            pass
    return jsonify({**_stats, 'recent': log_data})


if __name__ == '__main__':
    log.info('Email operator starting on port %s', PORT)
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
