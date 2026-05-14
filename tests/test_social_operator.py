"""
tests/test_social_operator.py
Social Media Campaign Operator — 20+ tests covering:
  campaign_db, quality_check, generator, server endpoints.

Run from /Users/andy/Zyrcon/cascadia-os:
    python3 -m pytest tests/test_social_operator.py -v
"""
from __future__ import annotations

import json
import sys
import types
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path wiring + cascadia_sdk stub
# ---------------------------------------------------------------------------

OPS_ROOT = Path(__file__).parent.parent.parent / 'operators' / 'cascadia-os-operators'
sys.path.insert(0, str(OPS_ROOT))

sdk_stub = types.ModuleType('cascadia_sdk')
sdk_stub.vault_get      = lambda k: None
sdk_stub.vault_store    = lambda k, v: False
sdk_stub.sentinel_check = lambda a, c=None: {'allowed': True}
sdk_stub.crew_register  = lambda m: False
sys.modules.setdefault('cascadia_sdk', sdk_stub)


# ---------------------------------------------------------------------------
# DB fixture — isolated temp DB per test
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    import social.campaign_db as cdb
    monkeypatch.setattr(cdb, 'DB_PATH', tmp_path / 'social.db')
    monkeypatch.setattr(cdb, '_DB_DIR', tmp_path)
    cdb.init_db()
    yield cdb


# ============================================================================
# campaign_db
# ============================================================================

class TestCampaignDB:

    def test_create_campaign_returns_uuid(self, db):
        cid = db.create_campaign('HVAC', {}, 5, '2026-05-16')
        assert isinstance(cid, str)
        uuid.UUID(cid)  # raises if not valid UUID

    def test_insert_post_returns_uuid_and_pending(self, db):
        cid = db.create_campaign('Roofing', {}, 3, '2026-05-16')
        pid = db.insert_post(cid, 'x', 'Great deals today.', '2026-05-16T08:00:00')
        assert isinstance(pid, str)
        uuid.UUID(pid)
        posts = db.get_campaign_posts(cid)
        assert posts[0]['status'] == 'pending_approval'

    def test_get_due_posts_returns_only_approved_and_past(self, db):
        cid = db.create_campaign('Test', {}, 2, '2026-01-01')
        pid_past   = db.insert_post(cid, 'x', 'Past post',   '2020-01-01T08:00:00')
        pid_future = db.insert_post(cid, 'x', 'Future post', '2099-12-31T08:00:00')
        pid_pend   = db.insert_post(cid, 'x', 'Pending',     '2020-06-01T08:00:00')
        db.approve_post(pid_past, 'admin')
        db.approve_post(pid_future, 'admin')
        # pid_pend stays pending_approval
        due = db.get_due_posts()
        due_ids = {p['id'] for p in due}
        assert pid_past in due_ids
        assert pid_future not in due_ids
        assert pid_pend not in due_ids

    def test_approve_post_sets_status_and_approved_at(self, db):
        cid = db.create_campaign('HVAC', {}, 1, '2026-05-16')
        pid = db.insert_post(cid, 'x', 'Content here', '2026-05-16T08:00:00')
        ok  = db.approve_post(pid, 'owner')
        assert ok is True
        posts = db.get_campaign_posts(cid, 'approved')
        assert len(posts) == 1
        assert posts[0]['approved_by'] == 'owner'
        assert posts[0]['approved_at'] is not None

    def test_approve_all_returns_correct_count(self, db):
        cid = db.create_campaign('Plumbing', {}, 4, '2026-05-16')
        for i in range(4):
            db.insert_post(cid, 'x', f'Post {i}', f'2026-05-1{6 + i}T08:00:00')
        n = db.approve_all(cid, 'admin')
        assert n == 4
        assert len(db.get_campaign_posts(cid, 'approved')) == 4

    def test_reject_post_sets_status_rejected(self, db):
        cid = db.create_campaign('Electrical', {}, 1, '2026-05-16')
        pid = db.insert_post(cid, 'x', 'Bad content', '2026-05-16T08:00:00')
        ok  = db.reject_post(pid)
        assert ok is True
        posts = db.get_campaign_posts(cid, 'rejected')
        assert len(posts) == 1

    def test_edit_content_updates_on_approve(self, db):
        cid = db.create_campaign('HVAC', {}, 1, '2026-05-16')
        pid = db.insert_post(cid, 'x', 'Original', '2026-05-16T08:00:00')
        db.approve_post(pid, 'owner', edit_content='Edited content')
        posts = db.get_campaign_posts(cid, 'approved')
        assert posts[0]['content'] == 'Edited content'

    def test_get_campaign_posts_filter_by_status(self, db):
        cid = db.create_campaign('Test', {}, 3, '2026-05-16')
        p1  = db.insert_post(cid, 'x', 'A', '2026-05-16T08:00:00')
        p2  = db.insert_post(cid, 'x', 'B', '2026-05-17T08:00:00')
        db.insert_post(cid, 'x', 'C', '2026-05-18T08:00:00')
        db.approve_post(p1, 'admin')
        db.reject_post(p2)
        assert len(db.get_campaign_posts(cid, 'approved'))         == 1
        assert len(db.get_campaign_posts(cid, 'rejected'))         == 1
        assert len(db.get_campaign_posts(cid, 'pending_approval')) == 1

    def test_init_db_is_idempotent(self, db):
        db.init_db()
        db.init_db()
        cid = db.create_campaign('Safe', {}, 1, '2026-05-16')
        assert cid


