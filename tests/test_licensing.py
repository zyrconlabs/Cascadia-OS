from __future__ import annotations

import time
import unittest

from cascadia.licensing.tier_validator import TierValidator, VALID_TIERS


class TierValidatorTests(unittest.TestCase):
    SECRET = 'test_secret_abc123'

    def setUp(self) -> None:
        self.v = TierValidator(self.SECRET)

    def _make_key(self, tier: str = 'pro', customer: str = 'acme',
                  days: int = 365) -> str:
        expiry = int(time.time()) + days * 86400
        return self.v.generate(tier, customer, expiry)

    def test_valid_pro_key(self) -> None:
        key = self._make_key('pro', 'acme')
        result = self.v.validate(key)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'pro')
        self.assertEqual(result['customer_id'], 'acme')
        self.assertIsNone(result['error'])

    def test_valid_enterprise_key(self) -> None:
        key = self._make_key('enterprise', 'zyrcon')
        result = self.v.validate(key)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'enterprise')

    def test_valid_lite_key(self) -> None:
        key = self._make_key('lite', 'demo')
        result = self.v.validate(key)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'lite')

    def test_expired_key_invalid(self) -> None:
        expiry = int(time.time()) - 86400  # 1 day ago
        key = self.v.generate('pro', 'acme', expiry)
        result = self.v.validate(key)
        self.assertFalse(result['valid'])
        self.assertIn('expired', result['error'].lower())

    def test_tampered_key_rejected(self) -> None:
        key = self._make_key('pro', 'acme')
        tampered = key[:-4] + 'xxxx'
        result = self.v.validate(tampered)
        self.assertFalse(result['valid'])

    def test_wrong_secret_rejected(self) -> None:
        key = self._make_key('pro', 'acme')
        other = TierValidator('different_secret')
        result = other.validate(key)
        self.assertFalse(result['valid'])

    def test_empty_key_rejected(self) -> None:
        result = self.v.validate('')
        self.assertFalse(result['valid'])

    def test_unknown_tier_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.v.generate('ultimate', 'acme', int(time.time()) + 86400)

    def test_all_valid_tiers(self) -> None:
        for tier in VALID_TIERS:
            key = self._make_key(tier, 'test')
            result = self.v.validate(key)
            self.assertTrue(result['valid'], f'{tier} key should be valid')

    def test_days_remaining_approx(self) -> None:
        key = self._make_key('pro', 'acme', days=30)
        result = self.v.validate(key)
        self.assertGreaterEqual(result['days_remaining'], 29)
        self.assertLessEqual(result['days_remaining'], 30)


if __name__ == '__main__':
    unittest.main()
