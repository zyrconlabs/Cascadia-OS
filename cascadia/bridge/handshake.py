"""
handshake/handshake.py - Cascadia OS v0.2
HANDSHAKE: API bridge to external services.

Owns: connection registry for external APIs (CRMs, ERPs, payment systems),
      credential reference storage (not credential values — those go in VAULT),
      outbound API call proxying and response normalization,
      connection health checking.
Does not own: encryption (CURTAIN), secrets storage (VAULT),
              routing (BEACON), workflow execution (STITCH).

HANDSHAKE communicates a bilateral agreement — exactly what an API
integration is. It works alongside CURTAIN for secure transport.
"""
# MATURITY: STUB — Connection registry and call logging work. Actual HTTP execution to external APIs is v0.3.
from __future__ import annotations

import argparse
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Known external service types HANDSHAKE supports
SERVICE_TYPES = {
    'crm',          # HubSpot, Salesforce, Zoho
    'erp',          # QuickBooks, NetSuite
    'email',        # Gmail, Outlook (via OAuth)
    'calendar',     # Google Calendar, Outlook Calendar
    'payment',      # Stripe, Square
    'storage',      # Google Drive, Dropbox, S3
    'database',     # Postgres, MySQL, Airtable
    'messaging',    # Slack, WhatsApp, Teams
    'analytics',    # GA4, Mixpanel
    'webhook',      # Generic outbound webhooks
    'custom',       # Anything else
}


class ServiceConnection:
    """
    One registered external service connection.
    Owns: connection metadata and health state.
    Does not own: credentials (those are in VAULT by reference).
    """

    def __init__(self, connection_id: str, service_type: str, name: str,
                 base_url: str, vault_credential_key: str,
                 tenant_id: str = 'default') -> None:
        self.connection_id = connection_id
        self.service_type = service_type
        self.name = name
        self.base_url = base_url
        self.vault_credential_key = vault_credential_key  # Key in VAULT, not the credential itself
        self.tenant_id = tenant_id
        self.registered_at = _now()
        self.last_checked: Optional[str] = None
        self.status = 'registered'   # registered / healthy / degraded / unreachable

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
        }


class HandshakeService:
    """
    HANDSHAKE - API bridge and external service registry.
    Owns connection registry and proxy routing.
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
        self.runtime.register_route('GET',  '/call/log',            self.get_call_log)
        self.runtime.register_route('GET',  '/service-types',       self.service_types)

    def register_connection(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Register an external service connection.
        Credentials are stored in VAULT — only the vault_credential_key is stored here.
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
        Health check a registered connection.
        In v0.2: marks status and returns. Real ping in v0.3.
        """
        conn_id = payload.get('connection_id', '')
        with self._lock:
            conn = self._connections.get(conn_id)
        if conn is None:
            return 404, {'error': 'connection not found'}
        conn.last_checked = _now()
        conn.status = 'healthy'   # v0.3: actual HTTP ping
        return 200, {'connection_id': conn_id, 'status': conn.status, 'checked_at': conn.last_checked}

    def proxy_call(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Proxy an outbound API call through a registered connection.
        Logs the call. In v0.2: records intent. Real HTTP execution in v0.3.
        Operators call HANDSHAKE — HANDSHAKE owns the connection details.
        """
        conn_id = payload.get('connection_id', '')
        endpoint = payload.get('endpoint', '')
        method = payload.get('method', 'GET').upper()
        call_payload = payload.get('payload', {})
        operator_id = payload.get('operator_id', 'unknown')

        with self._lock:
            conn = self._connections.get(conn_id)
        if conn is None:
            return 404, {'error': f'connection not found: {conn_id}'}

        call_id = f'call_{uuid.uuid4().hex[:8]}'
        log_entry = {
            'call_id': call_id,
            'connection_id': conn_id,
            'service_type': conn.service_type,
            'endpoint': endpoint,
            'method': method,
            'operator_id': operator_id,
            'called_at': _now(),
            'status': 'logged',   # v0.3: 'completed' / 'failed' after real execution
        }
        with self._lock:
            self._call_log.append(log_entry)
            if len(self._call_log) > 500:
                self._call_log = self._call_log[-500:]

        self.runtime.logger.info(
            'HANDSHAKE call: %s %s%s via %s by %s',
            method, conn.base_url, endpoint, conn_id, operator_id,
        )
        return 202, {
            'call_id': call_id,
            'connection_id': conn_id,
            'endpoint': endpoint,
            'status': 'queued',
            'note': 'Real execution in v0.3 — logged for audit in v0.2',
        }

    def get_call_log(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        with self._lock:
            log = list(self._call_log[-50:])
        return 200, {'calls': log, 'count': len(log)}

    def service_types(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {'supported_service_types': sorted(SERVICE_TYPES)}

    def start(self) -> None:
        self.runtime.logger.info('HANDSHAKE API bridge active')
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='HANDSHAKE - Cascadia OS API bridge')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    HandshakeService(a.config, a.name).start()


if __name__ == '__main__':
    main()
