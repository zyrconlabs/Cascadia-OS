from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from cascadia.licensing.license_gate import (
    OPERATOR_LIMITS,
    _build_status,
    _get_status,
    _cache,
)
from tests._v3_keys import bundle_file, ephemeral_keys, mint
from cascadia.licensing.tier_validator import TierValidator

# Format-C (ZYRCON-<TIER>-<hex>) was retired in S4a; the gate now accepts only
# HMAC-signed Format-A keys. These tests sign keys in-process with a known test
# secret exported via LICENSE_SIGNING_SECRET, which _resolve_signing_secret reads
# ahead of VAULT/config — keeping the suite hermetic (no live VAULT dependency).
_KEYS = ephemeral_keys()
_BUNDLE_PATH = bundle_file(_KEYS)   # for ZYRCON_LICENSE_KEYS_PATH
_FAR_EXPIRY = 4102444800  # 2100-01-01 UTC — well beyond any test run


def _key(tier: str) -> str:
    return mint(tier, 'unit-test', _FAR_EXPIRY, _KEYS)


class LicenseGateTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        os.environ['ZYRCON_LICENSE_KEYS_PATH'] = _BUNDLE_PATH

    @classmethod
    def tearDownClass(cls) -> None:
        os.environ.pop('ZYRCON_LICENSE_KEYS_PATH', None)

    def setUp(self) -> None:
        # Reset cache before each test
        _cache['result'] = None
        _cache['expires_at'] = 0.0

    def test_valid_pro_license_returns_tier(self) -> None:
        result = _build_status(_key('pro'))
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
            ('lite', 2),
            ('pro', 6),
            ('business', 12),
            ('enterprise', 999),
        ]
        for expected_tier, expected_limit in cases:
            with self.subTest(tier=expected_tier):
                result = _build_status(_key(expected_tier))
                self.assertEqual(result['tier'], expected_tier)
                self.assertEqual(result['operator_limit'], expected_limit)

    def test_cache_returns_same_result(self) -> None:
        with patch(
            'cascadia.licensing.license_gate._load_license_key',
            return_value=_key('pro'),
        ) as mock_load:
            first = _get_status()
            second = _get_status()
            # Key loader should only be called once — second call uses cache
            mock_load.assert_called_once()
            self.assertEqual(first, second)
            self.assertEqual(first['tier'], 'pro')


if __name__ == '__main__':
    unittest.main()
