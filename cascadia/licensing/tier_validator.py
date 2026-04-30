"""
tier_validator.py — Cascadia OS
HMAC license key generation and validation.
Owns: key format, HMAC signing, expiry checking, version gating.
Does not own: key storage, email delivery, Stripe events.

Key format (v2):
    zyrcon_{tier}_{customer_id}_{expiry_epoch}_{key_version}_{hmac_sha256}

Old format (v1, rejected after rotation):
    zyrcon_{tier}_{customer_id}_{expiry_epoch}_{hmac_sha256}
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Dict

CURRENT_KEY_VERSION = 'v2'

VALID_TIERS = ('lite', 'pro', 'business', 'enterprise')

TIER_RANKS: Dict[str, int] = {
    'lite':       0,
    'pro':        1,
    'business':   2,
    'enterprise': 3,
}

_VERSION_PREFIX = 'v'


def _is_version_tag(s: str) -> bool:
    return s.startswith(_VERSION_PREFIX) and s[1:].isdigit()


class TierValidator:
    """Generates and validates HMAC-signed license keys."""

    def __init__(self, secret: str, key_version: str = CURRENT_KEY_VERSION) -> None:
        self._secret = secret
        self._key_version = key_version

    def generate(self, tier: str, customer_id: str, expiry: int) -> str:
        """Return a signed key string. expiry is a Unix epoch timestamp."""
        message = f'zyrcon_{tier}_{customer_id}_{expiry}_{self._key_version}'.encode()
        sig = hmac.new(self._secret.encode(), message, hashlib.sha256).hexdigest()
        return f'zyrcon_{tier}_{customer_id}_{expiry}_{self._key_version}_{sig}'

    def validate(self, key: str) -> Dict[str, Any]:
        """
        Parse and cryptographically verify a license key.
        Returns dict with 'valid' bool and details, or 'error' on failure.
        """
        if not key or not key.startswith('zyrcon_'):
            return {'valid': False, 'error': 'invalid_format'}

        parts = key.split('_')

        # Distinguish v1 (5 parts) from v2+ (6+ parts with version tag)
        if len(parts) == 6 and _is_version_tag(parts[4]):
            _, tier, customer_id, expiry_str, key_version, sig = parts
        elif len(parts) == 5:
            # v1 format — no version tag; reject after secret rotation
            key_version = 'v1'
            _, tier, customer_id, expiry_str, sig = parts
        else:
            return {'valid': False, 'error': 'invalid_format'}

        if key_version != self._key_version:
            return {'valid': False, 'error': 'key_version_rejected',
                    'key_version': key_version, 'expected': self._key_version}

        if tier not in VALID_TIERS:
            return {'valid': False, 'error': 'invalid_tier'}

        try:
            expiry_ts = int(expiry_str)
        except ValueError:
            return {'valid': False, 'error': 'invalid_expiry'}

        # HMAC verification
        if key_version == 'v1':
            message = f'zyrcon_{tier}_{customer_id}_{expiry_str}'.encode()
        else:
            message = f'zyrcon_{tier}_{customer_id}_{expiry_str}_{key_version}'.encode()
        expected = hmac.new(self._secret.encode(), message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return {'valid': False, 'error': 'invalid_signature'}

        now = int(time.time())
        if expiry_ts < now:
            return {'valid': False, 'error': 'expired',
                    'days_expired': (now - expiry_ts) // 86400}

        return {
            'valid':         True,
            'tier':          tier,
            'customer_id':   customer_id,
            'expires_at':    expiry_ts,
            'days_remaining': (expiry_ts - now) // 86400,
        }
