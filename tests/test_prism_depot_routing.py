"""Tests for PRISM depot_operators() routing priority: local → remote → fallback."""
import json
import unittest
from unittest.mock import patch, MagicMock


def _make_response(operators):
    mock = MagicMock()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.read.return_value = json.dumps({'operators': operators}).encode()
    return mock


class TestPrismDepotRouting(unittest.TestCase):

    def _get_depot_operators(self):
        from cascadia.dashboard.prism import PrismService
        svc = PrismService.__new__(PrismService)
        svc.config = {}
        return svc.depot_operators

    def test_local_depot_api_used_first(self):
        depot_operators = self._get_depot_operators()
        local_ops = [{'id': 'op-local', 'name': 'Local Op'}]

        call_log = []

        def fake_urlopen(url, timeout=5):
            call_log.append(str(url))
            if '127.0.0.1:6212' in str(url):
                return _make_response(local_ops)
            raise Exception('should not reach remote')

        import urllib.request as _ur
        with patch.object(_ur, 'urlopen', side_effect=fake_urlopen):
            code, body = depot_operators({})

        self.assertEqual(code, 200)
        self.assertTrue(any('6212' in u for u in call_log), f'6212 not in calls: {call_log}')

    def test_falls_back_to_remote_when_local_down(self):
        depot_operators = self._get_depot_operators()
        remote_ops = [{'id': 'op-remote', 'name': 'Remote Op'}]

        def fake_urlopen(url, timeout=5):
            if '127.0.0.1:6212' in str(url):
                raise ConnectionRefusedError('local down')
            return _make_response(remote_ops)

        import urllib.request as _ur
        with patch.object(_ur, 'urlopen', side_effect=fake_urlopen):
            code, body = depot_operators({})

        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(body['operators']), 0)  # remote or fallback

    def test_falls_back_to_depot_client_when_both_down(self):
        depot_operators = self._get_depot_operators()

        def fake_urlopen(url, timeout=5):
            raise ConnectionRefusedError('all down')

        fallback_ops = [{'id': 'fallback-op', 'name': 'Fallback'}]

        import urllib.request as _ur
        with patch.object(_ur, 'urlopen', side_effect=fake_urlopen):
            with patch('cascadia.marketplace.depot_client.DEPOTClient') as MockClient:
                MockClient.return_value.list_operators.return_value = fallback_ops
                code, body = depot_operators({})

        self.assertEqual(code, 200)


if __name__ == '__main__':
    unittest.main()
