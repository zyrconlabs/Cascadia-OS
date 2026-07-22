"""Tests for TierValidator — Ed25519 (v3) license key validation.

TierValidator is verify-only, so keys are minted here with an ephemeral
keypair via tests/_v3_keys.py rather than through the validator.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import unittest

from tests._v3_keys import ephemeral_keys, mint

from cascadia.licensing.tier_validator import (
    CURRENT_KEY_VERSION,
    TIER_MAX_USERS,
    TIER_RANKS,
    VALID_TIERS,
    TierValidator,
    get_max_users,
)

KEYS = ephemeral_keys()          # the 'real' signer for these tests
OTHER_KEYS = ephemeral_keys()    # a different signer — must never validate
SECRET_OLD = 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'  # v1 HMAC fixture
FUTURE = int(time.time()) + 86400 * 365


def _make_v1_key(secret: str, tier: str, customer_id: str, expiry: int) -> str:
    """Build a v1-format key (old format, no key_version in payload)."""
    message = f'zyrcon_{tier}_{customer_id}_{expiry}'.encode()
    sig = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f'zyrcon_{tier}_{customer_id}_{expiry}_{sig}'


class TestTierValidatorConstants(unittest.TestCase):

    def test_current_version_is_v3(self) -> None:
        self.assertEqual(CURRENT_KEY_VERSION, 'v3')

    def test_tier_ranks_ordering(self) -> None:
        self.assertLess(TIER_RANKS['lite'], TIER_RANKS['pro'])
        self.assertLess(TIER_RANKS['pro'], TIER_RANKS['business'])
        self.assertLess(TIER_RANKS['business'], TIER_RANKS['enterprise'])

    def test_valid_tiers_complete(self) -> None:
        for tier in ('lite', 'pro', 'business', 'enterprise'):
            self.assertIn(tier, VALID_TIERS)

    def test_pro_workspace_in_valid_tiers(self) -> None:
        self.assertIn('pro_workspace', VALID_TIERS)

    def test_pro_workspace_rank(self) -> None:
        self.assertGreater(TIER_RANKS['pro_workspace'], TIER_RANKS['pro'])
        self.assertLess(TIER_RANKS['pro_workspace'], TIER_RANKS['business'])
        self.assertEqual(TIER_RANKS['pro_workspace'], 2)

    def test_pro_workspace_max_users(self) -> None:
        self.assertEqual(get_max_users('pro_workspace'), 3)
        self.assertEqual(get_max_users('lite'), 1)
        self.assertEqual(get_max_users('pro'), 1)
        self.assertEqual(get_max_users('business'), 10)
        self.assertEqual(get_max_users('enterprise'), 999)
        self.assertEqual(get_max_users('unknown_tier'), 1)


class TestTierValidatorGenerate(unittest.TestCase):

    def setUp(self) -> None:
        self.v = TierValidator(KEYS.public_b64)

    def test_minted_key_is_v3_format(self) -> None:
        key = mint('pro', 'acme', FUTURE, KEYS)
        parts = key.split('_')
        self.assertEqual(len(parts), 6)
        self.assertEqual(parts[0], 'zyrcon')
        self.assertEqual(parts[1], 'pro')
        self.assertEqual(parts[2], 'acme')
        self.assertEqual(parts[4], 'v3')

    def test_generate_then_validate_roundtrip(self) -> None:
        key = mint('enterprise', 'customer99', FUTURE, KEYS)
        result = self.v.validate(key)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'enterprise')
        self.assertEqual(result['customer_id'], 'customer99')
        self.assertGreater(result['days_remaining'], 0)

    def test_generate_all_tiers(self) -> None:
        for tier in VALID_TIERS:
            key = mint(tier, 'testcust', FUTURE, KEYS)
            result = self.v.validate(key)
            self.assertTrue(result['valid'], f'tier {tier} failed')
            self.assertEqual(result['tier'], tier)


class TestV1KeyRejection(unittest.TestCase):
    """After secret rotation, v1-format keys must be rejected regardless of signature correctness."""

    def setUp(self) -> None:
        self.v = TierValidator(KEYS.public_b64)

    def test_v1_key_with_old_secret_rejected(self) -> None:
        key = _make_v1_key(SECRET_OLD, 'pro', 'cust1', FUTURE)
        result = self.v.validate(key)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'key_version_rejected')

    def test_v1_key_with_new_secret_rejected(self) -> None:
        # Even if someone re-signs a v1 key with the new secret, version is still rejected
        key = _make_v1_key(SECRET_OLD, 'enterprise', 'cust2', FUTURE)
        result = self.v.validate(key)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'key_version_rejected')


class TestInvalidSignature(unittest.TestCase):

    def setUp(self) -> None:
        self.v = TierValidator(KEYS.public_b64)

    def test_key_signed_with_wrong_secret_rejected(self) -> None:
        key = mint('pro', 'cust3', FUTURE, OTHER_KEYS)
        result = self.v.validate(key)
        self.assertFalse(result['valid'])
        self.assertIn(result['error'], ('key_version_rejected', 'invalid_signature'))

    def test_tampered_tier_rejected(self) -> None:
        key = mint('pro', 'cust4', FUTURE, KEYS)
        tampered = key.replace('_pro_', '_enterprise_', 1)
        result = self.v.validate(tampered)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'invalid_signature')

    def test_tampered_sig_rejected(self) -> None:
        key = mint('business', 'cust5', FUTURE, KEYS)
        tampered = key[:-4] + 'ffff'
        result = self.v.validate(tampered)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'invalid_signature')


class TestExpiredKey(unittest.TestCase):

    def setUp(self) -> None:
        self.v = TierValidator(KEYS.public_b64)

    def test_expired_key_invalid(self) -> None:
        past = int(time.time()) - 86400 * 2  # expired 2 days ago
        key = mint('pro', 'cust6', past, KEYS)
        result = self.v.validate(key)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'expired')
        self.assertGreaterEqual(result['days_expired'], 1)


class TestMalformedKeys(unittest.TestCase):

    def setUp(self) -> None:
        self.v = TierValidator(KEYS.public_b64)

    def test_empty_string(self) -> None:
        self.assertFalse(self.v.validate('')['valid'])

    def test_random_string(self) -> None:
        self.assertFalse(self.v.validate('not-a-key')['valid'])

    def test_too_few_parts(self) -> None:
        self.assertFalse(self.v.validate('zyrcon_pro')['valid'])

    def test_too_many_parts(self) -> None:
        self.assertFalse(self.v.validate('zyrcon_a_b_c_d_e_f_g')['valid'])


if __name__ == '__main__':
    unittest.main()
