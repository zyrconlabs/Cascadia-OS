"""
linkedin_connector.py — Zyrcon Social Operator v1.1
LinkedIn API v2 connector for publishing text posts.
Owns: posting to LinkedIn via API v2, reading connection status.
Does not own: content generation, QC scoring, OAuth flow, approval gates.
"""
# MATURITY: FUNCTIONAL — Text posts working. Article/image posts v1.2.
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from cascadia.shared.logger import get_logger

logger = get_logger('linkedin_connector')

CONFIG_PATH = Path(__file__).parent / 'linkedin.config.json'

LINKEDIN_SCORING = {
    'max_chars': 3000,
    'optimal_min': 150,
    'optimal_max': 300,
    'max_hashtags': 5,
    'preview_cutoff': 210,
}


class LinkedInConnector:
    """Owns LinkedIn API v2 post publishing."""

    API_BASE = 'https://api.linkedin.com/v2'

    def __init__(self) -> None:
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if CONFIG_PATH.exists():
            try:
                return json.loads(CONFIG_PATH.read_text())
            except Exception:
                pass
        return {}

    def is_connected(self) -> bool:
        return bool(self._config.get('access_token'))

    def get_person_urn(self) -> Optional[str]:
        """Get the authenticated user's LinkedIn URN via OpenID Connect."""
        token = self._config.get('access_token', '')
        if not token:
            return None
        try:
            req = urllib.request.Request(
                f'{self.API_BASE}/userinfo',
                headers={'Authorization': f'Bearer {token}'},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
                return data.get('sub')
        except Exception as e:
            logger.error('LinkedIn: get_person_urn failed: %s', e)
            return None

    def post(self, content: str) -> Dict[str, Any]:
        """
        Publish a text post to LinkedIn.
        Returns: {'success': bool, 'post_id': str, 'error': str}
        """
        token = self._config.get('access_token', '')
        if not token:
            return {'success': False, 'post_id': '', 'error': 'Not authenticated. Run LinkedIn OAuth first.'}

        person_urn = self.get_person_urn()
        if not person_urn:
            return {'success': False, 'post_id': '', 'error': 'Could not resolve LinkedIn user URN.'}

        payload = json.dumps({
            'author': f'urn:li:person:{person_urn}',
            'lifecycleState': 'PUBLISHED',
            'specificContent': {
                'com.linkedin.ugc.ShareContent': {
                    'shareCommentary': {'text': content},
                    'shareMediaCategory': 'NONE',
                }
            },
            'visibility': {'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC'},
        }).encode()

        try:
            req = urllib.request.Request(
                f'{self.API_BASE}/ugcPosts',
                data=payload, method='POST',
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                    'X-Restli-Protocol-Version': '2.0.0',
                },
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                post_id = r.headers.get('X-RestLi-Id', '')
                logger.info('LinkedIn: posted successfully, id=%s', post_id)
                return {'success': True, 'post_id': post_id, 'error': ''}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            logger.error('LinkedIn: post failed %s: %s', e.code, error_body)
            return {'success': False, 'post_id': '', 'error': f'HTTP {e.code}: {error_body}'}
        except Exception as e:
            logger.error('LinkedIn: post failed: %s', e)
            return {'success': False, 'post_id': '', 'error': str(e)}

    def score_content(self, content: str) -> Dict[str, Any]:
        """
        Score content for LinkedIn suitability.
        Returns: {'score': int 0-100, 'issues': list[str], 'platform': 'linkedin'}
        """
        issues = []
        score = 100

        length = len(content)
        if length > LINKEDIN_SCORING['max_chars']:
            issues.append(f'Too long: {length} chars (max {LINKEDIN_SCORING["max_chars"]})')
            score -= 30
        elif length < LINKEDIN_SCORING['optimal_min']:
            issues.append(f'Too short: {length} chars (optimal 150-300)')
            score -= 15
        elif length > LINKEDIN_SCORING['optimal_max']:
            score -= max(0, int((length - LINKEDIN_SCORING['optimal_max']) / 50))

        hashtag_count = content.count('#')
        if hashtag_count > LINKEDIN_SCORING['max_hashtags']:
            issues.append(f'Too many hashtags: {hashtag_count} (max 5)')
            score -= 20

        # First line hook check (first 210 chars before "see more")
        first_line = content[:LINKEDIN_SCORING['preview_cutoff']]
        if not any(c in first_line for c in ('?', '!', ':')):
            issues.append('No hook in first 210 chars — consider adding a question or statement')
            score -= 5

        return {
            'score': max(0, score),
            'issues': issues,
            'platform': 'linkedin',
            'char_count': length,
            'hashtag_count': hashtag_count,
        }
