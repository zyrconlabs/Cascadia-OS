"""
cascadia_sdk.py — Cascadia OS SDK v0.47
Stdlib-only helper functions for operator developers.
Provides type-safe wrappers for VAULT, SENTINEL, BEACON, and CREW APIs.

All functions are safe to call even when Cascadia OS components are not running —
they catch all exceptions and return safe defaults.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional
from urllib import request as _urllib_request

# Internal API key — set by Watchdog if security.internal_api_key_required is true
_KEY: str = os.environ.get('CASCADIA_INTERNAL_KEY', '')

# Default component ports — match config.example.json
_PORTS: Dict[str, int] = {
    'vault':    5101,
    'sentinel': 5102,
    'beacon':   6200,
    'crew':     5100,
}


def _headers() -> Dict[str, str]:
    """Build request headers, including the internal API key if configured."""
    h = {'Content-Type': 'application/json'}
    if _KEY:
        h['X-Cascadia-Key'] = _KEY
    return h


def _post(port: int, path: str, payload: Dict[str, Any], timeout: int = 3) -> Optional[Dict[str, Any]]:
    """POST JSON to a local component port. Returns parsed response or None on failure."""
    try:
        data = json.dumps(payload).encode('utf-8')
        req = _urllib_request.Request(
            f'http://127.0.0.1:{port}{path}',
            data=data, method='POST',
            headers=_headers(),
        )
        with _urllib_request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception:
        return None


def _get(port: int, path: str, timeout: int = 3) -> Optional[Dict[str, Any]]:
    """GET from a local component port. Returns parsed response or None on failure."""
    try:
        req = _urllib_request.Request(
            f'http://127.0.0.1:{port}{path}',
            headers=_headers(),
        )
        with _urllib_request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception:
        return None


def vault_store(key: str, value: str) -> bool:
    """
    Store a value in VAULT under the given key.
    Returns True on success, False on failure.
    """
    try:
        result = _post(_PORTS['vault'], '/write', {'key': key, 'value': value})
        return bool(result and result.get('written', False))
    except Exception:
        return False


def vault_get(key: str) -> Optional[str]:
    """
    Retrieve a value from VAULT by key.
    Returns the value string or None if not found or on error.
    """
    try:
        result = _post(_PORTS['vault'], '/read', {'key': key})
        if result and 'value' in result:
            return result['value']
        return None
    except Exception:
        return None


def sentinel_check(action: str, context: Optional[Dict[str, Any]] = None) -> bool:
    """
    Check whether an action is permitted by SENTINEL risk policy.
    Returns True if allowed, False if denied.
    Fail-closed: returns False on any exception (connection error, timeout, etc.).
    """
    try:
        result = _post(_PORTS['sentinel'], '/check', {
            'action': action,
            'context': context or {},
        })
        if result is None:
            return False  # fail-closed
        return bool(result.get('allowed', False))
    except Exception:
        return False  # fail-closed — never permit on error


def beacon_route(target: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Route a message to a target operator via BEACON.
    Returns the response dict, or an empty dict on failure.
    """
    try:
        result = _post(_PORTS['beacon'], '/route', {
            'target': target,
            'message': payload,
        })
        return result or {}
    except Exception:
        return {}


def crew_register(manifest: Dict[str, Any]) -> bool:
    """
    Register this operator with CREW on startup.
    Returns True on success, False on failure.
    Failure is non-fatal — the operator continues running unregistered.
    """
    try:
        result = _post(_PORTS['crew'], '/register', {
            'operator_id': manifest.get('id', ''),
            'type':         manifest.get('type', 'service'),
            'autonomy_level': manifest.get('autonomy_level', 'assistive'),
            'capabilities': manifest.get('capabilities', []),
            'health_hook':  manifest.get('health_hook', '/health'),
            'version':      manifest.get('version', ''),
            'name':         manifest.get('name', ''),
        })
        return bool(result and result.get('ok', False))
    except Exception:
        return False
