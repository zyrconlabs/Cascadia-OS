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

import argparse
import json
import os
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import request as urllib_request

from cascadia.licensing.tier_validator import TierValidator
from cascadia.shared.logger import get_logger

logger = get_logger('license_gate')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PORT = 6100
CREW_URL = "http://127.0.0.1:5100"

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

# Bounded retry for signing-secret resolution. Protects against the cold-boot
# race where the gate polls before VAULT (:5101) is listening: a transient
# resolution failure must NOT get cached as a fail-closed Lite result.
# Total sleep budget = 0.4 + 0.8 + 1.2 = 2.4s (< ~3s) so a poll never hangs.
SECRET_RESOLVE_ATTEMPTS = 4
SECRET_RESOLVE_BACKOFF  = 0.4  # seconds; linear escalation (backoff * attempt)

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


def _resolve_signing_secret() -> Optional[str]:
    """Resolve the HMAC signing secret using the SAME order as the mint path
    (cascadia/core/stripe_webhook.py): env LICENSE_SIGNING_SECRET > VAULT
    (op email_operator, key license_secret, ns default) > config.json
    license_secret. Returns None if none resolves (gate then cannot verify
    signatures — callers must fail closed, never grant off an unverified key).
    """
    # 1. Environment variable
    env_secret = os.environ.get('LICENSE_SIGNING_SECRET', '').strip()
    if env_secret:
        return env_secret

    # 2. VAULT read (email_operator / license_secret / default)
    try:
        payload = json.dumps({
            'operator_id': 'email_operator',
            'key':         'license_secret',
            'namespace':   'default',
        }).encode('utf-8')
        req = urllib_request.Request(
            'http://127.0.0.1:5101/read',
            data=payload,
            headers={'Content-Type': 'application/json'},
        )
        with urllib_request.urlopen(req, timeout=3) as r:
            vault_secret = (json.loads(r.read()).get('value', '') or '').strip()
            if vault_secret:
                return vault_secret
    except Exception:
        pass

    # 3. config.json license_secret (repo root, 2 levels up from this package)
    config_path = Path(__file__).parents[2] / 'config.json'
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            cfg_secret = (cfg.get('license_secret', '') or '').strip()
            if cfg_secret:
                return cfg_secret
        except Exception:
            pass

    return None


def _resolve_signing_secret_with_retry() -> Optional[str]:
    """Resolve the signing secret with a bounded retry, for the cold-boot race.

    env/config lookups are synchronous and return on the first attempt; the
    only thing that gets retried is the VAULT read — i.e. exactly the case
    where VAULT (:5101) is not yet listening at boot. Returns the secret, or
    None if it still could not be resolved within the sleep budget (the caller
    then treats the result as INDETERMINATE and does not cache it).
    """
    for attempt in range(1, SECRET_RESOLVE_ATTEMPTS + 1):
        secret = _resolve_signing_secret()
        if secret:
            return secret
        if attempt < SECRET_RESOLVE_ATTEMPTS:
            time.sleep(SECRET_RESOLVE_BACKOFF * attempt)
    return None


def _lite_status() -> Dict[str, Any]:
    """Fail-closed status: invalid, lite tier, no expiry."""
    return {
        'valid':          False,
        'tier':           'lite',
        'operator_limit': OPERATOR_LIMITS['lite'],
        'expires':        None,
    }


