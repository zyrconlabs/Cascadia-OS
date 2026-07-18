"""Regression test for the readiness != licensed invariant (S4-FIX).

license_gate /health must be pure LIVENESS: it returns ok:true quickly and
exposes NO licensing state, EVEN when VAULT is unreachable and a Format-A key is
present. That is the data-dependent cold-boot case that deadlocked FLINT — the
2s health probe blocked on a not-yet-ready VAULT (license_gate tier 0 -> VAULT
tier 1). This test locks the invariant so a future change cannot re-couple
/health to secret resolution (the 3rd ordering regression in this area).

It FAILS against the pre-S4-FIX /health (which called _get_status and so blocked
on the VAULT read: the timing assertion trips and 'tier'/'valid' are present),
and PASSES after the fix.
"""
import json
import threading
import time
import unittest
import urllib.request as u
from unittest.mock import patch

from cascadia.licensing import license_gate as lg

# A Format-A shaped key (never actually verified here — resolution is mocked).
_FORMAT_A = 'zyrcon_enterprise_TESTCUST_9999999999_v2_' + 'ab' * 32


class HealthLivenessTest(unittest.TestCase):
    def setUp(self) -> None:
        lg._cache['result'] = None
        lg._cache['expires_at'] = 0.0
        self._srv = lg._ReusableServer(('127.0.0.1', 0), lg._Handler)
        self._port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        time.sleep(0.1)

    def tearDown(self) -> None:
        self._srv.shutdown()

    def _get(self, path: str) -> dict:
        with u.urlopen(f'http://127.0.0.1:{self._port}{path}', timeout=2) as r:
            return json.loads(r.read())

    def test_health_is_liveness_only_when_vault_unreachable(self) -> None:
        """ok:true, fast, and no licensing state — even with VAULT 'down'."""
        def slow_unreachable_vault():
            time.sleep(1.5)   # simulate a not-ready VAULT the resolver waits on
            return None

        with patch.object(lg, '_load_license_key', return_value=_FORMAT_A), \
             patch.object(lg, '_resolve_signing_secret_with_retry',
                          side_effect=slow_unreachable_vault):
            t0 = time.time()
            body = self._get('/api/health')
            elapsed = time.time() - t0

        self.assertTrue(body.get('ok'), 'liveness must report ok:true')
        self.assertLess(
            elapsed, 0.5,
            f'/health blocked on secret resolution ({elapsed:.2f}s) — it must '
            f'not read VAULT on the liveness path')
        self.assertNotIn('tier', body, '/health must expose no licensing state')
        self.assertNotIn('valid', body, '/health must expose no licensing state')

    def test_status_still_resolves_and_selfheals(self) -> None:
        """/status keeps the full resolve + S3.5 non-cache self-heal."""
        with patch.object(lg, '_load_license_key', return_value=_FORMAT_A), \
             patch.object(lg, '_resolve_signing_secret_with_retry', return_value=None):
            body = self._get('/api/license/status')
            self.assertEqual(body['tier'], 'lite')
            self.assertFalse(body['valid'])
            # transient indeterminate result must NOT be cached (S3.5)
            self.assertIsNone(lg._cache['result'])


if __name__ == '__main__':
    unittest.main()
