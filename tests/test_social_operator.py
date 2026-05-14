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
sdk_stub.vault_get         = lambda k: None
sdk_stub.vault_store       = lambda k, v: False
sdk_stub.sentinel_check    = lambda a, c=None: {'allowed': True}
sdk_stub.crew_register     = lambda m: False
sys.modules.setdefault('cascadia_sdk', sdk_stub)


# ---------------------------------------------------------------------------
# DB fixture — isolated temp DB per test
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    import operators.social.campaign_db as cdb
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
        pid_pending = db.insert_post(cid, 'x', 'Pending',     '2020-06-01T08:00:00')
        db.approve_post(pid_past, 'admin')
        db.approve_post(pid_future, 'admin')
        # pid_pending stays pending_approval
        due = db.get_due_posts()
        due_ids = {p['id'] for p in due}
        assert pid_past in due_ids
        assert pid_future not in due_ids
        assert pid_pending not in due_ids

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
            db.insert_post(cid, 'x', f'Post {i}', f'2026-05-1{6+i}T08:00:00')
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
        p1 = db.insert_post(cid, 'x', 'A', '2026-05-16T08:00:00')
        p2 = db.insert_post(cid, 'x', 'B', '2026-05-17T08:00:00')
        db.insert_post(cid, 'x', 'C', '2026-05-18T08:00:00')
        db.approve_post(p1, 'admin')
        db.reject_post(p2)
        assert len(db.get_campaign_posts(cid, 'approved'))          == 1
        assert len(db.get_campaign_posts(cid, 'rejected'))          == 1
        assert len(db.get_campaign_posts(cid, 'pending_approval'))  == 1

    def test_init_db_is_idempotent(self, db):
        db.init_db()
        db.init_db()  # must not raise
        cid = db.create_campaign('Safe', {}, 1, '2026-05-16')
        assert cid


# ============================================================================
# quality_check
# ============================================================================

class TestQualityCheck:

    def _qc(self):
        from operators.social.quality_check import score_post
        return score_post

    def test_returns_float_in_range(self):
        score = self._qc()('Good content here', 'x')
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_excessive_hashtags_lower_score(self):
        low  = self._qc()('Normal post text here. Call us today! #one #two #three #four #five', 'x')
        high = self._qc()('Normal post text here. Call us today!', 'x')
        assert low < high

    def test_all_caps_lowers_score(self):
        low  = self._qc()('THIS IS GREAT STUFF BUY NOW TODAY DEALS', 'x')
        high = self._qc()('This is great. Buy now for deals today.', 'x')
        assert low < high

    def test_correct_length_for_x_raises_score(self):
        short   = self._qc()('Hi.', 'x')
        optimal = self._qc()('We fix HVAC systems fast. 20 years serving local homeowners. Call us today for same-day service. 555-1234.', 'x')
        assert optimal > short

    def test_ai_phrases_lower_score(self):
        ai_text  = self._qc()('In conclusion, it is worth noting that our services are comprehensive.', 'x')
        normal   = self._qc()('We fix your heating system same day. Call 555-1234.', 'x')
        assert normal > ai_text

    def test_hook_raises_score(self):
        with_hook    = self._qc()('Did you know most HVAC systems fail in summer? Call us before it happens.', 'x')
        without_hook = self._qc()('We have been serving customers for twenty years in this area.', 'x')
        assert with_hook >= without_hook

    def test_cta_raises_score(self):
        with_cta    = self._qc()('HVAC tune-up season is here. Call 555-1234 for a free quote.', 'x')
        without_cta = self._qc()('HVAC tune-up season is here. Systems need maintenance in summer.', 'x')
        assert with_cta >= without_cta


# ============================================================================
# generator (mock LLM)
# ============================================================================

def _fake_llm(prompt: str) -> str:
    return 'Great HVAC deals. Call 555-1234 for a free quote today!'


class TestGenerator:

    @pytest.fixture(autouse=True)
    def patch_llm(self):
        import operators.social.generator as gen
        with patch.object(gen, '_call_llm', side_effect=_fake_llm):
            yield

    def test_generate_campaign_returns_correct_count(self):
        from operators.social.generator import generate_campaign
        posts = generate_campaign('HVAC', {}, 5, '2026-05-16')
        assert len(posts) == 5

    def test_posts_spaced_one_day_apart(self):
        from operators.social.generator import generate_campaign
        posts = generate_campaign('Roofing', {}, 4, '2026-06-01')
        dates = [p['scheduled_at'][:10] for p in posts]
        assert dates == ['2026-06-01', '2026-06-02', '2026-06-03', '2026-06-04']

    def test_scheduled_at_starts_at_start_date_0800(self):
        from operators.social.generator import generate_campaign
        posts = generate_campaign('Plumbing', {}, 3, '2026-05-20')
        for p in posts:
            assert 'T08:00:00' in p['scheduled_at']

    def test_each_post_has_required_keys(self):
        from operators.social.generator import generate_campaign
        posts = generate_campaign('Electrical', {}, 2, '2026-05-16')
        for p in posts:
            assert 'content'      in p
            assert 'scheduled_at' in p
            assert 'quality_score' in p

    def test_quality_score_is_float_in_range(self):
        from operators.social.generator import generate_campaign
        posts = generate_campaign('Construction', {}, 3, '2026-05-16')
        for p in posts:
            assert isinstance(p['quality_score'], float)
            assert 0.0 <= p['quality_score'] <= 1.0