# ============================================================================
# quality_check  (score_post returns Dict; total_score is a float 0–1)
# ============================================================================

class TestQualityCheck:

    def _qc(self, text, platform='x', hashtags=None, tone='direct', forbidden=None):
        from social.pipeline.quality_check import score_post
        return score_post(text, platform, hashtags or [], tone, forbidden or [])

    def test_returns_dict_with_total_score_in_range(self):
        report = self._qc('Good content here. Call us today!')
        assert isinstance(report, dict)
        assert isinstance(report['total_score'], float)
        assert 0.0 <= report['total_score'] <= 1.0

    def test_excessive_hashtags_lower_platform_fit_score(self):
        many = [f'#tag{i}' for i in range(10)]
        low  = self._qc('Normal post here. Call us!', platform='x', hashtags=many)
        none = self._qc('Normal post here. Call us!', platform='x', hashtags=[])
        assert low['signals']['platform_fit']['score'] < none['signals']['platform_fit']['score']

    def test_over_length_post_hard_fails_platform_fit(self):
        long_text = 'word ' * 60  # well over 280 chars
        report = self._qc(long_text, platform='x')
        assert report['signals']['platform_fit']['score'] == 0.0

    def test_correct_length_raises_platform_fit_score(self):
        good = self._qc(
            'Fixed 47 AC units this summer alone. Zero callbacks. That is our standard.',
            platform='x'
        )
        assert good['signals']['platform_fit']['score'] > 0.5

    def test_ai_filler_phrases_lower_no_generic_filler_score(self):
        filler = self._qc('We are excited to share our game-changing, cutting-edge platform.')
        clean  = self._qc('We cut HVAC failure rates by 30% this season. Here is how.')
        assert filler['signals']['no_generic_filler']['score'] < clean['signals']['no_generic_filler']['score']

    def test_strong_hook_detected(self):
        report = self._qc('3 reasons your AC fails in summer.', platform='x')
        assert report['signals']['hook_strength']['score'] >= 1.0

    def test_weak_hook_scores_lower(self):
        report = self._qc('We are announcing a new service today.', platform='x')
        assert report['signals']['hook_strength']['score'] <= 0.5

    def test_clear_cta_raises_score(self):
        report = self._qc('HVAC failure rates dropped 40%. Reply if you want details.', platform='x')
        assert report['signals']['cta_clarity']['score'] >= 1.0

    def test_specificity_with_number(self):
        specific = self._qc('We reduced downtime by 40% in 3 months.', platform='x')
        vague    = self._qc('We reduced downtime significantly.', platform='x')
        assert specific['signals']['specificity']['score'] > vague['signals']['specificity']['score']

    def test_forbidden_claim_zero_brand_consistency(self):
        report = self._qc('This product is the best AI solution.', platform='x', forbidden=['best AI'])
        assert report['signals']['brand_consistency']['score'] == 0.0


# ============================================================================
# generator (mock LLM)
# ============================================================================

def _fake_llm(prompt: str) -> str:
    return 'Great HVAC deals. Call 555-1234 for a free quote today!'


