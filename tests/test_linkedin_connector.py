from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from operators.social.connectors.linkedin_connector import LinkedInConnector


class LinkedInConnectorTests(unittest.TestCase):
    def _make_connector(self, token: str = '') -> LinkedInConnector:
        conn = LinkedInConnector.__new__(LinkedInConnector)
        conn._config = {'access_token': token}
        return conn

    def test_is_connected_false_without_token(self) -> None:
        conn = self._make_connector(token='')
        self.assertFalse(conn.is_connected())

    def test_is_connected_true_with_token(self) -> None:
        conn = self._make_connector(token='tok_abc')
        self.assertTrue(conn.is_connected())

    def test_post_without_token_returns_error(self) -> None:
        conn = self._make_connector(token='')
        result = conn.post('Hello LinkedIn')
        self.assertFalse(result['success'])
        self.assertIn('Not authenticated', result['error'])

    def test_post_without_urn_returns_error(self) -> None:
        conn = self._make_connector(token='tok_abc')
        with patch.object(conn, 'get_person_urn', return_value=None):
            result = conn.post('Hello')
            self.assertFalse(result['success'])
            self.assertIn('URN', result['error'])

    def test_post_success_mock(self) -> None:
        conn = self._make_connector(token='tok_abc')
        with patch.object(conn, 'get_person_urn', return_value='abc123'):
            mock_response = MagicMock()
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_response.headers = {'X-RestLi-Id': 'post_999'}
            with patch('urllib.request.urlopen', return_value=mock_response):
                result = conn.post('Test post content')
                self.assertTrue(result['success'])
                self.assertEqual(result['post_id'], 'post_999')

    def test_score_content_optimal_length(self) -> None:
        conn = self._make_connector()
        content = 'A' * 200
        result = conn.score_content(content)
        self.assertEqual(result['platform'], 'linkedin')
        self.assertGreater(result['score'], 80)
        self.assertEqual(result['char_count'], 200)

    def test_score_content_too_long(self) -> None:
        conn = self._make_connector()
        content = 'A' * 3001
        result = conn.score_content(content)
        self.assertLess(result['score'], 80)
        self.assertTrue(any('Too long' in i for i in result['issues']))

    def test_score_content_too_many_hashtags(self) -> None:
        conn = self._make_connector()
        content = 'Post ' + ' '.join(f'#tag{i}' for i in range(7))
        result = conn.score_content(content)
        self.assertTrue(any('hashtag' in i for i in result['issues']))

    def test_load_config_missing_file(self) -> None:
        conn = LinkedInConnector.__new__(LinkedInConnector)
        conn._config = conn._load_config()
        self.assertIsInstance(conn._config, dict)


if __name__ == '__main__':
    unittest.main()
