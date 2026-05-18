"""
license_gate.py - Cascadia OS
LICENSE GATE: License validation and tier enforcement service.

Owns: license key parsing, format validation, tier resolution, operator limit
      enforcement, and license status caching.
Does not own: operator execution, capability checks, or payment processing.

Runs on 127.0.0.1:6100. Registers with CREW on startup.
"""
# MATURITY: PRODUCTION
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import request as urllib_request

from cascadia.shared.logger import get_logger

logger = get_logger('license_gate')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PORT = 6100
CREW_URL = "http://127.0.0.1:5100"

LICENSE_REGEX = re.compile(
    r'^ZYRCON-(LITE|PRO|BUSINESS|ENTERPRISE)-([0-9A-Fa-f]{16})$'
)

OPERATOR_LIMITS: Dict[str, int] = {
    'lite':       2,
    'pro':        6,
    'business':   12,
    'enterprise': 999,
}

TIER_RANK: Dict[str, int] = {
    'lite':       0,
    'pro':        1,
    'business':   2,
    'enterprise': 3,
}

ENTITLEMENT_PROFILES: Dict[str, Any] = {
    'lite': {
        'tier': 'lite',
        'features': {
            'basic_prism':       True,
            'crew':              True,
            'vault':             True,
            'depot':             True,
            'paid_operators':    False,
            'enterprise_prism':  False,
            'multi_user':        False,
            'advanced_audit':    False,
            'workflow_sharing':  False,
            'scheduled_reports': False,
        },
        'limits': {
            'max_operators':         2,
            'max_workflows_per_day': 10,
            'max_connectors':        1,
        },
    },
    'pro': {
        'tier': 'pro',
        'features': {
            'basic_prism':       True,
            'crew':              True,
            'vault':             True,
            'depot':             True,
            'paid_operators':    True,
            'enterprise_prism':  False,
            'multi_user':        False,
            'advanced_audit':    False,
            'workflow_sharing':  False,
            'scheduled_reports': False,
        },
        'limits': {
            'max_operators':         6,
            'max_workflows_per_day': 500,
            'max_connectors':        5,
        },
    },
    'business': {
        'tier': 'business',
        'features': {
            'basic_prism':       True,
            'crew':              True,
            'vault':             True,
            'depot':             True,
            'paid_operators':    True,
            'enterprise_prism':  False,
            'multi_user':        True,
            'advanced_audit':    True,
            'workflow_sharing':  True,
            'scheduled_reports': True,
        },
        'limits': {
            'max_operators':         12,
            'max_workflows_per_day': 5000,
            'max_connectors':        20,
        },
    },
    'enterprise': {
        'tier': 'enterprise',
        'features': {
            'basic_prism':       True,
            'crew':              True,
            'vault':             True,
            'depot':             True,
            'paid_operators':    True,
            'enterprise_prism':  True,
            'multi_user':        True,
            'advanced_audit':    True,
            'workflow_sharing':  True,
            'scheduled_reports': True,
        },
        'limits': {
            'max_operators':         999,
            'max_workflows_per_day': 100000,
            'max_connectors':        999,
        },
    },
}

CACHE_TTL = 60  # seconds

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_cache: Dict[str, Any] = {
    'result':     None,
    'expires_at': 0.0,
}

# ---------------------------------------------------------------------------
# License resolution
# ---------------------------------------------------------------------------

def _load_license_key() -> Optional[str]:
    """Return license key from env var or config.json, or None."""
    # 1. Environment variable takes precedence
    env_key = os.environ.get('ZYRCON_LICENSE_KEY', '').strip()
    if env_key:
        return env_key

    # 2. config.json at repo root (2 levels up from this file's package)
    config_path = Path(__file__).parents[2] / 'config.json'
    if not config_path.exists():
        config_path = Path(__file__).parent / 'config.json'
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            key = cfg.get('license_key', '').strip()
            if key:
                return key
        except Exception:
            pass

    return None


