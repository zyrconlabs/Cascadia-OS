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


VALID_TIERS = (
    'lite',
    'pro',
    'pro_workspace',
    'business',
    'business_starter',
    'business_growth',
    'business_max',
    'enterprise',
)
_KEY_PREFIX = 'zyrcon_'

FEATURE_TIERS = {
    'workflow_viewer':    'lite',
    'workflow_designer':  'pro',
    'workflow_save':      'pro',
    'workflow_templates': 'pro',
    'workflow_team':      'enterprise',
    'fleet_management':   'enterprise',
    'audit_export':       'pro',
    'compliance_tab':     'enterprise',
    'iot_triggers':       'pro',
    'iot_actuators':      'enterprise',
}

TIER_RANKS = {
    'lite':             0,
    'pro':              1,
    'pro_workspace':    2,
    'business':         3,
    'business_starter': 3,
    'business_growth':  4,
    'business_max':     5,
    'enterprise':       6,
}


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

    def __init__(self, secret: str, tier: str = 'lite') -> None:
        self._secret = secret
        self._tier = tier

    def validate(self, key: str) -> Dict[str, Any]:
        """
        Validate a license key. Returns a dict with:
          valid, tier, customer_id, expiry_epoch, expires_at, days_remaining, error
        """
        if not key or not key.startswith(_KEY_PREFIX):
            return self._fail('Invalid key format')

        body = key[len(_KEY_PREFIX):]

        # Match tier first (sort longest to shortest to avoid prefix collisions).
        tier = None
        rest = ''
        for candidate in sorted(VALID_TIERS, key=len, reverse=True):
            if body.startswith(candidate + '_'):
                tier = candidate
                rest = body[len(candidate) + 1:]
                break

        if tier is None:
            # No known tier prefix matched — check if it looks like an unknown tier
            first_part = body.split('_')[0]
            return self._fail(f'Unknown tier: {first_part!r}')

        # rest = <customer_id>_<expiry>_<hmac>
        # hmac is last, expiry is second-to-last (numeric), customer_id is everything before
        parts = rest.split('_')
        if len(parts) < 3:
            return self._fail('Invalid key format — expected 4 segments')

        provided_hmac = parts[-1]
        expiry_str = parts[-2]
        customer_id = '_'.join(parts[:-2])

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

    def is_at_least(self, validated_result: dict, minimum_tier: str) -> bool:
        """Returns True if the validated license meets or exceeds the minimum_tier requirement."""
        tier = validated_result.get('tier', 'lite')
        return TIER_RANKS.get(tier, 0) >= TIER_RANKS.get(minimum_tier, 0)

    def can_access(self, feature: str) -> bool:
        """Return True if the current tier has access to the given feature."""
        required = FEATURE_TIERS.get(feature, 'lite')
        user_rank = TIER_RANKS.get(self._tier, 0)
        required_rank = TIER_RANKS.get(required, 0)
        return user_rank >= required_rank

    def feature_map(self) -> dict:
        """Return a dict of all features and whether the current tier can access them."""
        return {f: self.can_access(f) for f in FEATURE_TIERS}

    def generate(self, tier: str, customer_id: str, expiry_epoch: int) -> str:
        """Generate a valid license key. Used by generate_license.py script."""
        if tier not in VALID_TIERS:
            raise ValueError(f'Unknown tier: {tier!r}')
        message = f'{_KEY_PREFIX}{tier}_{customer_id}_{expiry_epoch}'
        sig = _sign(self._secret, message)
        return f'{message}_{sig}'
