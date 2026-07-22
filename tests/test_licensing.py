"""Tests for the public license_gate.py interface.

Format-C (ZYRCON-<TIER>-<hex>) was retired in S4a — the gate now accepts only
HMAC-signed Format-A keys, verified against the resolved signing secret. Keys are
signed in-process with a known test secret exported via LICENSE_SIGNING_SECRET
(which _resolve_signing_secret reads ahead of VAULT/config), keeping the suite
hermetic. The three LICENSE_REGEX tests were removed with the regex itself; their
behavioural intent (valid-tier acceptance, unknown-tier / malformed rejection) is
preserved below via Format-A keys and the malformed-rejection case.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from cascadia.licensing.license_gate import (
    _build_status,
    _get_status,
    OPERATOR_LIMITS,
)
from tests._v3_keys import bundle_file, ephemeral_keys, mint
from cascadia.licensing.tier_validator import TierValidator

_KEYS = ephemeral_keys()
_BUNDLE_PATH = bundle_file(_KEYS)   # for ZYRCON_LICENSE_KEYS_PATH
_FAR_EXPIRY = 4102444800  # 2100-01-01 UTC


def _key(tier: str) -> str:
    return mint(tier, 'unit-test', _FAR_EXPIRY, _KEYS)


class LicenseGateTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        os.environ['ZYRCON_LICENSE_KEYS_PATH'] = _BUNDLE_PATH

    @classmethod
    def tearDownClass(cls) -> None:
        os.environ.pop('ZYRCON_LICENSE_KEYS_PATH', None)

    def test_valid_pro_key(self):
        result = _build_status(_key('pro'))
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'pro')

    def test_valid_enterprise_key(self):
        result = _build_status(_key('enterprise'))
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'enterprise')

    def test_valid_lite_key(self):
        result = _build_status(_key('lite'))
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'lite')

    def test_valid_business_key(self):
        result = _build_status(_key('business'))
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
        # None of these are valid HMAC Format-A keys → all fail closed to invalid.
        # (Includes the old Format-C shapes, now just invalid strings.)
        for bad in ('NOTAKEY', 'ZYRCON-ULTIMATE-1234567890abcdef',
                    'ZYRCON-PRO-1234567890abcdef', 'zyrcon_pro_missing_sig'):
            with self.subTest(key=bad):
                self.assertFalse(_build_status(bad)['valid'])

    def test_forged_signature_rejected(self):
        # A structurally-correct Format-A key signed with the WRONG secret must
        # fail closed — this is the core HMAC guarantee that replaced regex parsing.
        # Signed by a DIFFERENT keypair — the shape is perfect, the signature is not.
        forged = mint('enterprise', 'x', _FAR_EXPIRY, ephemeral_keys())
        result = _build_status(forged)
        self.assertFalse(result['valid'])
        self.assertEqual(result['tier'], 'lite')

    def test_operator_limit_in_result_matches_table(self):
        for tier in ('pro', 'lite', 'business', 'enterprise'):
            with self.subTest(tier=tier):
                result = _build_status(_key(tier))
                self.assertEqual(result['operator_limit'], OPERATOR_LIMITS[tier])

    def test_operator_limits_strictly_ascending(self):
        self.assertLess(OPERATOR_LIMITS['lite'],     OPERATOR_LIMITS['pro'])
        self.assertLess(OPERATOR_LIMITS['pro'],      OPERATOR_LIMITS['business'])
        self.assertLess(OPERATOR_LIMITS['business'], OPERATOR_LIMITS['enterprise'])

    def test_enterprise_operator_limit_is_large(self):
        result = _build_status(_key('enterprise'))
        self.assertGreaterEqual(result['operator_limit'], 100)

    def test_lite_operator_limit_is_two(self):
        result = _build_status(_key('lite'))
        self.assertEqual(result['operator_limit'], 2)

    def test_expires_set_for_valid_key(self):
        result = _build_status(_key('pro'))
        self.assertIsNotNone(result['expires'])

    def test_expires_none_for_missing_key(self):
        result = _build_status(None)
        self.assertIsNone(result['expires'])

    def test_config_key_wins_over_env(self):
        # S4a config-primary: config.json license_key is authoritative; a stale
        # ZYRCON_LICENSE_KEY in env must be ignored when config has a key.
        # (Config read is mocked so the test never touches the live config.json.)
        import cascadia.licensing.license_gate as _lg
        cfg_key = _key('enterprise')
        with patch.object(_lg.Path, 'exists', return_value=True), \
             patch.object(_lg.Path, 'read_text', return_value=json.dumps({'license_key': cfg_key})):
            os.environ['ZYRCON_LICENSE_KEY'] = _key('pro')
            try:
                self.assertEqual(_lg._load_license_key(), cfg_key)  # config wins, env ignored
            finally:
                del os.environ['ZYRCON_LICENSE_KEY']

    def test_env_key_used_only_when_config_empty(self):
        # Deprecated backward-compat fallback: env is used only when config.json
        # has no license_key.
        import cascadia.licensing.license_gate as _lg
        env_key = _key('pro')
        with patch.object(_lg.Path, 'exists', return_value=True), \
             patch.object(_lg.Path, 'read_text', return_value=json.dumps({})):
            os.environ['ZYRCON_LICENSE_KEY'] = env_key
            try:
                self.assertEqual(_lg._load_license_key(), env_key)
            finally:
                del os.environ['ZYRCON_LICENSE_KEY']

    def test_get_status_returns_required_keys(self):
        os.environ.pop('ZYRCON_LICENSE_KEY', None)
        result = _get_status()
        for key in ('valid', 'tier', 'operator_limit'):
            self.assertIn(key, result)


if __name__ == '__main__':
    unittest.main()
