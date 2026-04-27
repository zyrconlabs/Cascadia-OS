from __future__ import annotations

import unittest
from unittest.mock import patch

from cascadia.licensing.license_gate import (
    OPERATOR_LIMITS,
    _build_status,
    _get_status,
    _cache,
)


class LicenseGateTests(unittest.TestCase):

    def setUp(self) -> None:
        # Reset cache before each test
        _cache['result'] = None
        _cache['expires_at'] = 0.0

    def test_valid_pro_license_returns_tier(self) -> None:
        key = 'ZYRCON-PRO-ABCDEF0123456789'
        result = _build_status(key)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'pro')
        self.assertEqual(result['operator_limit'], OPERATOR_LIMITS['pro'])
        self.assertIsNotNone(result['expires'])

    def test_invalid_key_returns_lite_tier(self) -> None:
        result = _build_status('not-a-valid-key')
        self.assertFalse(result['valid'])
        self.assertEqual(result['tier'], 'lite')
        self.assertEqual(result['operator_limit'], OPERATOR_LIMITS['lite'])
        self.assertIsNone(result['expires'])

    def test_missing_key_returns_lite_tier(self) -> None:
        result = _build_status(None)
        self.assertFalse(result['valid'])
        self.assertEqual(result['tier'], 'lite')
        self.assertEqual(result['operator_limit'], OPERATOR_LIMITS['lite'])

    def test_operator_limits_per_tier(self) -> None:
        cases = [
            ('ZYRCON-LITE-ABCDEF0123456789', 'lite', 2),
            ('ZYRCON-PRO-ABCDEF0123456789', 'pro', 6),
            ('ZYRCON-BUSINESS-ABCDEF0123456789', 'business', 12),
            ('ZYRCON-ENTERPRISE-ABCDEF0123456789', 'enterprise', 999),
        ]
        for key, expected_tier, expected_limit in cases:
            with self.subTest(tier=expected_tier):
                result = _build_status(key)
                self.assertEqual(result['tier'], expected_tier)
                self.assertEqual(result['operator_limit'], expected_limit)

    def test_cache_returns_same_result(self) -> None:
        with patch(
            'cascadia.licensing.license_gate._load_license_key',
            return_value='ZYRCON-PRO-ABCDEF0123456789',
        ) as mock_load:
            first = _get_status()
            second = _get_status()
            # Key loader should only be called once — second call uses cache
            mock_load.assert_called_once()
            self.assertEqual(first, second)
            self.assertEqual(first['tier'], 'pro')


if __name__ == '__main__':
    unittest.main()
