from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from cascadia.fleet.fleet_registry import FleetRegistry


class FleetRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'fleet.json'
        self.registry = FleetRegistry(db_path=self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_register_adds_node(self) -> None:
        self.registry.register('node_001', 'Main Office', '192.168.1.10', 6300)
        nodes = self.registry.list_nodes()
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]['name'], 'Main Office')

    def test_register_persists_to_file(self) -> None:
        self.registry.register('node_001', 'Main Office', '192.168.1.10')
        data = json.loads(self.db_path.read_text())
        self.assertIn('node_001', data)

    def test_remove_node(self) -> None:
        self.registry.register('node_001', 'Main', '192.168.1.10')
        result = self.registry.remove('node_001')
        self.assertTrue(result)
        self.assertEqual(len(self.registry.list_nodes()), 0)

    def test_remove_missing_returns_false(self) -> None:
        self.assertFalse(self.registry.remove('nonexistent'))

    def test_get_node(self) -> None:
        self.registry.register('node_abc', 'Test', '10.0.0.1')
        node = self.registry.get_node('node_abc')
        self.assertIsNotNone(node)
        self.assertEqual(node['host'], '10.0.0.1')

    def test_poll_marks_online_on_success(self) -> None:
        self.registry.register('node_001', 'Main', '192.168.1.10')
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read = lambda: json.dumps({'components_healthy': 11, 'components_total': 11}).encode()
        with patch('urllib.request.urlopen', return_value=mock_response):
            self.registry.poll_all()
        node = self.registry.get_node('node_001')
        self.assertEqual(node['status'], 'online')
        self.assertEqual(node['components_healthy'], 11)

    def test_poll_marks_offline_on_failure(self) -> None:
        self.registry.register('node_002', 'Offline Node', '192.168.1.99')
        with patch('urllib.request.urlopen', side_effect=Exception('timeout')):
            self.registry.poll_all()
        node = self.registry.get_node('node_002')
        self.assertEqual(node['status'], 'offline')

    def test_persistence_on_reload(self) -> None:
        self.registry.register('node_001', 'Persist', '192.168.1.10')
        reloaded = FleetRegistry(db_path=self.db_path)
        nodes = reloaded.list_nodes()
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]['node_id'], 'node_001')


if __name__ == '__main__':
    unittest.main()
