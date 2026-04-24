"""
vanguard/vanguard.py - Cascadia OS v0.44
ZYRCON VANGUARD: Communication gateway.

Owns: inbound channel routing (email, webhook, SMS, API calls),
      outbound message dispatch, channel registration,
      message normalization into Cascadia OS envelope format.
Does not own: encryption (CURTAIN), chat sessions (BELL),
              operator execution (BEACON), storage (VAULT).

Vanguard implies first contact — the layer that meets the outside
world before anything else in Cascadia OS does.
"""
# MATURITY: PRODUCTION — Inbound normalization and outbound webhook/HTTP dispatch via HANDSHAKE. SMTP/SMS adapters are v0.44 roadmap.
from __future__ import annotations

import json
import urllib.request
import urllib.error

import argparse
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Supported inbound channel types
CHANNEL_TYPES = {
    'email',
    'webhook',
    'sms',
    'api',
    'bell',       # From BELL chat interface
    'calendar',   # Calendar event triggers
    'form',       # Web form submissions
}


class InboundMessage:
    """
    A normalized message received from any external channel.
    Owns: channel normalization. Does not own routing decisions.
    """

    def __init__(self, channel: str, sender: str, content: str,
                 raw: Optional[Dict] = None, tenant_id: str = 'default') -> None:
        self.message_id = f'vg_{uuid.uuid4().hex[:10]}'
        self.channel = channel
        self.sender = sender
        self.content = content
        self.raw = raw or {}
        self.tenant_id = tenant_id
        self.received_at = _now()
        self.routed = False
        self.routed_to: Optional[str] = None

    def to_envelope(self) -> Dict[str, Any]:
        """Convert to Cascadia OS envelope format for BEACON."""
        return {
            'message_id': self.message_id,
            'channel': self.channel,
            'sender': self.sender,
            'content': self.content,
            'tenant_id': self.tenant_id,
            'received_at': self.received_at,
            'raw': self.raw,
        }