class TestGenerator:

    @pytest.fixture(autouse=True)
    def patch_llm(self):
        import social.generator as gen
        with patch.object(gen, '_call_llm', side_effect=_fake_llm):
            yield

    def test_generate_campaign_returns_correct_count(self):
        from social.generator import generate_campaign
        posts = generate_campaign('HVAC', {}, 5, '2026-05-16')
        assert len(posts) == 5

    def test_posts_spaced_one_day_apart(self):
        from social.generator import generate_campaign
        posts = generate_campaign('Roofing', {}, 4, '2026-06-01')
        dates = [p['scheduled_at'][:10] for p in posts]
        assert dates == ['2026-06-01', '2026-06-02', '2026-06-03', '2026-06-04']

    def test_scheduled_at_starts_at_0800(self):
        from social.generator import generate_campaign
        posts = generate_campaign('Plumbing', {}, 3, '2026-05-20')
        for p in posts:
            assert 'T08:00:00' in p['scheduled_at']

    def test_each_post_has_required_keys(self):
        from social.generator import generate_campaign
        posts = generate_campaign('Electrical', {}, 2, '2026-05-16')
        for p in posts:
            assert 'content'       in p
            assert 'scheduled_at'  in p
            assert 'quality_score' in p

    def test_quality_score_is_float_in_range(self):
        from social.generator import generate_campaign
        posts = generate_campaign('Construction', {}, 3, '2026-05-16')
        for p in posts:
            assert isinstance(p['quality_score'], float)
            assert 0.0 <= p['quality_score'] <= 1.0

    def test_max_posts_capped_at_90(self):
        from social.generator import generate_campaign
        posts = generate_campaign('Test', {}, 999, '2026-05-16')
        assert len(posts) == 90


# ============================================================================
# Server endpoints
# ============================================================================

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import social.campaign_db as cdb
    monkeypatch.setattr(cdb, 'DB_PATH', tmp_path / 'social.db')
    monkeypatch.setattr(cdb, '_DB_DIR', tmp_path)
    cdb.init_db()

    import social.server as srv
    # point session store to tmp dir too
    from pathlib import Path as P
    monkeypatch.setattr(srv, 'DATA_DIR',    P(str(tmp_path)))
    monkeypatch.setattr(srv, 'SESSIONS_DB', P(str(tmp_path)) / 'sessions.db')
    srv._init_db()

    # wire campaign_db helpers in server to the monkeypatched cdb
    monkeypatch.setattr(srv, 'create_campaign',    cdb.create_campaign)
    monkeypatch.setattr(srv, '_db_insert_post',    cdb.insert_post)
    monkeypatch.setattr(srv, 'get_campaign_posts', cdb.get_campaign_posts)
    monkeypatch.setattr(srv, 'get_due_posts',      cdb.get_due_posts)
    monkeypatch.setattr(srv, '_db_approve_post',   cdb.approve_post)
    monkeypatch.setattr(srv, '_db_approve_all',    cdb.approve_all)
    monkeypatch.setattr(srv, 'update_post_status', cdb.update_post_status)

    srv.app.config['TESTING'] = True
    with srv.app.test_client() as c:
        yield c, cdb


