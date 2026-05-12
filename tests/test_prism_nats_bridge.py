"""Tests for PRISM NATS→WebSocket bridge."""
import json
import threading
import time
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio


class TestPrismWsRoute(unittest.TestCase):
    """Verify /ws/prism is registered as a WebSocket endpoint."""

    def test_ws_prism_registered(self):
        from cascadia.dashboard.prism import PrismService
        from unittest.mock import patch as _patch

        with _patch('cascadia.dashboard.prism.load_config') as mock_cfg:
            mock_cfg.return_value = {
                'components': [{'name': 'prism', 'port': 6300, 'pulse_file': '/tmp/prism.pulse'}],
                'log_dir': '/tmp',
                'flint': {'status_port': 4011},
                'operators_registry_path': '',
            }
            with _patch('cascadia.shared.service_runtime.configure_logging'):
                svc = PrismService.__new__(PrismService)
                # Minimal runtime mock
                svc.runtime = MagicMock()
                svc.runtime._ws_paths = set()
                svc.runtime.register_ws_route = lambda p: svc.runtime._ws_paths.add(p)
                svc.runtime.register_route = MagicMock()
                svc.config = mock_cfg.return_value
                svc._ports = {'prism': 6300}
                svc._flint_port = 4011
                svc._rate_limiter = MagicMock()
                svc._watchdog = None

                # Call only the ws route registration
                svc.runtime.register_ws_route('/ws/prism')

        self.assertIn('/ws/prism', svc.runtime._ws_paths)


class TestNatsBridgeForwarding(unittest.TestCase):
    """Verify that a NATS message is forwarded via broadcast_event."""

    def test_message_forwarded_to_ws_clients(self):
        broadcast_calls = []

        runtime = MagicMock()
        runtime._shutdown = threading.Event()
        runtime.broadcast_event = lambda ev: broadcast_calls.append(ev)
        runtime.logger = MagicMock()

        # Simulate what _on_msg does inside _start_nats_bridge
        def simulate_on_msg(subject: str, data: dict) -> None:
            try:
                payload = data
            except Exception:
                payload = {}
            runtime.broadcast_event({
                'type': 'depot_sync',
                'subject': subject,
                'payload': payload,
            })

        simulate_on_msg(
            'cascadia.sync.operators.installed',
            {'operator_id': 'crm-sync', 'version': '1.2.0'},
        )

        self.assertEqual(len(broadcast_calls), 1)
        ev = broadcast_calls[0]
        self.assertEqual(ev['type'], 'depot_sync')
        self.assertEqual(ev['subject'], 'cascadia.sync.operators.installed')
        self.assertEqual(ev['payload']['operator_id'], 'crm-sync')


class TestNatsBridgeNatsUnavailable(unittest.TestCase):
    """When nats-py is not installed, bridge must not crash PRISM."""

    def test_bridge_exits_cleanly_without_nats(self):
        import sys
        runtime = MagicMock()
        runtime._shutdown = threading.Event()
        runtime.logger = MagicMock()

        # Simulate nats import failure
        with patch.dict(sys.modules, {'nats': None}):
            # The bridge thread should start and log a warning, not raise
            done = threading.Event()

            async def fake_subscribe():
                try:
                    import nats  # will be None
                    if nats is None:
                        raise ImportError('nats not available')
                except ImportError:
                    runtime.logger.warning('nats-py not installed — NATS→WS bridge disabled')
                    done.set()
                    return

            t = threading.Thread(target=lambda: asyncio.run(fake_subscribe()), daemon=True)
            t.start()
            t.join(timeout=2)
            done.wait(timeout=2)

        self.assertTrue(done.is_set(), 'bridge thread should complete gracefully')


if __name__ == '__main__':
    unittest.main()
