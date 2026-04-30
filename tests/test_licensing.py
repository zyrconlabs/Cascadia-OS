from __future__ import annotations

import time
import unittest

from cascadia.licensing.tier_validator import TierValidator, VALID_TIERS, TIER_RANKS


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

    def test_business_starter_validates(self) -> None:
        key = self._make_key('business_starter', 'acme')
        result = self.v.validate(key)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'business_starter')

    def test_business_growth_validates(self) -> None:
        key = self._make_key('business_growth', 'acme')
        result = self.v.validate(key)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'business_growth')

    def test_business_max_validates(self) -> None:
        key = self._make_key('business_max', 'acme')
        result = self.v.validate(key)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'business_max')

    def test_pro_workspace_validates(self) -> None:
        key = self._make_key('pro_workspace', 'acme')
        result = self.v.validate(key)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'pro_workspace')

    def test_is_at_least_business_starter_above_pro(self) -> None:
        key = self._make_key('business_starter', 'acme')
        result = self.v.validate(key)
        self.assertTrue(self.v.is_at_least(result, 'pro'))

    def test_is_at_least_lite_below_business(self) -> None:
        key = self._make_key('lite', 'acme')
        result = self.v.validate(key)
        self.assertFalse(self.v.is_at_least(result, 'business'))

    def test_unknown_tier_in_key_fails_validation(self) -> None:
        # Manually craft a key with an unknown tier (bypassing generate validation)
        import hashlib
        import hmac as _hmac
        secret = self.SECRET
        tier = 'ultimate'
        customer = 'acme'
        expiry = int(time.time()) + 86400
        message = f'zyrcon_{tier}_{customer}_{expiry}'
        sig = _hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        bad_key = f'{message}_{sig}'
        result = self.v.validate(bad_key)
        self.assertFalse(result['valid'])
        self.assertIn('Unknown tier', result['error'])


if __name__ == '__main__':
    unittest.main()