# ============================================================================
# Server endpoints
# ============================================================================

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import operators.social.campaign_db as cdb
    monkeypatch.setattr(cdb, 'DB_PATH', tmp_path / 'social.db')
    monkeypatch.setattr(cdb, '_DB_DIR', tmp_path)
    cdb.init_db()

    import operators.social.generator as gen
    def _fake_generate(topic, business_context, num_posts, start_date, platform='x'):
        base = date.fromisoformat(start_date)
        return [
            {
                'content':      f'Post {i} about {topic}',
                'scheduled_at': f'{(base + timedelta(days=i)).isoformat()}T08:00:00',
                'quality_score': 0.82,
            }
            for i in range(num_posts)
        ]
    monkeypatch.setattr(gen, 'generate_campaign', _fake_generate)

    import operators.social.server as srv
    srv.app.config['TESTING'] = True
    with srv.app.test_client() as c:
        yield c


class TestServerEndpoints:

    def test_healthz_returns_200(self, client):
        r = client.get('/healthz')
        assert r.status_code == 200
        assert json.loads(r.data)['status'] == 'ok'

    def test_generate_returns_campaign_id_and_num_posts(self, client):
        r = client.post('/generate', json={'topic': 'HVAC', 'num_posts': 3, 'start_date': '2026-06-01'})
        assert r.status_code == 200
        data = json.loads(r.data)
        assert 'campaign_id' in data
        assert data['num_posts'] == 3
        assert data['status'] == 'pending_approval'

    def test_generate_missing_topic_returns_400(self, client):
        r = client.post('/generate', json={'num_posts': 3})
        assert r.status_code == 400

    def test_approve_all_returns_approved_count(self, client):
        r = client.post('/generate', json={'topic': 'Roofing', 'num_posts': 4, 'start_date': '2026-06-01'})
        cid = json.loads(r.data)['campaign_id']
        r2  = client.post(f'/campaigns/{cid}/approve_all', json={'approved_by': 'owner'})
        assert r2.status_code == 200
        assert json.loads(r2.data)['approved'] == 4

    def test_list_posts_returns_all(self, client):
        r   = client.post('/generate', json={'topic': 'Plumbing', 'num_posts': 3, 'start_date': '2026-06-01'})
        cid = json.loads(r.data)['campaign_id']
        r2  = client.get(f'/campaigns/{cid}/posts')
        data = json.loads(r2.data)
        assert data['count'] == 3

    def test_list_posts_filtered_by_status(self, client):
        r   = client.post('/generate', json={'topic': 'HVAC', 'num_posts': 4, 'start_date': '2026-06-01'})
        cid = json.loads(r.data)['campaign_id']
        r2  = client.get(f'/campaigns/{cid}/posts?status=pending_approval')
        assert json.loads(r2.data)['count'] == 4

    def test_publish_due_returns_published_and_failed_counts(self, client):
        r   = client.post('/generate', json={'topic': 'Electrical', 'num_posts': 2, 'start_date': '2020-01-01'})
        cid = json.loads(r.data)['campaign_id']
        client.post(f'/campaigns/{cid}/approve_all', json={'approved_by': 'admin'})

        import operators.social.connectors.x_connector as xc
        with patch.object(xc, 'post_tweet', return_value={'success': True, 'post_id': 'tw_123', 'url': 'https://x.com/x'}):
            r2 = client.post(f'/campaigns/{cid}/publish_due')
        data = json.loads(r2.data)
        assert data['published'] == 2
        assert data['failed']    == 0

    def test_reject_post(self, client):
        r   = client.post('/generate', json={'topic': 'Test', 'num_posts': 1, 'start_date': '2026-06-01'})
        cid = json.loads(r.data)['campaign_id']
        import operators.social.campaign_db as cdb
        posts = cdb.get_campaign_posts(cid)
        pid   = posts[0]['id']
        r2    = client.post(f'/campaigns/{cid}/posts/{pid}/reject')
        assert r2.status_code == 200
        assert json.loads(r2.data)['status'] == 'rejected'
