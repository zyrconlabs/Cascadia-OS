"""tests/test_vantage.py — VANTAGE capability gateway tests."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from cascadia.gateway.vantage import VantageService
from cascadia.shared.entitlements import get_risk_level as resolve_risk_level


def _make_service() -> VantageService:
    """Build a VantageService without starting the HTTP server."""
    with patch('cascadia.gateway.vantage.load_config') as mock_cfg, \
         patch('cascadia.gateway.vantage.ServiceRuntime') as mock_rt_cls, \
         patch('cascadia.gateway.vantage.AuditLog'):
        mock_cfg.return_value = {
            'components': [{'name': 'vantage', 'port': 6208, 'pulse_file': '/tmp/v.hb'}],
            'log_dir': '/tmp/logs',
        }
        mock_rt = MagicMock()
        mock_rt.state = 'ready'
        mock_rt_cls.return_value = mock_rt
        svc = VantageService('/fake/config.json', 'vantage')
        svc._audit = MagicMock()
        return svc


class TestResolveRiskLevel(unittest.TestCase):

    def test_sentinel_exact_match_medium(self):
        assert resolve_risk_level('email.send') == 'medium'

    def test_sentinel_exact_match_critical(self):
        assert resolve_risk_level('shell.exec') == 'critical'

    def test_prefix_verb_gmail_send(self):
        # gmail → email domain; send_email verb → email.send → medium
        assert resolve_risk_level('gmail.send_email') == 'medium'

    def test_prefix_verb_gmail_delete(self):
        # gmail → email domain; delete_email verb → email.delete → high
        assert resolve_risk_level('gmail.delete_email') == 'high'

    def test_prefix_verb_salesforce_delete(self):
        # salesforce → crm domain; delete_contact verb → crm.delete → high
        assert resolve_risk_level('salesforce.delete_contact') == 'high'

    def test_prefix_verb_salesforce_read(self):
        # salesforce → crm domain; read_contact → crm.read not in sentinel → verb fallback → low
        assert resolve_risk_level('salesforce.read_contact') == 'low'

    def test_verb_fallback_delete(self):
        # unknown prefix, verb delete → high
        assert resolve_risk_level('unknown_connector.delete_stuff') == 'high'

    def test_default_low_read(self):
        assert resolve_risk_level('some_service.read_data') == 'low'

    def test_default_medium_unknown(self):
        assert resolve_risk_level('totally.unknown') == 'medium'  # safe default


class TestVantageHandlers(unittest.TestCase):

    def setUp(self) -> None:
        self.svc = _make_service()

    def test_handle_call_missing_operator_id(self):
        code, body = self.svc.handle_call({'capability': 'gmail.send', 'connector_port': 9000})
        self.assertEqual(code, 400)
        self.assertIn('operator_id', body['error'])

    def test_handle_call_missing_capability(self):
        code, body = self.svc.handle_call({'operator_id': 'test_op', 'connector_port': 9000})
        self.assertEqual(code, 400)
        self.assertIn('capability', body['error'])

    def test_handle_call_missing_connector_port(self):
        code, body = self.svc.handle_call({'operator_id': 'test_op', 'capability': 'gmail.send'})
        self.assertEqual(code, 400)
        self.assertIn('connector_port', body['error'])

    def test_handle_call_crew_unreachable(self):
        with patch('cascadia.gateway.vantage.check_capability', return_value={'error': 'connection refused'}):
            code, body = self.svc.handle_call(
                {'operator_id': 'test_op', 'capability': 'gmail.send', 'connector_port': 9000}
            )
        self.assertEqual(code, 503)
        self.assertIn('CREW', body['error'])

    def test_handle_call_undeclared_capability(self):
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': False}):
            code, body = self.svc.handle_call(
                {'operator_id': 'test_op', 'capability': 'gmail.send', 'connector_port': 9000}
            )
        self.assertEqual(code, 403)
        self.assertEqual(body['verdict'], 'blocked')

    def test_handle_call_low_risk_allowed(self):
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': True}), \
             patch('cascadia.gateway.vantage._http_post', return_value={'result': 'ok'}):
            code, body = self.svc.handle_call({
                'operator_id': 'test_op', 'capability': 'salesforce.read_contact',
                'connector_port': 9200,
            })
        self.assertEqual(code, 200)
        self.assertEqual(body['verdict'], 'allowed')
        self.assertEqual(body['risk_level'], 'low')

    def test_handle_call_high_risk_sentinel_blocks(self):
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': True}), \
             patch('cascadia.gateway.vantage.call_sentinel',
                   return_value={'verdict': 'blocked', 'reason': 'insufficient autonomy'}):
            code, body = self.svc.handle_call({
                'operator_id': 'test_op', 'capability': 'gmail.delete_email',
                'connector_port': 9000, 'autonomy_level': 'manual_only',
            })
        self.assertEqual(code, 403)
        self.assertIn('blocked', body['verdict'])

    def test_handle_call_high_risk_sentinel_allowed(self):
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': True}), \
             patch('cascadia.gateway.vantage.call_sentinel', return_value={'verdict': 'allowed'}), \
             patch('cascadia.gateway.vantage._http_post', return_value={'deleted': True}):
            code, body = self.svc.handle_call({
                'operator_id': 'test_op', 'capability': 'gmail.delete_email',
                'connector_port': 9000, 'autonomy_level': 'autonomous',
            })
        self.assertEqual(code, 200)
        self.assertEqual(body['verdict'], 'allowed')

    def test_handle_call_sentinel_unavailable(self):
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': True}), \
             patch('cascadia.gateway.vantage.call_sentinel', return_value={'error': 'timeout'}):
            code, body = self.svc.handle_call({
                'operator_id': 'test_op', 'capability': 'file.delete',
                'connector_port': 9000,
            })
        self.assertEqual(code, 503)

    def test_tier_blocks_lite_license_on_business_operator(self):
        """lite license (level 1) must not reach a business-tier operator (level 3)."""
        crew_registry = {'operators': {'test_op': {
            'capabilities': ['crm.write'], 'tier_required': 'business',
        }}}
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': True}), \
             patch('cascadia.gateway.vantage.fetch_crew_registry', return_value=crew_registry):
            code, body = self.svc.handle_call({
                'operator_id': 'test_op', 'capability': 'crm.write', 'connector_port': 9200,
            })
        self.assertEqual(code, 403)
        self.assertEqual(body['error'], 'tier_insufficient')
        self.assertEqual(body['required_tier'], 'business')
        self.assertEqual(body['current_tier'], 'lite')

    def test_tier_allows_matching_tier(self):
        """business license (level 3) satisfies a business-tier operator requirement."""
        crew_registry = {'operators': {'test_op': {
            'capabilities': ['crm.write'], 'tier_required': 'business',
        }}}
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': True}), \
             patch('cascadia.gateway.vantage.fetch_crew_registry', return_value=crew_registry), \
             patch('cascadia.gateway.vantage._http_post', return_value={'result': 'ok'}):
            with patch.object(self.svc, '_config', {'license': {'tier': 'business'}, **self.svc._config}):
                code, body = self.svc.handle_call({
                    'operator_id': 'test_op', 'capability': 'crm.write', 'connector_port': 9200,
                })
        self.assertEqual(code, 200)
        self.assertEqual(body['verdict'], 'allowed')

    def test_tier_allows_lite_operator_on_lite_license(self):
        """Operator with no tier_required (defaults to lite) is always accessible."""
        crew_registry = {'operators': {'test_op': {'capabilities': ['crm.read']}}}
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': True}), \
             patch('cascadia.gateway.vantage.fetch_crew_registry', return_value=crew_registry), \
             patch('cascadia.gateway.vantage._http_post', return_value={'result': 'ok'}):
            code, body = self.svc.handle_call({
                'operator_id': 'test_op', 'capability': 'crm.read', 'connector_port': 9200,
            })
        self.assertEqual(code, 200)
        self.assertEqual(body['verdict'], 'allowed')

    def test_handle_simulate_allowed(self):
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': True}), \
             patch('cascadia.gateway.vantage.call_sentinel', return_value={'verdict': 'allowed'}):
            code, body = self.svc.handle_simulate({
                'operator_id': 'test_op', 'capability': 'file.delete',
                'autonomy_level': 'autonomous',
            })
        self.assertEqual(code, 200)
        self.assertTrue(body['simulation'])
        self.assertEqual(body['predicted_verdict'], 'allowed')
        self.assertEqual(body['risk_level'], 'high')

    def test_handle_simulate_blocked_undeclared(self):
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': False}):
            code, body = self.svc.handle_simulate({
                'operator_id': 'test_op', 'capability': 'gmail.send',
            })
        self.assertEqual(code, 200)
        self.assertEqual(body['predicted_verdict'], 'blocked_undeclared')

    def test_handle_health(self):
        code, body = self.svc.handle_health({})
        self.assertEqual(code, 200)
        self.assertEqual(body['service'], 'vantage')
        self.assertIn('calls_total', body)
        self.assertIn('calls_blocked', body)

    def test_handle_registry(self):
        crew = {'operators': {'test_op': {'capabilities': ['gmail.send', 'file.read']}}}
        with patch('cascadia.gateway.vantage.fetch_crew_registry', return_value=crew):
            code, body = self.svc.handle_registry({})
        self.assertEqual(code, 200)
        self.assertIn('test_op', body['registry'])
        risk_map = {c['capability']: c['risk_level'] for c in body['registry']['test_op']}
        self.assertEqual(risk_map['gmail.send'], 'medium')
        self.assertEqual(risk_map['file.read'], 'low')

    def test_handle_capabilities_not_found(self):
        with patch('cascadia.gateway.vantage.fetch_crew_registry', return_value={'operators': {}}):
            code, body = self.svc.handle_capabilities({'operator_id': 'missing_op'})
        self.assertEqual(code, 404)
        self.assertIn('missing_op', body['error'])

    def test_crew_ok_contract(self):
        """
        VANTAGE must read 'ok' from CREW /validate response.
        CREW returns {ok: bool} — reading 'valid' would always return False
        and block every capability check regardless of manifest declarations.
        Regression guard: never revert to reading 'valid' from CREW response.
        """
        base = {'operator_id': 'test_op', 'capability': 'connector.auth', 'connector_port': 9020}

        # CREW returns ok=True → capability check passes, call forwarded
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': True}), \
             patch('cascadia.gateway.vantage._http_post', return_value={'access_token': 'tok'}):
            code, body = self.svc.handle_call(base)
        self.assertEqual(code, 200)
        self.assertEqual(body['verdict'], 'allowed')

        # CREW returns ok=False → capability check fails, 403 blocked
        with patch('cascadia.gateway.vantage.check_capability', return_value={'ok': False}):
            code, body = self.svc.handle_call(base)
        self.assertEqual(code, 403)
        self.assertEqual(body['verdict'], 'blocked')

        # CREW returns legacy 'valid' key (wrong key) → treated as False → 403
        with patch('cascadia.gateway.vantage.check_capability', return_value={'valid': True}):
            code, body = self.svc.handle_call(base)
        self.assertEqual(code, 403, "VANTAGE must not read 'valid' from CREW — use 'ok'")


if __name__ == '__main__':
    unittest.main()