def _build_status(key: Optional[str]) -> Dict[str, Any]:
    """Validate a license key and return status dict.

    Ordered so that SECRET-INDEPENDENT determinations resolve first; only a
    Format-A key actually needs the signing secret. Each returned status is
    either DEFINITIVE (safe to cache for the full TTL) or INDETERMINATE
    (marked non-cacheable via an internal '_cacheable' flag that _get_status
    strips before returning — see below):

      a) no key                       → definitive lite
      b) Format A shape:
           secret resolved + valid    → definitive tier
           secret resolved + bad sig  → definitive lite (a real determination)
           secret NOT resolved        → INDETERMINATE lite (non-cacheable, retry)

    Format C (ZYRCON-<TIER>-<hex>) was retired in S4a — such a string is now
    just an invalid Format-A key → lite.

    Fail-closed everywhere: an unresolved secret or invalid key never yields a
    tier above the key's literal/lite floor.
    """
    fallback_expires = (date.today() + timedelta(days=365)).isoformat()

    # a) No key present → definitive fail-closed lite. (cacheable)
    if not key:
        return _lite_status()

    # b) Format A shape — HMAC verification requires the signing secret.
    #    (Format C dual-accept was retired in S4a: an old ZYRCON-<TIER>-<hex>
    #    string is now just an invalid Format-A key and falls through to lite.)
    secret = _resolve_signing_secret_with_retry()

    if not secret:
        # INDETERMINATE: secret could not be resolved (e.g. VAULT not yet ready
        # at cold boot). Fail closed for THIS call, but DO NOT cache it — the
        # next poll re-evaluates and self-heals once the secret is reachable,
        # instead of freezing Enterprise→Lite for the full 60s TTL.
        logger.warning(
            'license signing secret unresolved; indeterminate; not caching; '
            'next poll will re-evaluate (last4=%s)',
            key[-4:],
        )
        status = _lite_status()
        status['_cacheable'] = False
        return status

    # Secret resolved → definitive determination either way.
    try:
        result = TierValidator(secret).validate(key)
    except Exception as exc:
        logger.warning('tier_validator error (last4=%s): %s', key[-4:], exc)
        result = {'valid': False}
    if result.get('valid'):
        tier = result.get('tier', 'lite')
        # Map into a tier the gate serves an entitlement profile for;
        # unknown/higher-granularity tiers fail closed to lite.
        if tier not in ENTITLEMENT_PROFILES:
            tier = 'lite'
        exp_ts = result.get('expires_at')
        try:
            expires = (date.fromtimestamp(exp_ts).isoformat()
                       if exp_ts else fallback_expires)
        except Exception:
            expires = fallback_expires
        logger.info('license valid (HMAC v2) — tier=%s last4=%s', tier, key[-4:])
        return {
            'valid':          True,
            'tier':           tier,
            'operator_limit': OPERATOR_LIMITS.get(tier, 2),
            'expires':        expires,
        }

    # Secret resolved but signature invalid / expired → DEFINITIVE lite.
    # (A real determination, NOT transient — safe to cache.)
    logger.info('license rejected (HMAC verify failed) — last4=%s', key[-4:])
    return _lite_status()


def _get_status() -> Dict[str, Any]:
    """Return cached license status, refreshing if expired.

    Only DEFINITIVE results are stored for the full TTL. An INDETERMINATE
    result (Format-A key whose signing secret could not be resolved) carries
    an internal '_cacheable': False flag; it is returned for this call but
    NEVER cached, so the next poll re-evaluates and self-heals. The flag is
    stripped before returning so the HTTP response shape is byte-identical.
    """
    now = time.time()
    if _cache['result'] is not None and now < _cache['expires_at']:
        return _cache['result']

    key = _load_license_key()
    result = _build_status(key)
    cacheable = result.pop('_cacheable', True)
    if cacheable:
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
                'valid':       status['valid'],
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

def _pulse_loop(pulse_file: Path) -> None:
    """Write timestamp to pulse_file every 5 s so FLINT knows we're alive."""
    while True:
        try:
            pulse_file.parent.mkdir(parents=True, exist_ok=True)
            pulse_file.write_text(str(time.time()))
        except Exception:
            pass
        time.sleep(5)


def main() -> None:
    p = argparse.ArgumentParser(description='LICENSE_GATE — Cascadia OS tier enforcement')
    p.add_argument('--config', default='')
    p.add_argument('--name', default='license_gate')
    args = p.parse_args()

    # Resolve pulse_file from config.json if provided
    pulse_file: Optional[Path] = None
    if args.config:
        try:
            cfg = json.loads(Path(args.config).read_text())
            for comp in cfg.get('components', []):
                if comp.get('name') == args.name:
                    pf = comp.get('pulse_file', '')
                    if pf:
                        pulse_file = Path(pf)
                    break
        except Exception:
            pass
    if pulse_file is None:
        pulse_file = Path(f'./data/runtime/{args.name}.pulse')

    # Pre-warm cache so first request is instant
    status = _get_status()
    logger.info(
        'starting — tier=%s valid=%s port=%d',
        status['tier'], status['valid'], PORT,
    )

    threading.Thread(target=_pulse_loop, args=(pulse_file,), daemon=True,
                     name='license-gate-pulse').start()

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
