"""Tests for the public license_gate.py interface."""
from __future__ import annotations

import os
import unittest

from cascadia.licensing.license_gate import (
    _build_status,
    _get_status,
    OPERATOR_LIMITS,
    LICENSE_REGEX,
)

_PRO_KEY        = 'ZYRCON-PRO-1234567890abcdef'
_LITE_KEY       = 'ZYRCON-LITE-abcdef1234567890'
_BUSINESS_KEY   = 'ZYRCON-BUSINESS-0a1b2c3d4e5f6a7b'
_ENTERPRISE_KEY = 'ZYRCON-ENTERPRISE-fedcba9876543210'


class LicenseGateTests(unittest.TestCase):

    def test_valid_pro_key(self):
        result = _build_status(_PRO_KEY)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'pro')

    def test_valid_enterprise_key(self):
        result = _build_status(_ENTERPRISE_KEY)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'enterprise')

    def test_valid_lite_key(self):
        result = _build_status(_LITE_KEY)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'lite')

    def test_valid_business_key(self):
        result = _build_status(_BUSINESS_KEY)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'business')

    def test_no_key_returns_invalid_lite(self):
        result = _build_status(None)
        self.assertFalse(result['valid'])
        self.assertEqual(result['tier'], 'lite')

    def test_empty_key_invalid(self):
        result = _build_status('')
        self.assertFalse(result['valid'])

    def test_malformed_key_rejected(self):
        for bad in ('NOTAKEY', 'ZYRCON-ULTIMATE-1234567890abcdef',
                    'zyrcon-pro-1234567890abcdef', 'ZYRCON-PRO-tooshort'):
            with self.subTest(key=bad):
                self.assertFalse(_build_status(bad)['valid'])

    def test_operator_limit_in_result_matches_table(self):
        for tier, key in [('pro', _PRO_KEY), ('lite', _LITE_KEY),
                          ('business', _BUSINESS_KEY), ('enterprise', _ENTERPRISE_KEY)]:
            with self.subTest(tier=tier):
                result = _build_status(key)
                self.assertEqual(result['operator_limit'], OPERATOR_LIMITS[tier])

    def test_operator_limits_strictly_ascending(self):
        self.assertLess(OPERATOR_LIMITS['lite'],     OPERATOR_LIMITS['pro'])
        self.assertLess(OPERATOR_LIMITS['pro'],      OPERATOR_LIMITS['business'])
        self.assertLess(OPERATOR_LIMITS['business'], OPERATOR_LIMITS['enterprise'])

    def test_enterprise_operator_limit_is_large(self):
        result = _build_status(_ENTERPRISE_KEY)
        self.assertGreaterEqual(result['operator_limit'], 100)

    def test_lite_operator_limit_is_two(self):
        result = _build_status(_LITE_KEY)
        self.assertEqual(result['operator_limit'], 2)

    def test_expires_set_for_valid_key(self):
        result = _build_status(_PRO_KEY)
        self.assertIsNotNone(result['expires'])

    def test_expires_none_for_missing_key(self):
        result = _build_status(None)
        self.assertIsNone(result['expires'])

    def test_license_regex_accepts_all_tiers(self):
        for tier in ('LITE', 'PRO', 'BUSINESS', 'ENTERPRISE'):
            key = f'ZYRCON-{tier}-1234567890abcdef'
            self.assertIsNotNone(LICENSE_REGEX.match(key), f'{tier} should match regex')

    def test_license_regex_rejects_unknown_tier(self):
        self.assertIsNone(LICENSE_REGEX.match('ZYRCON-ULTIMATE-1234567890abcdef'))

    def test_license_regex_requires_16_hex_chars(self):
        self.assertIsNone(LICENSE_REGEX.match('ZYRCON-PRO-12345'))
        self.assertIsNone(LICENSE_REGEX.match('ZYRCON-PRO-1234567890abcdefXX'))

    def test_get_status_uses_env_var(self):
        import cascadia.licensing.license_gate as _lg
        os.environ['ZYRCON_LICENSE_KEY'] = _PRO_KEY
        _lg._cache['result'] = None  # bust the TTL cache
        try:
            result = _get_status()
            self.assertEqual(result['tier'], 'pro')
            self.assertTrue(result['valid'])
        finally:
            del os.environ['ZYRCON_LICENSE_KEY']
            _lg._cache['result'] = None

    def test_get_status_returns_required_keys(self):
        os.environ.pop('ZYRCON_LICENSE_KEY', None)
        result = _get_status()
        for key in ('valid', 'tier', 'operator_limit'):
            self.assertIn(key, result)


if __name__ == '__main__':
    unittest.main()
