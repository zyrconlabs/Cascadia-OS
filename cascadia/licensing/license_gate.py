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

from cascadia.licensing.tier_validator import TierValidator, load_key_bundle
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

# NOTE: the bounded signing-secret retry that used to live here is gone. It
# existed for the cold-boot race where the gate polled before VAULT (:5101) was
# listening. v3 verifies against a PUBLIC key bundle shipped inside the package,
# so there is no runtime fetch and no race to absorb.

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
    """Return the license KEY, with config.json AUTHORITATIVE.

    Precedence (config-primary, S4a):
      1. config.json license_key present → use it. If ZYRCON_LICENSE_KEY is
         also set and DIFFERS, config wins and env is ignored (redacted
         deprecation warning) — so a config re-key can never again be silently
         overridden by a stale .env.
      2. only env ZYRCON_LICENSE_KEY set (config empty) → use env with a
         redacted deprecation warning (backward-tolerant transition).
      3. neither → None (lite).

    NOTE: this is the KEY loader only — the licence key itself, which is not
    secret. The PUBLIC verify keys come from _resolve_verify_keys().
    """
    env_key = os.environ.get('ZYRCON_LICENSE_KEY', '').strip()

    # config.json at repo root (2 levels up from this file's package)
    config_path = Path(__file__).parents[2] / 'config.json'
    if not config_path.exists():
        config_path = Path(__file__).parent / 'config.json'
    cfg_key = ''
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            cfg_key = cfg.get('license_key', '').strip()
        except Exception:
            cfg_key = ''

    # 1. config.json is authoritative when present
    if cfg_key:
        if env_key and env_key != cfg_key:
            logger.warning(
                'ZYRCON_LICENSE_KEY (env, last4=%s) differs from config.json '
                'license_key (last4=%s); config wins, env ignored (deprecated)',
                env_key[-4:], cfg_key[-4:],
            )
        return cfg_key

    # 2. env-only fallback (config empty) — deprecated transition path
    if env_key:
        logger.warning(
            'using ZYRCON_LICENSE_KEY from env (last4=%s); config.json '
            'license_key is empty — env as a KEY source is deprecated, move it '
            'into config.json', env_key[-4:],
        )
        return env_key

    # 3. neither → no key
    return None


def _resolve_verify_keys() -> dict:
    """Load the PUBLIC key bundle used to verify v3 licence keys.

    Order: env ZYRCON_LICENSE_KEYS_PATH (testing/rotation escape hatch) > the
    bundle shipped inside the package. Returns {} when nothing is readable, and
    the caller then treats the result as INDETERMINATE rather than granting.

    There is no secret and no VAULT round-trip here. Under the old symmetric
    HMAC scheme the verify secret was also the SIGNING secret, so it could never
    ship and had to be fetched at runtime — which is what created the cold-boot
    race the retry helper existed for. A public key is not a secret: it ships in
    the payload and is readable the moment the process starts.
    """
    env_path = os.environ.get('ZYRCON_LICENSE_KEYS_PATH', '').strip()
    if env_path:
        bundle = load_key_bundle(env_path)
        if bundle:
            return bundle
        logger.warning('ZYRCON_LICENSE_KEYS_PATH set but unreadable: %s', env_path)
    return load_key_bundle()


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

    # b) Ed25519 v3 verification against the PUBLIC key bundle that ships with
    #    the product. No secret, no VAULT round-trip, and therefore no cold-boot
    #    race: the bundle is a file inside the package and is readable from the
    #    moment the process starts.
    validator = TierValidator(_resolve_verify_keys())

    if not validator.has_keys:
        # INDETERMINATE: this node cannot verify anything (bundle missing or
        # unreadable). Fail closed for THIS call, but DO NOT cache it, so the
        # node self-heals if the bundle becomes readable rather than freezing
        # Enterprise→Lite for the full 60s TTL. Should never happen on a normal
        # install — the bundle is part of the payload.
        logger.warning(
            'license verify key bundle unavailable; indeterminate; not caching; '
            'next poll will re-evaluate (last4=%s)',
            key[-4:],
        )
        status = _lite_status()
        status['_cacheable'] = False
        return status

    # Verify keys present → definitive determination either way.
    try:
        result = validator.validate(key)
    except Exception as exc:
        # validate() is contracted never to raise; belt-and-braces so a future
        # change cannot turn a crash into a grant.
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
        logger.info('license valid (Ed25519 v3) — tier=%s last4=%s', tier, key[-4:])
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
            # LIVENESS ONLY — must NOT touch licensing state: no _get_status,
            # no secret resolution, no VAULT read on this path. Answering the
            # moment the HTTP server is bound deletes the FLINT readiness cycle
            # license_gate(tier 0) -> VAULT(tier 1): the 2s health probe can no
            # longer block on a not-yet-ready VAULT. Tier/valid live on /status
            # and /entitlement, which still resolve lazily and self-heal (S3.5
            # non-cache) once VAULT is up. readiness != licensed.
            self._send_json(200, {
                'component': 'license_gate',
                'status':    'ok',
                'ok':        True,
                'port':      PORT,
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
