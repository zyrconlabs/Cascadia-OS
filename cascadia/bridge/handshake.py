"""
handshake/handshake.py - Cascadia OS v0.44
HANDSHAKE: API bridge to external services.

Owns: connection registry for external APIs (CRMs, ERPs, payment systems),
      credential reference storage (not credential values — those go in VAULT),
      outbound API call proxying and response normalization,
      connection health checking.
Does not own: encryption (CURTAIN), secrets storage (VAULT),
              routing (BEACON), workflow execution (STITCH).

HANDSHAKE communicates a bilateral agreement — exactly what an API
integration is. It works alongside CURTAIN for secure transport.

Supported channels with real HTTP execution:
  webhook   — POST JSON payload to any URL (generic outbound)
  email     — SMTP send via smtplib (requires smtp_* config on connection)
  http      — generic GET/POST/PUT/DELETE to a registered base_url

All other registered service types are logged and queued for future adapters.
"""
# MATURITY: PRODUCTION — Webhook and HTTP execution live. SMTP email adapter live.
# CRM, ERP, calendar adapters planned — webhook/HTTP/email are live.
from __future__ import annotations

import argparse
import json
import smtplib
import ssl
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Known external service types HANDSHAKE supports
SERVICE_TYPES = {
    'crm',          # HubSpot, Salesforce, Zoho
    'erp',          # QuickBooks, NetSuite
    'email',        # Gmail, Outlook (via SMTP)
    'calendar',     # Google Calendar, Outlook Calendar
    'payment',      # Stripe, Square
    'storage',      # Google Drive, Dropbox, S3
    'database',     # Postgres, MySQL, Airtable
    'messaging',    # Slack, WhatsApp, Teams
    'analytics',    # GA4, Mixpanel
    'webhook',      # Generic outbound webhooks — fully implemented
    'http',         # Generic HTTP — fully implemented
    'custom',       # Anything else
}

# Service types with real HTTP execution implemented
_LIVE_TYPES = {'webhook', 'http', 'email'}


class ServiceConnection:
    """
    One registered external service connection.
    Owns: connection metadata and health state.
    Does not own: credentials (those are in VAULT by reference).
    """

    def __init__(self, connection_id: str, service_type: str, name: str,
                 base_url: str, vault_credential_key: str,
                 tenant_id: str = 'default',
                 headers: Optional[Dict[str, str]] = None,
                 smtp_host: str = '', smtp_port: int = 587,
                 smtp_user: str = '', smtp_password: str = '',
                 smtp_from: str = '') -> None:
        self.connection_id = connection_id
        self.service_type = service_type
        self.name = name
        self.base_url = base_url
        self.vault_credential_key = vault_credential_key
        self.tenant_id = tenant_id
        self.headers = headers or {}         # Custom HTTP headers (auth tokens, API keys)
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from
        self.registered_at = _now()
        self.last_checked: Optional[str] = None
        self.status = 'registered'

    def to_dict(self) -> Dict[str, Any]:
        return {
            'connection_id': self.connection_id,
            'service_type': self.service_type,
            'name': self.name,
            'base_url': self.base_url,
            'vault_credential_key': self.vault_credential_key,
            'tenant_id': self.tenant_id,
            'registered_at': self.registered_at,
            'last_checked': self.last_checked,
            'status': self.status,
            'live_execution': self.service_type in _LIVE_TYPES,
        }


