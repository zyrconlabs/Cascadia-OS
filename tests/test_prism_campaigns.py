"""Tests for PRISM /campaigns — Growth Campaigns review page.

Covers:
  - serve_campaigns: HTML serving
  - campaigns_list: proxy to social operator
  - campaign_posts_list: proxy with campaign_id path param
  - campaign_post_approve: proxy approval with optional edit_content
  - campaign_post_reject: proxy rejection
  - campaign_approve_all: proxy bulk approval
  - campaigns_generate: proxy campaign generation
  - prism.html structural checks (nav button, route registrations)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

HTML_PATH = Path(__file__).parent.parent / "cascadia" / "dashboard" / "prism.html"
TMPL_PATH = Path(__file__).parent.parent / "cascadia" / "dashboard" / "templates" / "campaigns.html"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _make_svc():
    from cascadia.dashboard.prism import PrismService
    svc = PrismService.__new__(PrismService)
    svc._ports = {}
    return svc


# ── serve_campaigns ─────────────────────────────────────────────────────────────

class TestServeCampaigns:

    def test_returns_200(self):
        svc = _make_svc()
        code, body = svc.serve_campaigns({})
        assert code == 200

    def test_returns_html_bytes(self):
        svc = _make_svc()
        _, body = svc.serve_campaigns({})
        assert '__html__' in body
        assert isinstance(body['__html__'], bytes)

    def test_html_contains_campaigns_heading(self):
        svc = _make_svc()
        _, body = svc.serve_campaigns({})
        assert b'Growth Campaigns' in body['__html__']

    def test_template_file_exists(self):
        assert TMPL_PATH.exists(), "campaigns.html template must exist"


# ── campaigns_list ──────────────────────────────────────────────────────────────

class TestCampaignsList:

    def test_proxies_to_social_operator(self):
        svc = _make_svc()
        fake = {'campaigns': [{'id': 'c1', 'topic': 'test', 'status': 'draft'}]}
        with patch('cascadia.dashboard.prism._http_get', return_value=fake) as mock:
            code, body = svc.campaigns_list({})
        assert code == 200
        mock.assert_called_once_with(8011, '/campaigns', timeout=5.0)

    def test_returns_campaigns(self):
        svc = _make_svc()
        fake = {'campaigns': [{'id': 'c1', 'topic': 'hello'}]}
        with patch('cascadia.dashboard.prism._http_get', return_value=fake):
            _, body = svc.campaigns_list({})
        assert body['campaigns'][0]['id'] == 'c1'

    def test_returns_502_when_social_down(self):
        svc = _make_svc()
        with patch('cascadia.dashboard.prism._http_get', return_value=None):
            code, body = svc.campaigns_list({})
        assert code == 502
        assert 'error' in body

    def test_502_includes_empty_campaigns(self):
        svc = _make_svc()
        with patch('cascadia.dashboard.prism._http_get', return_value=None):
            _, body = svc.campaigns_list({})
        assert body.get('campaigns') == []


# ── campaign_posts_list ─────────────────────────────────────────────────────────

class TestCampaignPostsList:

    def test_returns_posts(self):
        svc = _make_svc()
        fake = {'posts': [{'id': 'p1', 'content': 'hello'}]}
        with patch('cascadia.dashboard.prism._http_get', return_value=fake):
            code, body = svc.campaign_posts_list({'campaign_id': 'c1'})
        assert code == 200
        assert body['posts'][0]['id'] == 'p1'

    def test_proxies_correct_path(self):
        svc = _make_svc()
        with patch('cascadia.dashboard.prism._http_get', return_value={}) as mock:
            svc.campaign_posts_list({'campaign_id': 'abc-123'})
        mock.assert_called_once_with(8011, '/campaigns/abc-123/posts', timeout=5.0)

    def test_400_when_no_campaign_id(self):
        svc = _make_svc()
        code, body = svc.campaign_posts_list({})
        assert code == 400
        assert 'error' in body

    def test_502_when_social_down(self):
        svc = _make_svc()
        with patch('cascadia.dashboard.prism._http_get', return_value=None):
            code, _ = svc.campaign_posts_list({'campaign_id': 'c1'})
        assert code == 502


# ── campaign_post_approve ───────────────────────────────────────────────────────

class TestCampaignPostApprove:

    def test_proxies_approval(self):
        svc = _make_svc()
        fake = {'approved': True}
        with patch('cascadia.dashboard.prism._http_post', return_value=fake) as mock:
            code, body = svc.campaign_post_approve({'campaign_id': 'c1', 'post_id': 'p1'})
        assert code == 200
        mock.assert_called_once_with(
            8011, '/campaigns/c1/posts/p1/approve',
            {'approved_by': 'prism', 'edit_content': None},
            timeout=10.0,
        )

    def test_proxies_edit_content(self):
        svc = _make_svc()
        with patch('cascadia.dashboard.prism._http_post', return_value={}) as mock:
            svc.campaign_post_approve({'campaign_id': 'c1', 'post_id': 'p1', 'edit_content': 'new text'})
        call_kwargs = mock.call_args
        assert call_kwargs[0][2]['edit_content'] == 'new text'

    def test_400_when_missing_ids(self):
        svc = _make_svc()
        code, _ = svc.campaign_post_approve({'campaign_id': 'c1'})
        assert code == 400

    def test_502_when_social_down(self):
        svc = _make_svc()
        with patch('cascadia.dashboard.prism._http_post', return_value=None):
            code, _ = svc.campaign_post_approve({'campaign_id': 'c1', 'post_id': 'p1'})
        assert code == 502


# ── campaign_post_reject ────────────────────────────────────────────────────────

class TestCampaignPostReject:

    def test_proxies_rejection(self):
        svc = _make_svc()
        with patch('cascadia.dashboard.prism._http_post', return_value={'rejected': True}) as mock:
            code, _ = svc.campaign_post_reject({'campaign_id': 'c1', 'post_id': 'p2'})
        assert code == 200
        mock.assert_called_once_with(8011, '/campaigns/c1/posts/p2/reject', {}, timeout=10.0)

    def test_400_when_missing_post_id(self):
        svc = _make_svc()
        code, _ = svc.campaign_post_reject({'campaign_id': 'c1'})
        assert code == 400

    def test_502_when_social_down(self):
        svc = _make_svc()
        with patch('cascadia.dashboard.prism._http_post', return_value=None):
            code, _ = svc.campaign_post_reject({'campaign_id': 'c1', 'post_id': 'p1'})
        assert code == 502


# ── campaign_approve_all ────────────────────────────────────────────────────────

class TestCampaignApproveAll:

    def test_proxies_approve_all(self):
        svc = _make_svc()
        fake = {'approved_count': 3}
        with patch('cascadia.dashboard.prism._http_post', return_value=fake) as mock:
            code, body = svc.campaign_approve_all({'campaign_id': 'c1'})
        assert code == 200
        assert body['approved_count'] == 3
        mock.assert_called_once_with(
            8011, '/campaigns/c1/approve_all',
            {'approved_by': 'prism'},
            timeout=10.0,
        )

    def test_400_when_no_campaign_id(self):
        svc = _make_svc()
        code, _ = svc.campaign_approve_all({})
        assert code == 400

    def test_502_when_social_down(self):
        svc = _make_svc()
        with patch('cascadia.dashboard.prism._http_post', return_value=None):
            code, _ = svc.campaign_approve_all({'campaign_id': 'c1'})
        assert code == 502


# ── campaigns_generate ──────────────────────────────────────────────────────────

class TestCampaignsGenerate:

    def test_proxies_generate(self):
        svc = _make_svc()
        fake = {'campaign_id': 'c99', 'posts': []}
        with patch('cascadia.dashboard.prism._http_post', return_value=fake) as mock:
            code, body = svc.campaigns_generate({'topic': 'launch', 'num_posts': 3})
        assert code == 202
        assert body['campaign_id'] == 'c99'
        args = mock.call_args[0]
        assert args[0] == 8011
        assert args[1] == '/generate'
        assert args[2]['topic'] == 'launch'
        assert args[2]['num_posts'] == 3

    def test_502_when_social_down(self):
        svc = _make_svc()
        with patch('cascadia.dashboard.prism._http_post', return_value=None):
            code, body = svc.campaigns_generate({'topic': 'x'})
        assert code == 502
        assert 'error' in body

    def test_default_platform_is_x(self):
        svc = _make_svc()
        with patch('cascadia.dashboard.prism._http_post', return_value={}) as mock:
            svc.campaigns_generate({'topic': 'test'})
        assert mock.call_args[0][2]['platform'] == 'x'


# ── prism.html structural checks ───────────────────────────────────────────────

class TestPrismHtmlStructure:

    def test_campaigns_nav_button_present(self):
        assert 'nav-campaigns' in _html()

    def test_campaigns_nav_links_to_campaigns(self):
        assert "location.href='/campaigns'" in _html()

    def test_campaigns_nav_tooltip(self):
        assert 'Growth Campaigns' in _html()


# ── campaigns.html template checks ─────────────────────────────────────────────

class TestCampaignsTemplate:

    def _tmpl(self) -> str:
        return TMPL_PATH.read_text(encoding='utf-8')

    def test_has_generate_form(self):
        assert 'generateCampaign' in self._tmpl()

    def test_has_approve_all_button(self):
        assert 'approveAll' in self._tmpl()

    def test_has_approve_post_action(self):
        assert 'approvePost' in self._tmpl()

    def test_has_reject_post_action(self):
        assert 'rejectPost' in self._tmpl()

    def test_fetches_campaigns_list(self):
        assert '/api/campaigns' in self._tmpl()

    def test_fetches_posts_list(self):
        assert '/api/campaigns/' in self._tmpl()

    def test_approve_all_posts_endpoint(self):
        assert 'approve_all' in self._tmpl()
