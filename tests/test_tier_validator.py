"""Tests for TierValidator — HMAC key generation and validation."""
from __future__ import annotations

import hashlib
import hmac
import time
import unittest

from cascadia.licensing.tier_validator import (
    CURRENT_KEY_VERSION,
    TIER_RANKS,
    VALID_TIERS,
    TierValidator,
)

SECRET_V2 = 'e4806882a41883d35af2aa4ecfa20e89939b3cc53adf103e342f45e1e9661e4d'
SECRET_OLD = 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
FUTURE = int(time.time()) + 86400 * 365


def _make_v1_key(secret: str, tier: str, customer_id: str, expiry: int) -> str:
    """Build a v1-format key (old format, no key_version in payload)."""
    message = f'zyrcon_{tier}_{customer_id}_{expiry}'.encode()
    sig = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f'zyrcon_{tier}_{customer_id}_{expiry}_{sig}'


class TestTierValidatorConstants(unittest.TestCase):

    def test_current_version_is_v2(self) -> None:
        self.assertEqual(CURRENT_KEY_VERSION, 'v2')

    def test_tier_ranks_ordering(self) -> None:
        self.assertLess(TIER_RANKS['lite'], TIER_RANKS['pro'])
        self.assertLess(TIER_RANKS['pro'], TIER_RANKS['business'])
        self.assertLess(TIER_RANKS['business'], TIER_RANKS['enterprise'])

    def test_valid_tiers_complete(self) -> None:
        for tier in ('lite', 'pro', 'business', 'enterprise'):
            self.assertIn(tier, VALID_TIERS)


class TestTierValidatorGenerate(unittest.TestCase):

    def setUp(self) -> None:
        self.v = TierValidator(SECRET_V2)

    def test_generate_returns_v2_format(self) -> None:
        key = self.v.generate('pro', 'acme', FUTURE)
        parts = key.split('_')
        self.assertEqual(len(parts), 6)
        self.assertEqual(parts[0], 'zyrcon')
        self.assertEqual(parts[1], 'pro')
        self.assertEqual(parts[2], 'acme')
        self.assertEqual(parts[4], 'v2')

    def test_generate_then_validate_roundtrip(self) -> None:
        key = self.v.generate('enterprise', 'customer99', FUTURE)
        result = self.v.validate(key)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'enterprise')
        self.assertEqual(result['customer_id'], 'customer99')
        self.assertGreater(result['days_remaining'], 0)

    def test_generate_all_tiers(self) -> None:
        for tier in VALID_TIERS:
            key = self.v.generate(tier, 'testcust', FUTURE)
            result = self.v.validate(key)
            self.assertTrue(result['valid'], f'tier {tier} failed')
            self.assertEqual(result['tier'], tier)


class TestV1KeyRejection(unittest.TestCase):
    """After secret rotation, v1-format keys must be rejected regardless of signature correctness."""

    def setUp(self) -> None:
        self.v = TierValidator(SECRET_V2)

    def test_v1_key_with_old_secret_rejected(self) -> None:
        key = _make_v1_key(SECRET_OLD, 'pro', 'cust1', FUTURE)
        result = self.v.validate(key)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'key_version_rejected')

    def test_v1_key_with_new_secret_rejected(self) -> None:
        # Even if someone re-signs a v1 key with the new secret, version is still rejected
        key = _make_v1_key(SECRET_V2, 'enterprise', 'cust2', FUTURE)
        result = self.v.validate(key)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'key_version_rejected')


class TestInvalidSignature(unittest.TestCase):

    def setUp(self) -> None:
        self.v = TierValidator(SECRET_V2)

    def test_key_signed_with_wrong_secret_rejected(self) -> None:
        key = TierValidator(SECRET_OLD).generate('pro', 'cust3', FUTURE)
        result = self.v.validate(key)
        self.assertFalse(result['valid'])
        self.assertIn(result['error'], ('key_version_rejected', 'invalid_signature'))

    def test_tampered_tier_rejected(self) -> None:
        key = self.v.generate('pro', 'cust4', FUTURE)
        tampered = key.replace('_pro_', '_enterprise_', 1)
        result = self.v.validate(tampered)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'invalid_signature')

    def test_tampered_sig_rejected(self) -> None:
        key = self.v.generate('business', 'cust5', FUTURE)
        tampered = key[:-4] + 'ffff'
        result = self.v.validate(tampered)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'invalid_signature')


class TestExpiredKey(unittest.TestCase):

    def setUp(self) -> None:
        self.v = TierValidator(SECRET_V2)

    def test_expired_key_invalid(self) -> None:
        past = int(time.time()) - 86400 * 2  # expired 2 days ago
        key = self.v.generate('pro', 'cust6', past)
        result = self.v.validate(key)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'expired')
        self.assertGreaterEqual(result['days_expired'], 1)


class TestMalformedKeys(unittest.TestCase):

    def setUp(self) -> None:
        self.v = TierValidator(SECRET_V2)

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
