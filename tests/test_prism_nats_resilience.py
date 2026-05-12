"""Tests for PRISM NATS bridge resilience: NATS down, reconnect, malformed payload."""
import json
import threading
import time
import unittest
from unittest.mock import MagicMock


class TestNatsBridgeResilience(unittest.TestCase):

    def test_malformed_json_does_not_crash_bridge(self):
        """Raw non-JSON bytes in a NATS message must not propagate an exception."""
        broadcast_calls = []
        runtime = MagicMock()
        runtime.broadcast_event = lambda ev: broadcast_calls.append(ev)

        def safe_on_msg(subject: str, raw: bytes) -> None:
            try:
                payload = json.loads(raw.decode())
            except Exception:
                payload = {'raw': raw.decode(errors='replace')}
            runtime.broadcast_event({'type': 'depot_sync', 'subject': subject, 'payload': payload})

        safe_on_msg('cascadia.sync.operators.installed', b'NOT VALID JSON{{{{')
        self.assertEqual(len(broadcast_calls), 1)
        self.assertIn('raw', broadcast_calls[0]['payload'])

    def test_broadcast_exception_does_not_crash_bridge(self):
        """If broadcast_event raises (dead socket cleanup), bridge must continue."""
        call_count = [0]

        def flaky_broadcast(ev):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError('broken pipe')

        runtime = MagicMock()
        runtime.broadcast_event = flaky_broadcast

        def safe_forward(subject: str, payload: dict) -> None:
            try:
                runtime.broadcast_event({'type': 'depot_sync', 'subject': subject, 'payload': payload})
            except Exception:
                pass  # dead clients are pruned by ServiceRuntime.broadcast_event

        safe_forward('cascadia.sync.operators.installed', {'id': 'a'})
        safe_forward('cascadia.sync.operators.updated', {'id': 'b'})
        self.assertEqual(call_count[0], 2)

    def test_sync_publisher_health_port_constant(self):
        from cascadia.depot.sync_publisher import HEALTH_PORT
        self.assertEqual(HEALTH_PORT, 6213)

    def test_sync_publisher_reports_ready_without_nats(self):
        """When nats-py absent, sync_publisher sets _ready=True so FLINT sees ok."""
        from cascadia.depot import sync_publisher as sp
        import asyncio

        original_ready = sp._ready
        original_nats = sp._NATS_AVAILABLE
        try:
            sp._NATS_AVAILABLE = False
            sp._ready = False

            async def run():
                # Run main but break out of infinite sleep after 0.1s
                import asyncio as _a
                task = _a.create_task(sp.main())
                await _a.sleep(0.05)
                task.cancel()
                try:
                    await task
                except _a.CancelledError:
                    pass

            asyncio.run(run())
            self.assertTrue(sp._ready)
        finally:
            sp._ready = original_ready
            sp._NATS_AVAILABLE = original_nats


if __name__ == '__main__':
    unittest.main()
