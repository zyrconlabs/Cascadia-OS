"""
tier_validator.py — Cascadia OS Task 10
HMAC-SHA256 license key validation for Cascadia OS tier enforcement.

Key format:
  zyrcon_<tier>_<customer_id>_<expiry_epoch>_<hmac_hex>

Where hmac_hex = HMAC-SHA256(secret, "zyrcon_<tier>_<customer_id>_<expiry_epoch>")

Tiers: lite, pro, enterprise
"""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


VALID_TIERS = ('lite', 'pro', 'enterprise')
_KEY_PREFIX = 'zyrcon_'


def _sign(secret: str, message: str) -> str:
    return hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


class TierValidator:
    """
    Validates Cascadia OS license keys.
    Does not own key storage — reads secret from config at validation time.
    """

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def validate(self, key: str) -> Dict[str, Any]:
        """
        Validate a license key. Returns a dict with:
          valid, tier, customer_id, expiry_epoch, expires_at, days_remaining, error
        """
        if not key or not key.startswith(_KEY_PREFIX):
            return self._fail('Invalid key format')

        body = key[len(_KEY_PREFIX):]
        parts = body.split('_')
        if len(parts) < 4:
            return self._fail('Invalid key format — expected 4 segments')

        tier = parts[0]
        customer_id = parts[1]
        expiry_str = parts[2]
        provided_hmac = parts[3]

        if tier not in VALID_TIERS:
            return self._fail(f'Unknown tier: {tier!r}')

        try:
            expiry_epoch = int(expiry_str)
        except ValueError:
            return self._fail('Invalid expiry in key')

        message = f'{_KEY_PREFIX}{tier}_{customer_id}_{expiry_str}'
        expected = _sign(self._secret, message)
        if not hmac.compare_digest(expected, provided_hmac):
            return self._fail('Key signature invalid')

        now_epoch = int(time.time())
        days_remaining = max(0, (expiry_epoch - now_epoch) // 86400)
        expired = now_epoch > expiry_epoch
        expires_at = datetime.fromtimestamp(expiry_epoch, tz=timezone.utc).isoformat()

        if expired:
            return {
                'valid': False,
                'tier': tier,
                'customer_id': customer_id,
                'expiry_epoch': expiry_epoch,
                'expires_at': expires_at,
                'days_remaining': 0,
                'error': 'License expired',
            }

        return {
            'valid': True,
            'tier': tier,
            'customer_id': customer_id,
            'expiry_epoch': expiry_epoch,
            'expires_at': expires_at,
            'days_remaining': days_remaining,
            'error': None,
        }

    @staticmethod
    def _fail(error: str) -> Dict[str, Any]:
        return {
            'valid': False,
            'tier': 'lite',
            'customer_id': '',
            'expiry_epoch': 0,
            'expires_at': '',
            'days_remaining': 0,
            'error': error,
        }

    def generate(self, tier: str, customer_id: str, expiry_epoch: int) -> str:
        """Generate a valid license key. Used by generate_license.py script."""
        if tier not in VALID_TIERS:
            raise ValueError(f'Unknown tier: {tier!r}')
        message = f'{_KEY_PREFIX}{tier}_{customer_id}_{expiry_epoch}'
        sig = _sign(self._secret, message)
        return f'{message}_{sig}'