class VanguardService:
    """
    ZYRCON VANGUARD - Communication gateway.
    Owns channel registration, inbound normalization, and outbound dispatch.
    Does not own encryption, sessions, or operator execution.
    """

    def __init__(self, config_path: str, name: str) -> None:
        self.config = load_config(config_path)
        component = next(c for c in self.config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=component['port'],
            heartbeat_file=component['heartbeat_file'],
            log_dir=self.config['log_dir'],
        )
        self._lock = threading.Lock()
        self._channels: Dict[str, Dict[str, Any]] = {}   # channel_id -> config
        self._inbox: List[InboundMessage] = []            # Recent messages
        self._outbox: List[Dict[str, Any]] = []           # Dispatched messages

        # HANDSHAKE port for outbound dispatch
        hs_comp = next((c for c in self.config.get('components', []) if c['name'] == 'handshake'), None)
        self._handshake_port: int | None = hs_comp['port'] if hs_comp else None

        # Register built-in channels
        self._register_defaults()

        self.runtime.register_route('POST', '/channel/register',   self.register_channel)
        self.runtime.register_route('GET',  '/channel/list',       self.list_channels)
        self.runtime.register_route('POST', '/inbound',            self.receive_inbound)
        self.runtime.register_route('POST', '/outbound',           self.dispatch_outbound)
        self.runtime.register_route('GET',  '/inbox',              self.get_inbox)
        self.runtime.register_route('GET',  '/outbox',             self.get_outbox)
        self.runtime.register_route('POST', '/webhook',            self.receive_webhook)

    def _register_defaults(self) -> None:
        for ch_type in ('email', 'webhook', 'api', 'bell'):
            self._channels[ch_type] = {
                'channel_id': ch_type,
                'type': ch_type,
                'active': True,
                'registered_at': _now(),
            }

    def register_channel(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Register a new communication channel with VANGUARD."""
        ch_type = payload.get('type', '')
        if ch_type not in CHANNEL_TYPES:
            return 400, {'error': f'unsupported channel type: {ch_type}', 'supported': list(CHANNEL_TYPES)}
        channel_id = payload.get('channel_id', f'{ch_type}_{uuid.uuid4().hex[:6]}')
        with self._lock:
            self._channels[channel_id] = {**payload, 'channel_id': channel_id, 'registered_at': _now()}
        self.runtime.logger.info('VANGUARD channel registered: %s (%s)', channel_id, ch_type)
        return 201, {'channel_id': channel_id, 'type': ch_type}

    def list_channels(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        with self._lock:
            channels = list(self._channels.values())
        return 200, {'channels': channels, 'count': len(channels)}

    def receive_inbound(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Receive and normalize any inbound message.
        Returns a normalized envelope for BEACON to route.
        """
        channel = payload.get('channel', 'api')
        sender = payload.get('sender', 'unknown')
        content = payload.get('content', '')
        tenant_id = payload.get('tenant_id', 'default')

        msg = InboundMessage(
            channel=channel, sender=sender, content=content,
            raw=payload, tenant_id=tenant_id,
        )
        with self._lock:
            self._inbox.append(msg)
            if len(self._inbox) > 200:
                self._inbox = self._inbox[-200:]

        self.runtime.logger.info('VANGUARD inbound: %s from %s via %s', msg.message_id, sender, channel)
        return 202, {'message_id': msg.message_id, 'envelope': msg.to_envelope()}

    def receive_webhook(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Dedicated webhook endpoint. Normalizes and queues for BEACON.
        Trigger source could be Stripe, Calendly, Typeform, Zapier, etc.
        """
        source = payload.get('source', 'webhook')
        return self.receive_inbound({
            **payload,
            'channel': 'webhook',
            'sender': source,
            'content': f'Webhook from {source}',
        })

    def dispatch_outbound(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Dispatch an outbound message through a registered channel.
        For webhook and email channels: forwards to HANDSHAKE for real execution.
        For other channels: logs and queues (SMTP/SMS adapters planned).
        """
        channel   = payload.get('channel', 'webhook')
        recipient = payload.get('recipient', '')
        content   = payload.get('content', '')
        url       = payload.get('url', '')
        headers   = payload.get('headers', {})
        msg_id    = f'out_{uuid.uuid4().hex[:10]}'

        dispatched = False
        status = 'queued'
        handshake_result: Dict[str, Any] = {}

        # Forward to HANDSHAKE for real execution on supported channels
        if self._handshake_port and channel in ('webhook', 'email', 'http'):
            try:
                if channel == 'webhook' and url:
                    body = json.dumps({
                        'url': url,
                        'body': {'recipient': recipient, 'content': content,
                                 **payload.get('body', {})},
                        'headers': headers,
                        'operator_id': 'vanguard',
                    }).encode()
                elif channel == 'email':
                    # Route through HANDSHAKE proxy_call with registered email connection
                    conn_id = payload.get('connection_id', '')
                    if not conn_id:
                        status = 'queued'
                        body = None
                    else:
                        body = json.dumps({
                            'connection_id': conn_id,
                            'method': 'POST',
                            'payload': {'to': recipient, 'subject': payload.get('subject', ''),
                                       'body': content},
                            'operator_id': 'vanguard',
                        }).encode()
                else:
                    body = None

                if body:
                    hs_path = '/webhook' if channel == 'webhook' else '/call'
                    req = urllib.request.Request(
                        f'http://127.0.0.1:{self._handshake_port}{hs_path}',
                        data=body, method='POST',
                        headers={'Content-Type': 'application/json'},
                    )
                    with urllib.request.urlopen(req, timeout=8) as r:
                        handshake_result = json.loads(r.read().decode())
                        status = handshake_result.get('status', 'completed')
                        dispatched = True
            except Exception as exc:
                self.runtime.logger.warning('VANGUARD dispatch via HANDSHAKE failed: %s', exc)
                status = 'queued'

        record = {
            'message_id': msg_id,
            'channel': channel,
            'recipient': recipient,
            'content': content,
            'dispatched_at': _now(),
            'status': status,
            'dispatched': dispatched,
            **(handshake_result if dispatched else {}),
        }
        with self._lock:
            self._outbox.append(record)
            if len(self._outbox) > 200:
                self._outbox = self._outbox[-200:]

        self.runtime.logger.info(
            'VANGUARD outbound %s: %s via %s to %s',
            status, msg_id, channel, recipient
        )
        http_code = 200 if dispatched else 202
        return http_code, record

    def get_inbox(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        with self._lock:
            messages = [m.to_envelope() for m in self._inbox[-20:]]
        return 200, {'messages': messages, 'count': len(messages)}

    def get_outbox(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        with self._lock:
            messages = list(self._outbox[-20:])
        return 200, {'messages': messages, 'count': len(messages)}

    def start(self) -> None:
        self.runtime.logger.info('ZYRCON VANGUARD gateway active')
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='VANGUARD - Cascadia OS communication gateway')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    VanguardService(a.config, a.name).start()


if __name__ == '__main__':
    main()
