from __future__ import annotations

import json
import unittest
from unittest.mock import patch, MagicMock

from cascadia.memory.context_builder import ContextBuilder


class ContextBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = ContextBuilder(vault_port=5101)

    def test_unknown_company_returns_empty(self) -> None:
        with patch.object(self.builder, '_vault_get', return_value=None):
            result = self.builder.build_for_lead('Unknown Corp')
            self.assertEqual(result, '')

    def test_known_company_returns_context(self) -> None:
        def mock_get(key):
            if 'proposals' in key:
                return [{'amount': 50000, 'outcome': 'won'}]
            return None
        with patch.object(self.builder, '_vault_get', side_effect=mock_get):
            result = self.builder.build_for_lead('Acme Corp')
            self.assertIn('RELEVANT BUSINESS CONTEXT', result)
            self.assertIn('proposals', result.lower())

    def test_context_truncated_at_token_budget(self) -> None:
        long_data = 'x' * 10000
        with patch.object(self.builder, '_vault_get', return_value=long_data):
            result = self.builder.build_for_lead('Big Corp', max_tokens=100)
            self.assertIn('[context truncated]', result)
            self.assertLessEqual(len(result), 100 * 4 + 100)

    def test_record_outcome_stores_in_vault(self) -> None:
        stored = {}
        def mock_get(key):
            return stored.get(key)
        def mock_set(key, value):
            stored[key] = value
        with patch.object(self.builder, '_vault_get', side_effect=mock_get):
            with patch.object(self.builder, '_vault_set', side_effect=mock_set):
                self.builder.record_outcome('Acme', 'owner@acme.com', 'won', 50000)
                self.assertIn('outcomes:owner@acme.com', stored)

    def test_record_preference_stores_in_vault(self) -> None:
        stored = {}
        with patch.object(self.builder, '_vault_get', return_value=None):
            with patch.object(self.builder, '_vault_set',
                              side_effect=lambda k, v: stored.update({k: v})):
                self.builder.record_preference('Acme Corp', 'format', 'formal')
                self.assertIn('preferences:acme_corp', stored)

    def test_vault_unreachable_returns_empty(self) -> None:
        # When VAULT is down, build_for_lead returns empty string
        import urllib.error
        with patch('urllib.request.urlopen', side_effect=Exception('conn refused')):
            result = self.builder.build_for_lead('Any Corp')
            self.assertEqual(result, '')

    def test_outcome_limited_to_last_10(self) -> None:
        existing = [{'outcome': 'won', 'ts': i} for i in range(15)]
        def mock_get(key):
            if 'outcomes' in key:
                return existing
            return None
        saved = {}
        with patch.object(self.builder, '_vault_get', side_effect=mock_get):
            with patch.object(self.builder, '_vault_set',
                              side_effect=lambda k, v: saved.update({k: v})):
                self.builder.record_outcome('Co', 'x@co.com', 'lost', 1000)
                key = 'outcomes:x@co.com'
                self.assertEqual(len(saved[key]), 10)


if __name__ == '__main__':
    unittest.main()
