"""
approval_timeout.py — Cascadia OS v0.46
Approval timeout and escalation daemon.
Owns: monitoring pending approvals for timeout, escalating to secondary
      contact, auto-rejecting after final timeout, maintaining audit trail.
Does not own: approval creation (ApprovalStore), notification delivery
              (HANDSHAKE), risk classification (SENTINEL).
"""
# MATURITY: PRODUCTION — Daemon thread, configurable per risk level, full audit.
from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from cascadia.shared.logger import get_logger

logger = get_logger('approval_timeout')

DEFAULT_TIMEOUTS: Dict[str, int] = {
    'HIGH':   30,
    'MEDIUM': 120,
    'LOW':    480,
}


_CHANNEL_PORTS: Dict[str, int] = {
    'telegram':  9000,
    'whatsapp':  9001,
    'sms':       9002,
    'slack':     9003,
}

_PRISM_URL = 'http://localhost:6300'


class ApprovalTimeoutDaemon:
    """
    Monitors pending approvals. Escalates then auto-rejects on timeout.
    Escalation channel is user-configurable via config.escalation.primary_channel.
    Does not own approval creation or delivery.
    """

    def __init__(self, db_path: str, handshake_port: int,
                 owner_email: str = '', escalation_email: str = '',
                 timeouts: Optional[Dict[str, int]] = None,
                 config: Optional[Dict[str, Any]] = None) -> None:
        self._db_path = db_path
        self._handshake_port = handshake_port
        self._owner_email = owner_email
        self._config = config or {}
        self._timeouts = timeouts or DEFAULT_TIMEOUTS
        self._escalated: set = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Resolve escalation channel with backward-compat shim
        esc_cfg = self._config.get('escalation', {})
        self._channel = esc_cfg.get('primary_channel', '').lower()
        if not self._channel:
            if escalation_email or self._config.get('escalation_email'):
                logger.warning(
                    'ApprovalTimeout: escalation_email is deprecated — '
                    'set escalation.primary_channel in config instead'
                )
                self._channel = 'email'
            else:
                self._channel = 'email'  # final fallback
        self._escalation_email = (
            escalation_email
            or self._config.get('escalation_email', '')
            or owner_email
        )

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name='approval-timeout', daemon=True
        )
        self._thread.start()
        logger.info('ApprovalTimeout: daemon started')

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            self._check_all_pending()
            time.sleep(60)

    def _check_all_pending(self) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, run_id, action_key, created_at, "
                "COALESCE(risk_level, 'MEDIUM') as risk_level "
                "FROM approvals WHERE decision = 'pending'"
            ).fetchall()
            conn.close()
            for row in rows:
                self._evaluate(dict(row))
        except Exception as e:
            logger.error('ApprovalTimeout: check failed: %s', e)

    def _evaluate(self, row: Dict[str, Any]) -> None:
        approval_id = row['id']
        risk = row.get('risk_level', 'MEDIUM')
        try:
            created = datetime.fromisoformat(row['created_at'])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except Exception:
            return
        age_minutes = (datetime.now(timezone.utc) - created).total_seconds() / 60

        escalate_at = self._timeouts.get(risk, 120)
        reject_at = escalate_at * 2

        if age_minutes >= reject_at:
            self._auto_reject(approval_id, row)
        elif age_minutes >= escalate_at and approval_id not in self._escalated:
            self._escalate(approval_id, row)

    def _escalate(self, approval_id: int, row: Dict[str, Any]) -> None:
        self._escalated.add(approval_id)
        risk = row.get('risk_level', 'MEDIUM')
        wait = self._timeouts.get(risk, 120)
        title = '[Zyrcon] Mission needs your attention'
        body = (
            f'An approval has been waiting for over {wait} minutes.\n\n'
            f'Action: {row["action_key"]}\n'
            f'Risk: {risk}\n'
            f'Run ID: {row["run_id"]}\n\n'
            f'Log in to your Zyrcon dashboard to approve or reject: {_PRISM_URL}\n'
            f'This action will auto-reject if not reviewed within {wait} more minutes.'
        )
        self._deliver(title, body, row)
        # Emit NATS decision_request subject so supervisor can track escalation
        try:
            from cascadia.automation.failure_event import _nats_publish_sync
            _nats_publish_sync(
                'zyrcon.beacon.decision_request',
                json.dumps({
                    'approval_id': approval_id,
                    'run_id': row['run_id'],
                    'action_key': row['action_key'],
                    'risk_level': risk,
                    'channel': self._channel,
                    'escalated_at': datetime.now(timezone.utc).isoformat(),
                }).encode(),
            )
        except Exception:
            pass
        logger.warning('ApprovalTimeout: escalated approval %s via %s', approval_id, self._channel)

    def _deliver(self, title: str, body: str, row: Dict[str, Any]) -> None:
        """Route escalation to the configured channel."""
        ch = self._channel
        if ch == 'email':
            target = self._escalation_email
            if target:
                self._send_email(to=target, subject=title, body=body)
        elif ch in _CHANNEL_PORTS:
            port = _CHANNEL_PORTS[ch]
            self._send_channel(port, ch, title, body, row)
        else:
            logger.warning('ApprovalTimeout: unknown channel %r — falling back to email', ch)
            if self._escalation_email:
                self._send_email(to=self._escalation_email, subject=title, body=body)

    def _send_channel(self, port: int, channel: str, title: str,
                      body: str, row: Dict[str, Any]) -> None:
        try:
            payload = json.dumps({
                'title': title, 'body': body,
                'run_id': row.get('run_id'), 'channel': channel,
            }).encode()
            req = urllib.request.Request(
                f'http://127.0.0.1:{port}/api/send',
                data=payload, method='POST',
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception as e:
            logger.error('ApprovalTimeout: %s delivery failed: %s', channel, e)

    def _auto_reject(self, approval_id: int, row: Dict[str, Any]) -> None:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "UPDATE approvals SET decision='rejected', actor='system:timeout', "
                "reason=?, decided_at=? WHERE id=? AND decision='pending'",
                (
                    f'Auto-rejected after timeout ({row.get("risk_level","MEDIUM")} risk)',
                    datetime.now(timezone.utc).isoformat(),
                    approval_id,
                )
            )
            conn.commit()
            conn.close()
            self._escalated.discard(approval_id)
            logger.warning('ApprovalTimeout: auto-rejected approval %s', approval_id)
        except Exception as e:
            logger.error('ApprovalTimeout: auto-reject failed for %s: %s', approval_id, e)

    def _send_email(self, to: str, subject: str, body: str) -> None:
        try:
            payload = json.dumps({'to': to, 'subject': subject, 'body': body}).encode()
            req = urllib.request.Request(
                f'http://127.0.0.1:{self._handshake_port}/api/handshake/smtp/send',
                data=payload, method='POST',
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception as e:
            logger.error('ApprovalTimeout: email failed: %s', e)

    def time_remaining(self, approval_id: int, created_at: str,
                       risk_level: str = 'MEDIUM') -> Dict[str, int]:
        """Return minutes until escalation and rejection for a pending approval."""
        try:
            created = datetime.fromisoformat(created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except Exception:
            return {'escalate_in': 0, 'reject_in': 0}
        age = (datetime.now(timezone.utc) - created).total_seconds() / 60
        escalate_at = self._timeouts.get(risk_level, 120)
        reject_at = escalate_at * 2
        return {
            'escalate_in': max(0, int(escalate_at - age)),
            'reject_in': max(0, int(reject_at - age)),
        }
