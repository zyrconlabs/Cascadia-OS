"""Integration tests for CREW tier gate using LICENSE_GATE check_tier."""
import json
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import URLError


def _simulate_check_tier(ok: bool, reason: str = ''):
    """Return a mock urlopen context manager that yields a fake LICENSE_GATE entitlement response."""
    mock_response = MagicMock()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({
        'tier': 'pro' if ok else 'lite',
        'features': {'paid_operators': ok},
        'limits': {'max_operators': 6 if ok else 2},
    }).encode()
    return mock_response


class TestCrewTierGate(unittest.TestCase):

    def _get_check_tier(self):
        from cascadia.registry import crew
        return crew._check_tier

    def test_sufficient_tier_returns_true(self):
        check_tier = self._get_check_tier()
        with patch('cascadia.registry.crew._urllib_request.urlopen',
                   return_value=_simulate_check_tier(True)):
            ok, reason = check_tier({}, 'pro')
        self.assertTrue(ok)
        self.assertEqual(reason, 'pro')

    def test_insufficient_tier_returns_false(self):
        check_tier = self._get_check_tier()
        with patch('cascadia.registry.crew._urllib_request.urlopen',
                   return_value=_simulate_check_tier(False, 'tier_insufficient: lite < pro')):
            ok, reason = check_tier({}, 'pro')
        self.assertFalse(ok)
        self.assertIn('tier_insufficient', reason)

    def test_license_gate_down_fails_closed(self):
        """When LICENSE_GATE is unreachable, CREW must deny — not allow."""
        check_tier = self._get_check_tier()
        with patch('cascadia.registry.crew._urllib_request.urlopen',
                   side_effect=URLError('connection refused')):
            ok, reason = check_tier({}, 'pro')
        self.assertFalse(ok)
        self.assertIn('tier_insufficient', reason)


if __name__ == '__main__':
    unittest.main()