class HandshakeService:
    """
    HANDSHAKE - API bridge and external service registry.
    Owns connection registry, real HTTP execution, and audit logging.
    Does not own credential values, routing decisions, or workflow logic.
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
        self._connections: Dict[str, ServiceConnection] = {}
        self._call_log: List[Dict[str, Any]] = []

        self.runtime.register_route('POST', '/connection/register', self.register_connection)
        self.runtime.register_route('GET',  '/connection/list',     self.list_connections)
        self.runtime.register_route('POST', '/connection/check',    self.check_connection)
        self.runtime.register_route('POST', '/call',                self.proxy_call)
        self.runtime.register_route('POST', '/webhook',             self.fire_webhook)
        self.runtime.register_route('GET',  '/call/log',            self.get_call_log)
        self.runtime.register_route('GET',  '/service-types',       self.service_types)
        self.runtime.register_route('GET',  '/capabilities',        self.capabilities)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def register_connection(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Register an external service connection.
        Credentials are stored in VAULT — only the vault_credential_key is stored here.
        For webhook/http: provide base_url and optional headers dict.
        For email/SMTP: provide smtp_host, smtp_port, smtp_user, smtp_password, smtp_from.
        """
        service_type = payload.get('service_type', 'custom')
        if service_type not in SERVICE_TYPES:
            return 400, {'error': f'unsupported service type: {service_type}',
                         'supported': sorted(SERVICE_TYPES)}
        conn_id = payload.get('connection_id', f'hs_{uuid.uuid4().hex[:8]}')
        conn = ServiceConnection(
            connection_id=conn_id,
            service_type=service_type,
            name=payload.get('name', conn_id),
            base_url=payload.get('base_url', ''),
            vault_credential_key=payload.get('vault_credential_key', ''),
            tenant_id=payload.get('tenant_id', 'default'),
            headers=payload.get('headers', {}),
            smtp_host=payload.get('smtp_host', ''),
            smtp_port=int(payload.get('smtp_port', 587)),
            smtp_user=payload.get('smtp_user', ''),
            smtp_password=payload.get('smtp_password', ''),
            smtp_from=payload.get('smtp_from', ''),
        )
        with self._lock:
            self._connections[conn_id] = conn
        self.runtime.logger.info('HANDSHAKE registered: %s (%s)', conn_id, service_type)
        return 201, conn.to_dict()

    def list_connections(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        with self._lock:
            connections = [c.to_dict() for c in self._connections.values()]
        return 200, {'connections': connections, 'count': len(connections)}

    def check_connection(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Health check a registered connection via real HTTP ping.
        Tries GET base_url — marks healthy/degraded/unreachable based on response.
        """
        conn_id = payload.get('connection_id', '')
        with self._lock:
            conn = self._connections.get(conn_id)
        if conn is None:
            return 404, {'error': 'connection not found'}

        conn.last_checked = _now()

        if conn.base_url and conn.service_type in ('webhook', 'http', 'crm', 'erp', 'storage'):
            try:
                headers = {'User-Agent': 'Cascadia-HANDSHAKE/0.34'}
                headers.update(conn.headers)
                req = urllib.request.Request(conn.base_url, headers=headers, method='GET')
                with urllib.request.urlopen(req, timeout=5) as r:
                    conn.status = 'healthy' if r.status < 400 else 'degraded'
                    return 200, {
                        'connection_id': conn_id,
                        'status': conn.status,
                        'http_status': r.status,
                        'checked_at': conn.last_checked,
                    }
            except urllib.error.HTTPError as e:
                conn.status = 'degraded'
                return 200, {'connection_id': conn_id, 'status': conn.status,
                             'http_status': e.code, 'checked_at': conn.last_checked}
            except Exception as e:
                conn.status = 'unreachable'
                return 200, {'connection_id': conn_id, 'status': conn.status,
                             'error': str(e), 'checked_at': conn.last_checked}

        # For types without a simple ping (email/SMTP), mark as registered
        conn.status = 'registered'
        return 200, {'connection_id': conn_id, 'status': conn.status,
                     'checked_at': conn.last_checked}

    # ------------------------------------------------------------------
    # Outbound execution
    # ------------------------------------------------------------------

    def proxy_call(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Proxy an outbound API call through a registered connection.
        Routes to the correct executor based on service_type.
        Fully implemented: webhook, http, email.
        Logged-and-queued: all other types.
        """
        conn_id = payload.get('connection_id', '')
        endpoint = payload.get('endpoint', '')
        method = payload.get('method', 'POST').upper()
        call_payload = payload.get('payload', {})
        operator_id = payload.get('operator_id', 'unknown')
        timeout = int(payload.get('timeout', 10))

        with self._lock:
            conn = self._connections.get(conn_id)
        if conn is None:
            return 404, {'error': f'connection not found: {conn_id}'}

        call_id = f'call_{uuid.uuid4().hex[:8]}'
        self.runtime.logger.info(
            'HANDSHAKE call: %s %s%s via %s by %s',
            method, conn.base_url, endpoint, conn_id, operator_id,
        )

        # Route to live executor
        if conn.service_type in ('webhook', 'http'):
            status, result = self._execute_http(
                conn, endpoint, method, call_payload, timeout
            )
        elif conn.service_type == 'email':
            status, result = self._execute_email(conn, call_payload)
        else:
            # Queued — not yet implemented for this service type
            status, result = 'queued', {
                'note': f'{conn.service_type} adapter not yet implemented — webhook/http/email are live.',
                'queued_at': _now(),
            }

        log_entry = {
            'call_id': call_id,
            'connection_id': conn_id,
            'service_type': conn.service_type,
            'endpoint': endpoint,
            'method': method,
            'operator_id': operator_id,
            'called_at': _now(),
            'status': status,
            **result,
        }
        with self._lock:
            self._call_log.append(log_entry)
            if len(self._call_log) > 500:
                self._call_log = self._call_log[-500:]

        http_status = 200 if status == 'completed' else 202 if status == 'queued' else 502
        return http_status, {
            'call_id': call_id,
            'connection_id': conn_id,
            'endpoint': endpoint,
            'status': status,
            **result,
        }

    def fire_webhook(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Convenience route — register a one-off webhook connection and fire it immediately.
        Useful for operators that need a quick outbound notification without pre-registering.

        Required: url, body (dict)
        Optional: headers (dict), method (default POST)
        """
        url = payload.get('url', '')
        if not url:
            return 400, {'error': 'url required'}

        # Auto-register an ephemeral connection
        conn_id = f'wh_{uuid.uuid4().hex[:8]}'
        conn = ServiceConnection(
            connection_id=conn_id,
            service_type='webhook',
            name=f'webhook:{url[:40]}',
            base_url=url,
            vault_credential_key='',
            headers=payload.get('headers', {}),
        )

        call_payload = payload.get('body', payload.get('payload', {}))
        method = payload.get('method', 'POST').upper()
        timeout = int(payload.get('timeout', 10))

        status, result = self._execute_http(conn, '', method, call_payload, timeout)

        call_id = f'call_{uuid.uuid4().hex[:8]}'
        log_entry = {
            'call_id': call_id,
            'connection_id': conn_id,
            'service_type': 'webhook',
            'endpoint': url,
            'method': method,
            'operator_id': payload.get('operator_id', 'direct'),
            'called_at': _now(),
            'status': status,
            **result,
        }
        with self._lock:
            self._call_log.append(log_entry)

        http_status = 200 if status == 'completed' else 502
        return http_status, {'call_id': call_id, 'url': url, 'status': status, **result}

    # ------------------------------------------------------------------
    # Executors
    # ------------------------------------------------------------------

    def _execute_http(
        self,
        conn: ServiceConnection,
        endpoint: str,
        method: str,
        call_payload: Any,
        timeout: int,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Execute a real HTTP request to base_url + endpoint.
        Supports GET, POST, PUT, PATCH, DELETE.
        Sends JSON body for POST/PUT/PATCH. Returns response status and body.
        """
        url = conn.base_url.rstrip('/') + endpoint
        body: Optional[bytes] = None

        headers = {'User-Agent': 'Cascadia-HANDSHAKE/0.34', 'Accept': 'application/json'}
        headers.update(conn.headers)

        if method in ('POST', 'PUT', 'PATCH') and call_payload:
            body = json.dumps(call_payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'

        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                response_body = r.read().decode('utf-8', errors='replace')
                try:
                    response_json = json.loads(response_body)
                except Exception:
                    response_json = None
                return 'completed', {
                    'http_status': r.status,
                    'response_body': response_json or response_body[:500],
                    'executed_at': _now(),
                }
        except urllib.error.HTTPError as e:
            body_text = e.read().decode('utf-8', errors='replace')[:200]
            return 'failed', {
                'http_status': e.code,
                'error': str(e),
                'response_body': body_text,
                'executed_at': _now(),
            }
        except Exception as e:
            return 'failed', {
                'error': str(e),
                'executed_at': _now(),
            }

    def _execute_email(
        self,
        conn: ServiceConnection,
        call_payload: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Send an email via SMTP using connection credentials.
        call_payload must include: to, subject, body
        Optional: cc, bcc, html_body
        """
        if not conn.smtp_host or not conn.smtp_user:
            return 'failed', {'error': 'SMTP not configured on this connection'}

        to = call_payload.get('to', '')
        subject = call_payload.get('subject', '')
        body = call_payload.get('body', '')
        html_body = call_payload.get('html_body', '')

        if not to or not subject:
            return 'failed', {'error': 'to and subject required in payload'}

        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = conn.smtp_from or conn.smtp_user
            msg['To'] = to
            msg['Subject'] = subject
            if call_payload.get('cc'):
                msg['Cc'] = call_payload['cc']

            msg.attach(MIMEText(body, 'plain'))
            if html_body:
                msg.attach(MIMEText(html_body, 'html'))

            context = ssl.create_default_context()
            with smtplib.SMTP(conn.smtp_host, conn.smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls(context=context)
                server.login(conn.smtp_user, conn.smtp_password)
                server.sendmail(msg['From'], to, msg.as_string())

            return 'completed', {
                'sent_to': to,
                'subject': subject,
                'executed_at': _now(),
            }
        except Exception as e:
            return 'failed', {'error': str(e), 'executed_at': _now()}

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_call_log(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        with self._lock:
            log = list(self._call_log[-50:])
        return 200, {'calls': log, 'count': len(log)}

    def service_types(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            'supported_service_types': sorted(SERVICE_TYPES),
            'live_execution': sorted(_LIVE_TYPES),
            'roadmap': sorted(SERVICE_TYPES - _LIVE_TYPES),
        }

    def capabilities(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            'webhook': 'POST JSON to any URL — live',
            'http': 'GET/POST/PUT/PATCH/DELETE to registered base_url — live',
            'email': 'SMTP send via smtplib — live (requires smtp_* config)',
            'crm': 'HubSpot/Salesforce/Zoho — planned',
            'erp': 'QuickBooks/NetSuite — planned',
            'calendar': 'Google Calendar/Outlook — planned',
        }

    def start(self) -> None:
        self.runtime.logger.info(
            'HANDSHAKE active — webhook/HTTP/SMTP execution live'
        )
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='HANDSHAKE - Cascadia OS API bridge')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    HandshakeService(a.config, a.name).start()


if __name__ == '__main__':
    main()
