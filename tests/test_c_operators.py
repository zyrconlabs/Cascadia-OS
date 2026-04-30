"""Tests for C1–C9 operators: Invoice, Appointment, FollowUp, Review, Intake, DocSummarizer, Budget, Social, CONNECT."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cascadia.depot.manifest_validator import validate_depot_manifest

BASE_OPS = Path(__file__).parent.parent / 'cascadia' / 'operators'

# ── Manifest validation ───────────────────────────────────────────────────────

@pytest.mark.parametrize('dirname,expected_id,expected_port', [
    ('invoice_generator',    'invoice-generator',    8101),
    ('appointment_scheduler','appointment-scheduler', 8102),
    ('followup_sequence',    'followup-sequence',     8103),
    ('review_requester',     'review-requester',      8104),
    ('intake_form',          'intake-form',           8105),
    ('document_summarizer',  'document-summarizer',   8106),
    ('budget_tracker',       'budget-tracker',        8107),
    ('social_scheduler',     'social-scheduler',      8108),
    ('connect',              'connect',               8200),
])
def test_manifest_valid(dirname, expected_id, expected_port):
    path = BASE_OPS / dirname / 'manifest.json'
    assert path.exists(), f"manifest.json missing in {dirname}"
    data = json.loads(path.read_text())
    result = validate_depot_manifest(data)
    assert result.valid, f"{dirname}: {result.errors}"
    assert data['id'] == expected_id
    assert data['port'] == expected_port
    assert data['type'] == 'operator'
    assert data['installed_by_default'] is False


@pytest.mark.parametrize('dirname', [
    'invoice_generator', 'appointment_scheduler', 'followup_sequence',
    'review_requester', 'intake_form', 'document_summarizer',
    'budget_tracker', 'social_scheduler', 'connect',
])
def test_required_files_present(dirname):
    d = BASE_OPS / dirname
    for fname in ('manifest.json', 'operator.py', 'health.py', 'install.sh', 'uninstall.sh', 'README.md'):
        assert (d / fname).exists(), f"{dirname}/{fname} missing"


# ── C1: Invoice Generator ─────────────────────────────────────────────────────

from cascadia.operators.invoice_generator.operator import (
    NAME as INV_NAME, PORT as INV_PORT,
    generate_invoice, execute_task as inv_exec, handle_event as inv_handle,
)

def test_invoice_metadata():
    assert INV_NAME == 'invoice-generator'
    assert INV_PORT == 8101


def test_invoice_generate():
    result = generate_invoice('ACME Corp', 'acme@example.com', [
        {'description': 'Consulting', 'quantity': 10, 'unit_price': 150.0}
    ])
    assert result['client_name'] == 'ACME Corp'
    assert result['total'] == 1500.0


def test_invoice_send_requires_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'send_invoice', 'client_email': 'a@b.com', 'invoice_text': 'INV...'}).encode()
    asyncio.run(inv_handle(nc, 'cascadia.operators.invoice-generator.call', raw))
    assert any('approvals' in s for s in published)


def test_invoice_generate_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'generate_invoice', 'client_name': 'X', 'client_email': 'x@y.com',
                      'items': [{'description': 'A', 'qty': 1, 'rate': 100}]}).encode()
    asyncio.run(inv_handle(nc, 'cascadia.operators.invoice-generator.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)


def test_invoice_exec_missing_action():
    assert inv_exec({})['ok'] is False


# ── C2: Appointment Scheduler ─────────────────────────────────────────────────

from cascadia.operators.appointment_scheduler.operator import (
    NAME as APPT_NAME, PORT as APPT_PORT,
    create_appointment, list_appointments,
    execute_task as appt_exec, handle_event as appt_handle,
)

def test_appointment_metadata():
    assert APPT_NAME == 'appointment-scheduler'
    assert APPT_PORT == 8102


def test_appointment_create_and_list():
    result = create_appointment('Jane Doe', 'jane@example.com', '2026-05-01', '10:00', 60)
    assert 'appointment_id' in result
    appt_id = result['appointment_id']
    listed = list_appointments()
    assert any(a['appointment_id'] == appt_id for a in (listed if isinstance(listed, list) else listed.get('appointments', [])))


def test_appointment_send_confirmation_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'send_confirmation', 'appointment_id': 'APT001'}).encode()
    asyncio.run(appt_handle(nc, 'cascadia.operators.appointment-scheduler.call', raw))
    assert any('approvals' in s for s in published)


def test_appointment_list_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'list_appointments'}).encode()
    asyncio.run(appt_handle(nc, 'cascadia.operators.appointment-scheduler.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)


# ── C3: Follow-Up Sequence ────────────────────────────────────────────────────

from cascadia.operators.followup_sequence.operator import (
    NAME as FU_NAME, PORT as FU_PORT,
    create_sequence, enroll_contact,
    execute_task as fu_exec, handle_event as fu_handle,
)

def test_followup_metadata():
    assert FU_NAME == 'followup-sequence'
    assert FU_PORT == 8103


def test_followup_create_and_enroll():
    seq = create_sequence('Welcome Series', [
        {'subject': 'Welcome!', 'body': 'Hi there', 'delay_days': 0},
        {'subject': 'Check In', 'body': 'How are you?', 'delay_days': 3},
    ])
    assert 'sequence_id' in seq
    seq_id = seq['sequence_id']
    enroll = enroll_contact('bob@example.com', 'Bob', seq_id)
    assert enroll.get('ok') is True or 'enrollment_id' in enroll or ('enrollment' in enroll and 'enrollment_id' in enroll['enrollment'])


def test_followup_send_step_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'send_step', 'enrollment_id': 'E001'}).encode()
    asyncio.run(fu_handle(nc, 'cascadia.operators.followup-sequence.call', raw))
    assert any('approvals' in s for s in published)


# ── C4: Review Requester ──────────────────────────────────────────────────────

from cascadia.operators.review_requester.operator import (
    NAME as RR_NAME, PORT as RR_PORT,
    create_campaign, queue_request, list_pending,
    execute_task as rr_exec, handle_event as rr_handle,
)

def test_review_requester_metadata():
    assert RR_NAME == 'review-requester'
    assert RR_PORT == 8104


def test_review_create_campaign_and_queue():
    camp = create_campaign('Post-Purchase', 'google', 'https://g.co/r/abc', 'Please leave a review!')
    assert 'campaign_id' in camp
    cid = camp['campaign_id']
    queued = queue_request(cid, 'alice@example.com', 'Alice', 'ORD-001')
    assert queued.get('ok') is True or 'request_id' in queued or ('request' in queued and 'request_id' in queued['request'])
    pending = list_pending(cid)
    pending_list = pending if isinstance(pending, list) else pending.get('requests', [])
    assert len(pending_list) >= 1


def test_review_send_requires_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'send_requests', 'campaign_id': 'C001'}).encode()
    asyncio.run(rr_handle(nc, 'cascadia.operators.review-requester.call', raw))
    assert any('approvals' in s for s in published)


# ── C5: Intake Form ───────────────────────────────────────────────────────────

from cascadia.operators.intake_form.operator import (
    NAME as IF_NAME, PORT as IF_PORT,
    define_form, submit_form,
    execute_task as if_exec, handle_event as if_handle,
)

def test_intake_form_metadata():
    assert IF_NAME == 'intake-form'
    assert IF_PORT == 8105


def test_intake_define_and_submit():
    define_form('contact', 'Contact Us', [
        {'name': 'name', 'required': True, 'type': 'text'},
        {'name': 'email', 'required': True, 'type': 'email'},
    ])
    result = submit_form('contact', {'name': 'Bob', 'email': 'bob@example.com'})
    assert result['ok'] is True
    assert not result.get('errors')


def test_intake_submit_validation_error():
    define_form('signup', 'Sign Up', [
        {'name': 'email', 'required': True, 'type': 'email'},
    ])
    result = submit_form('signup', {'email': 'not-an-email'})
    assert result['ok'] is False or result.get('errors')


def test_intake_route_requires_approval():
    define_form('route_test', 'Route Test', [{'name': 'name', 'required': True, 'type': 'text'}])
    submission = submit_form('route_test', {'name': 'Tester'})
    submission_id = submission.get('submission_id', 'FALLBACK')

    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'route_submission', 'submission_id': submission_id,
                      'target_operator': 'lead-intake', 'target_action': 'process'}).encode()
    asyncio.run(if_handle(nc, 'cascadia.operators.intake-form.call', raw))
    assert any('approvals' in s for s in published)


# ── C6: Document Summarizer ───────────────────────────────────────────────────

from cascadia.operators.document_summarizer.operator import (
    NAME as DS_NAME, PORT as DS_PORT,
    summarize_text, extract_keywords,
    execute_task as ds_exec, handle_event as ds_handle,
)

def test_doc_summarizer_metadata():
    assert DS_NAME == 'document-summarizer'
    assert DS_PORT == 8106


def test_summarize_text():
    text = "The quick brown fox jumps over the lazy dog. Dogs are common pets. Foxes are wild animals. Many animals live in forests. The forest ecosystem is complex."
    result = summarize_text(text, max_sentences=2)
    summary = result['summary'] if isinstance(result, dict) else result
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_extract_keywords():
    text = "Machine learning models require large datasets for training purposes. Training data quality affects model performance significantly."
    result = extract_keywords(text, top_n=5)
    keywords = result['keywords'] if isinstance(result, dict) else result
    assert isinstance(keywords, list)
    assert len(keywords) <= 5


def test_doc_export_requires_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'export_summary', 'text': 'Hello world.', 'file_path': '/tmp/summary.txt'}).encode()
    asyncio.run(ds_handle(nc, 'cascadia.operators.document-summarizer.call', raw))
    assert any('approvals' in s for s in published)


def test_doc_summarize_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'summarize_text', 'text': 'Hello world. This is a test.'}).encode()
    asyncio.run(ds_handle(nc, 'cascadia.operators.document-summarizer.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)


# ── C7: Budget Tracker ────────────────────────────────────────────────────────

from cascadia.operators.budget_tracker.operator import (
    NAME as BT_NAME, PORT as BT_PORT,
    create_budget, log_expense, get_budget,
    execute_task as bt_exec, handle_event as bt_handle,
)

def test_budget_metadata():
    assert BT_NAME == 'budget-tracker'
    assert BT_PORT == 8107


def test_budget_create_and_expense():
    b = create_budget('Q2 Marketing', 10000, 'USD', 'marketing')
    assert 'budget_id' in b
    bid = b['budget_id']
    log_expense(bid, 500, 'Facebook Ads', 'Meta')
    budget = get_budget(bid)
    b_data = budget.get('budget', budget)
    assert b_data.get('spent') == 500 or budget.get('spent') == 500
    assert b_data.get('remaining') == 9500 or budget.get('remaining') == 9500


def test_budget_export_requires_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'export_report'}).encode()
    asyncio.run(bt_handle(nc, 'cascadia.operators.budget-tracker.call', raw))
    assert any('approvals' in s for s in published)


def test_budget_list_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'list_budgets'}).encode()
    asyncio.run(bt_handle(nc, 'cascadia.operators.budget-tracker.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)


# ── C8: Social Post Scheduler ─────────────────────────────────────────────────

from cascadia.operators.social_scheduler.operator import (
    NAME as SS_NAME, PORT as SS_PORT,
    create_post, list_posts, cancel_post,
    execute_task as ss_exec, handle_event as ss_handle,
)

def test_social_scheduler_metadata():
    assert SS_NAME == 'social-scheduler'
    assert SS_PORT == 8108


def test_social_create_and_list():
    post = create_post('linkedin', 'Excited to share our new product!', '2026-05-15T09:00:00', ['product', 'launch'])
    assert 'post_id' in post
    pid = post['post_id']
    posts = list_posts()
    post_list = posts.get('posts', posts) if isinstance(posts, dict) else posts
    assert any(p['post_id'] == pid for p in post_list)


def test_social_invalid_platform():
    result = create_post('myspace', 'Hello!', '2026-05-15T09:00:00')
    assert result.get('ok') is False or 'error' in result


def test_social_publish_requires_approval():
    post = create_post('twitter', 'Test publish approval', '2026-05-15T09:00:00')
    pid = post['post_id']

    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'publish_post', 'post_id': pid}).encode()
    asyncio.run(ss_handle(nc, 'cascadia.operators.social-scheduler.call', raw))
    assert any('approvals' in s for s in published)


def test_social_list_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'list_posts'}).encode()
    asyncio.run(ss_handle(nc, 'cascadia.operators.social-scheduler.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)


# ── C9: CONNECT ───────────────────────────────────────────────────────────────

from cascadia.operators.connect.operator import (
    NAME as CONN_NAME, PORT as CONN_PORT,
    register_webhook, list_webhooks,
    execute_task as conn_exec, handle_event as conn_handle,
)

def test_connect_metadata():
    assert CONN_NAME == 'connect'
    assert CONN_PORT == 8200


def test_connect_register_webhook_direct():
    result = register_webhook('wh1', 'My Hook', 'cascadia.operators.lead-intake.call')
    hooks = list_webhooks()
    hooks_list = hooks.get('webhooks', hooks) if isinstance(hooks, dict) else hooks
    assert any(h.get('webhook_id') == 'wh1' or h.get('id') == 'wh1' for h in hooks_list)


def test_connect_http_outbound_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'http_outbound', 'url': 'https://api.example.com/data',
                      'method': 'POST', 'headers': {}, 'body': {}}).encode()
    asyncio.run(conn_handle(nc, 'cascadia.operators.connect.call', raw))
    assert any('approvals' in s for s in published)


def test_connect_list_webhooks_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'list_webhooks'}).encode()
    asyncio.run(conn_handle(nc, 'cascadia.operators.connect.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)


def test_connect_exec_missing_action():
    result = conn_exec({})
    assert result.get('ok') is False or 'error' in result