class TestServerEndpoints:

    def test_healthz_returns_200_online(self, client):
        c, _ = client
        r = c.get('/healthz')
        assert r.status_code == 200
        assert json.loads(r.data)['status'] == 'online'

    def test_api_health_alias_returns_200(self, client):
        c, _ = client
        r = c.get('/api/health')
        assert r.status_code == 200

    def test_generate_returns_campaign_id_and_session_id(self, client):
        c, _ = client
        r = c.post('/generate', json={'topic': 'HVAC summer prep', 'platforms': ['x']})
        assert r.status_code == 200
        data = json.loads(r.data)
        assert 'campaign_id' in data
        assert 'session_id'  in data
        assert data['status'] == 'generating'

    def test_start_returns_session_id(self, client):
        c, _ = client
        r = c.post('/start', json={'topic': 'test', 'platforms': ['x']})
        assert r.status_code == 200
        data = json.loads(r.data)
        assert 'session_id' in data

    def test_get_campaign_posts_returns_list(self, client):
        c, cdb = client
        cid = cdb.create_campaign('test topic', {}, 2, date.today().isoformat())
        cdb.insert_post(cid, 'x',        'post body x',  date.today().isoformat() + 'T08:00:00')
        cdb.insert_post(cid, 'linkedin', 'post body li', date.today().isoformat() + 'T08:00:00')
        r = c.get(f'/campaigns/{cid}/posts')
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data['count'] == 2
        assert isinstance(data['posts'], list)

    def test_get_campaign_posts_filtered_by_status(self, client):
        c, cdb = client
        cid = cdb.create_campaign('filter test', {}, 3, date.today().isoformat())
        p1  = cdb.insert_post(cid, 'x', 'A', date.today().isoformat() + 'T08:00:00')
        cdb.insert_post(cid, 'x', 'B', date.today().isoformat() + 'T08:00:00')
        cdb.insert_post(cid, 'x', 'C', date.today().isoformat() + 'T08:00:00')
        cdb.approve_post(p1, 'admin')
        r = c.get(f'/campaigns/{cid}/posts?status=approved')
        assert json.loads(r.data)['count'] == 1

    def test_campaigns_approve_all_returns_count(self, client):
        c, cdb = client
        cid = cdb.create_campaign('test', {}, 3, date.today().isoformat())
        for i in range(3):
            cdb.insert_post(cid, 'x', f'post {i}', date.today().isoformat() + 'T08:00:00')
        r = c.post(f'/campaigns/{cid}/approve_all', json={'approved_by': 'owner'})
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data['approved'] == 3
        assert data['campaign_id'] == cid

    def test_campaigns_approve_all_idempotent(self, client):
        c, cdb = client
        cid = cdb.create_campaign('test', {}, 2, date.today().isoformat())
        for i in range(2):
            cdb.insert_post(cid, 'x', f'post {i}', date.today().isoformat() + 'T08:00:00')
        c.post(f'/campaigns/{cid}/approve_all', json={})
        r2 = c.post(f'/campaigns/{cid}/approve_all', json={})
        assert json.loads(r2.data)['approved'] == 0  # nothing left to approve

    def test_reject_post_endpoint(self, client):
        c, cdb = client
        cid = cdb.create_campaign('test', {}, 1, date.today().isoformat())
        pid = cdb.insert_post(cid, 'x', 'content', date.today().isoformat() + 'T08:00:00')
        r   = c.post(f'/campaigns/{cid}/posts/{pid}/reject')
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data['status'] == 'rejected'
        assert data['id'] == pid

    def test_reject_nonexistent_post_returns_404(self, client):
        c, _ = client
        r = c.post('/campaigns/fake-cid/posts/fake-pid/reject')
        assert r.status_code == 404

    def test_publish_due_returns_published_and_failed_counts(self, client):
        c, cdb = client
        past = '2020-01-01T08:00:00'
        cid  = cdb.create_campaign('test', {}, 1, '2020-01-01')
        pid  = cdb.insert_post(cid, 'x', 'publish me', past)
        cdb.approve_post(pid)

        mock_result = {'success': True, 'post_id': 'sim_abc', 'url': 'https://x.com/i/web/status/sim', 'simulated': True}
        with patch('social.connectors.x_connector.post_tweet', return_value=mock_result):
            r = c.post(f'/campaigns/{cid}/publish_due')

        assert r.status_code == 200
        data = json.loads(r.data)
        assert data['published'] == 1
        assert data['failed']    == 0

    def test_publish_due_handles_x_connector_failure(self, client):
        c, cdb = client
        past = '2020-01-01T08:00:00'
        cid  = cdb.create_campaign('test', {}, 1, '2020-01-01')
        pid  = cdb.insert_post(cid, 'x', 'fail post', past)
        cdb.approve_post(pid)

        mock_fail = {'success': False, 'error': 'API rate limit exceeded'}
        with patch('social.connectors.x_connector.post_tweet', return_value=mock_fail):
            r = c.post(f'/campaigns/{cid}/publish_due')

        data = json.loads(r.data)
        assert data['published'] == 0
        assert data['failed']    == 1

    def test_approve_all_session_endpoint(self, client):
        c, cdb = client
        cid = cdb.create_campaign('test', {}, 2, date.today().isoformat())
        for i in range(2):
            cdb.insert_post(cid, 'x', f'post {i}', date.today().isoformat() + 'T08:00:00')

        import social.server as srv
        sid = 'soc_testapproveall'
        srv.put_session(sid, {
            'session_id':  sid,
            'campaign_id': cid,
            'state':       {'platforms': ['x'], 'platform_drafts': {}},
            'status':      'pending_approval',
            'revision':    1,
            'history':     [],
            'decisions':   {},
            'created_at':  '2026-01-01T00:00:00',
            'updated_at':  '2026-01-01T00:00:00',
        })
        r = c.post('/approve_all', json={'session_id': sid})
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data['approved_count'] == 2
        assert data['status'] == 'approved'
