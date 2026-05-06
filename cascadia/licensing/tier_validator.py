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

VALID_TIERS = ('lite', 'pro', 'pro_workspace', 'business', 'enterprise')

TIER_RANKS: Dict[str, int] = {
    'lite':          0,
    'pro':           1,
    'pro_workspace': 2,
    'business':      3,
    'enterprise':    4,
}

TIER_MAX_USERS: Dict[str, int] = {
    'lite':             1,
    'pro':              1,
    'pro_workspace':    3,
    'business_starter': 5,
    'business_growth':  10,
    'business_max':     999,
    'business':         5,
    'enterprise':       999,
}


def get_max_users(tier: str) -> int:
    """Return the maximum number of users allowed for a given tier."""
    return TIER_MAX_USERS.get(tier, 1)

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

        # Match tier by prefix (handles tiers that contain underscores, e.g. pro_workspace)
        tier = None
        for t in sorted(VALID_TIERS, key=len, reverse=True):
            if key.startswith(f'zyrcon_{t}_'):
                tier = t
                break
        if not tier:
            return {'valid': False, 'error': 'invalid_tier'}

        # remainder is everything after 'zyrcon_{tier}_'
        remainder_parts = key[len(f'zyrcon_{tier}_'):].split('_')

        # v2+: [...customer_id..., expiry, version_tag, sig]
        # v1:  [...customer_id..., expiry, sig]
        if len(remainder_parts) >= 4 and _is_version_tag(remainder_parts[-2]):
            sig = remainder_parts[-1]
            key_version = remainder_parts[-2]
            expiry_str = remainder_parts[-3]
            customer_id = '_'.join(remainder_parts[:-3])
        elif len(remainder_parts) >= 3:
            sig = remainder_parts[-1]
            expiry_str = remainder_parts[-2]
            customer_id = '_'.join(remainder_parts[:-2])
            key_version = 'v1'
        else:
            return {'valid': False, 'error': 'invalid_format'}

        if not customer_id:
            return {'valid': False, 'error': 'invalid_format'}

        if key_version != self._key_version:
            return {'valid': False, 'error': 'key_version_rejected',
                    'key_version': key_version, 'expected': self._key_version}

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
