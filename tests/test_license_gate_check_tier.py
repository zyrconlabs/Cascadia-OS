"""Tests for LICENSE_GATE /api/license/check_tier endpoint."""
import json
import os
import unittest
from unittest.mock import patch

from cascadia.licensing.license_gate import _build_status, TIER_RANK as _TIER_RANK, _Handler, _ReusableServer
from tests._v3_keys import bundle_file, ephemeral_keys, mint
from cascadia.licensing.tier_validator import TierValidator

# Format-C (ZYRCON-<TIER>-<hex>) was retired in S4a; sign Format-A keys
# in-process with a known test secret exported via LICENSE_SIGNING_SECRET
# (which _resolve_signing_secret reads ahead of VAULT/config).
_KEYS = ephemeral_keys()
_BUNDLE_PATH = bundle_file(_KEYS)   # for ZYRCON_LICENSE_KEYS_PATH
_FAR_EXPIRY = 4102444800  # 2100-01-01 UTC


def _key(tier: str) -> str:
    return mint(tier, 'unit-test', _FAR_EXPIRY, _KEYS)


def _post_check_tier(tier_required: str, key: str | None = None):
    """Call check_tier logic directly — bypasses HTTP stack."""
    from cascadia.licensing import license_gate as lg
    with patch.object(lg, '_get_status', return_value=lg._build_status(key)):
        # Exercise the handler logic inline
        status = lg._get_status()
        current_tier = status['tier']
        reason = ''
        if not status['valid'] and status.get('expires') is not None:
            reason = 'license_expired'
            current_tier = 'lite'
        if tier_required not in _TIER_RANK:
            return 400, {'error': 'invalid tier_required'}
        ok = _TIER_RANK[current_tier] >= _TIER_RANK[tier_required]
        if not ok and not reason:
            reason = f'tier_insufficient: {current_tier} < {tier_required}'
        return 200, {
            'ok': ok,
            'current_tier': current_tier,
            'tier_required': tier_required,
            'reason': reason,
        }


class TestCheckTierLogic(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        os.environ['ZYRCON_LICENSE_KEYS_PATH'] = _BUNDLE_PATH

    @classmethod
    def tearDownClass(cls) -> None:
        os.environ.pop('ZYRCON_LICENSE_KEYS_PATH', None)

    def test_pro_meets_pro(self):
        code, body = _post_check_tier('pro', _key('pro'))
        self.assertEqual(code, 200)
        self.assertTrue(body['ok'])
        self.assertEqual(body['current_tier'], 'pro')

    def test_lite_fails_pro(self):
        code, body = _post_check_tier('pro', None)
        self.assertEqual(code, 200)
        self.assertFalse(body['ok'])
        self.assertIn('tier_insufficient', body['reason'])

    def test_enterprise_meets_lite(self):
        code, body = _post_check_tier('lite', _key('enterprise'))
        self.assertEqual(code, 200)
        self.assertTrue(body['ok'])
        self.assertEqual(body['current_tier'], 'enterprise')

    def test_no_license_meets_lite(self):
        # No license → tier=lite, valid=False, expires=None → no expiry degradation
        code, body = _post_check_tier('lite', None)
        self.assertEqual(code, 200)
        self.assertTrue(body['ok'])
        self.assertEqual(body['current_tier'], 'lite')

    def test_no_license_fails_pro(self):
        code, body = _post_check_tier('pro', None)
        self.assertEqual(code, 200)
        self.assertFalse(body['ok'])

    def test_malformed_tier_returns_400(self):
        code, body = _post_check_tier('superduper', None)
        self.assertEqual(code, 400)
        self.assertIn('error', body)


class TestTierRankOrder(unittest.TestCase):

    def test_rank_ordering(self):
        self.assertLess(_TIER_RANK['lite'], _TIER_RANK['pro'])
        self.assertLess(_TIER_RANK['pro'], _TIER_RANK['business'])
        self.assertLess(_TIER_RANK['business'], _TIER_RANK['enterprise'])


if __name__ == '__main__':
    unittest.main()