def _build_status(key: Optional[str]) -> Dict[str, Any]:
    """Validate license key and return status dict."""
    today = date.today()
    expires = (today + timedelta(days=365)).isoformat()

    if not key:
        return {
            'valid':          False,
            'tier':           'lite',
            'operator_limit': OPERATOR_LIMITS['lite'],
            'expires':        None,
        }

    m = LICENSE_REGEX.match(key)
    if not m:
        return {
            'valid':          False,
            'tier':           'lite',
            'operator_limit': OPERATOR_LIMITS['lite'],
            'expires':        None,
        }

    tier = m.group(1).lower()
    return {
        'valid':          True,
        'tier':           tier,
        'operator_limit': OPERATOR_LIMITS.get(tier, 2),
        'expires':        expires,
    }


def _get_status() -> Dict[str, Any]:
    """Return cached license status, refreshing if expired."""
    now = time.time()
    if _cache['result'] is not None and now < _cache['expires_at']:
        return _cache['result']

    key = _load_license_key()
    result = _build_status(key)
    _cache['result'] = result
    _cache['expires_at'] = now + CACHE_TTL
    return result


def _get_current_tier() -> str:
    """Returns current tier string. Default is 'lite'. Never raises."""
    try:
        return _get_status()['tier']
    except Exception:
        return 'lite'


# ---------------------------------------------------------------------------
# CREW registration
# ---------------------------------------------------------------------------

def _register_with_crew() -> None:
    manifest = {
        'name':        'license_gate',
        'port':        PORT,
        'version':     '0.43',
        'description': 'License validation and tier enforcement',
        'capabilities': ['license.read'],
        'routes': [
            {'method': 'GET',  'path': '/api/license/status'},
            {'method': 'GET',  'path': '/api/license/entitlement'},
            {'method': 'POST', 'path': '/api/license/check_tier'},
            {'method': 'GET',  'path': '/api/health'},
        ],
    }
    try:
        body = json.dumps(manifest).encode('utf-8')
        req = urllib_request.Request(
            f'{CREW_URL}/register',
            data=body,
            method='POST',
            headers={'Content-Type': 'application/json'},
        )
        with urllib_request.urlopen(req, timeout=3) as r:
            logger.info('registered with CREW: %s', r.status)
    except Exception as exc:
        logger.warning('could not register with CREW: %s', exc)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split('?')[0]
        if path == '/api/license/status':
            tier = _get_current_tier()
            profile = ENTITLEMENT_PROFILES[tier]
            status = _get_status()
            self._send_json(200, {
                **status,
                'limits':   profile['limits'],
                'features': profile['features'],
            })
        elif path == '/api/license/entitlement':
            tier = _get_current_tier()
            profile = ENTITLEMENT_PROFILES[tier]
            key = _load_license_key()
            status = _get_status()
            self._send_json(200, {
                **profile,
                'key_present': bool(key),
                'valid':       status['valid'] or not bool(key),
            })
        elif path in ('/api/health', '/health'):
            status = _get_status()
            self._send_json(200, {
                'component': 'license_gate',
                'status':    'ok',
                'ok':        True,
                'port':      PORT,
                'tier':      status['tier'],
                'valid':     status['valid'],
            })
        else:
            self._send_json(404, {'error': f'unknown route: {path}'})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split('?')[0]
        if path == '/api/license/check_tier':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length)) if length else {}
            except Exception:
                body = {}
            tier_required = body.get('tier_required', 'lite')
            current_tier = _get_current_tier()
            profile = ENTITLEMENT_PROFILES[current_tier]
            allowed = (
                TIER_RANK.get(current_tier, 0) >=
                TIER_RANK.get(tier_required, 0)
            )
            self._send_json(200, {
                'ok':            allowed,
                'allowed':       allowed,
                'tier':          current_tier,
                'tier_required': tier_required,
                'limit':         profile['limits']['max_operators'],
                'features':      profile['features'],
                'reason':        None if allowed else 'tier_insufficient',
            })
        else:
            self._send_json(404, {'error': f'unknown route: {path}'})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info(fmt, *args)


class _ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True
    allow_reuse_port    = True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Pre-warm cache so first request is instant
    status = _get_status()
    logger.info(
        'starting — tier=%s valid=%s port=%d',
        status['tier'], status['valid'], PORT,
    )

    # Register with CREW (non-blocking — failure is logged, not fatal)
    _register_with_crew()

    server = _ReusableServer(('127.0.0.1', PORT), _Handler)
    logger.info('listening on 127.0.0.1:%d', PORT)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info('shutting down')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
