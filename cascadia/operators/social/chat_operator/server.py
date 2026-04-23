"""
operators/social/chat_operator/server.py
-----------------------------------------
Social Chat Operator — port 8011

The conversational interface for campaign creation and approval.

Responsibilities:
  - Accept a campaign brief and run the full pipeline
  - Present posts as approval cards in the chat feed
  - Handle inline edits, re-run QC on edited content
  - Handle decline-with-note: rewrite only flagged post,
    re-run QC, resubmit FULL package for approval
  - On full approval, hand off to publisher
  - Push state to PRISM approval tab simultaneously

Does NOT own:
  - QC logic (quality_check.py owns that)
  - Publishing (workflow_runtime owns that)
  - Email notification (email operator owns that)
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS

# Ensure imports resolve from repo root
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from operators.social.pipeline.content_pipeline import (
    build_master_package,
    render_platform,
    mark_published,
)
from operators.social.pipeline.campaign_planner import build_campaign_plan
from operators.social.pipeline.quality_check import (
    run_quality_check,
    build_rewrite_instructions,
    MIN_POST_SCORE,
)

app = Flask(__name__)
CORS(app)

PORT      = int(os.environ.get('SOCIAL_CHAT_PORT', '8011'))
DATA_DIR  = Path(__file__).parent / 'data'
DATA_DIR.mkdir(exist_ok=True)
SESSIONS  = DATA_DIR / 'sessions.json'

PRISM_PORT     = 6300
EMAIL_PORT     = 8010
MAX_REVISIONS  = 5


# ---------------------------------------------------------------------------
# Session store — one session per campaign run
# ---------------------------------------------------------------------------

def load_sessions() -> Dict[str, Any]:
    if SESSIONS.exists():
        try:
            return json.loads(SESSIONS.read_text())
        except Exception:
            pass
    return {}


def save_sessions(sessions: Dict[str, Any]) -> None:
    SESSIONS.write_text(json.dumps(sessions, indent=2, default=str))


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    return load_sessions().get(session_id)


def put_session(session_id: str, data: Dict[str, Any]) -> None:
    sessions = load_sessions()
    sessions[session_id] = data
    save_sessions(sessions)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _run_pipeline(brief: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run compose → render → QC with automatic retry.
    Returns final state with qc_passed, platform_drafts, qc_report.
    """
    state = dict(brief)
    state = build_master_package(state)

    platforms = state.get('platforms') or ['x']
    for platform in platforms:
        state = render_platform(state, platform)

    for _ in range(3):
        state = run_quality_check(state)
        if state.get('qc_passed') or state.get('qc_hard_fail'):
            break
        state = build_rewrite_instructions(state)

    return state


def _rewrite_one_post(
    state: Dict[str, Any],
    platform: str,
    note: str,
) -> Dict[str, Any]:
    """
    Rewrite only the flagged platform post using the decline note,
    re-run QC on just that post, return the full updated state.
    """
    state = dict(state)

    # Inject the note as a targeted rewrite instruction
    rewrite_tasks = state.get('rewrite_tasks') or {}
    rewrite_tasks[platform] = {
        'instruction': note,
        'current_score': 0,
        'target_score': MIN_POST_SCORE,
        'priority_fixes': [note],
    }
    state['rewrite_tasks'] = rewrite_tasks
    state['decline_note']  = note

    # Re-render only the declined platform
    state = render_platform(state, platform)

    # Re-run QC on full package (all posts) — preserves approved posts
    state['qc_iteration'] = 0  # reset iteration counter for clean retry
    for _ in range(3):
        state = run_quality_check(state)
        if state.get('qc_passed') or state.get('qc_hard_fail'):
            break
        state = build_rewrite_instructions(state)

    return state


def _notify_prism(session_id: str, state: Dict[str, Any]) -> None:
    """Push updated campaign state to PRISM approval tab."""
    import urllib.request as ur
    try:
        data = json.dumps({
            'session_id': session_id,
            'state':      state,
            'source':     'social_chat_operator',
        }).encode()
        req = ur.Request(
            f'http://127.0.0.1:{PRISM_PORT}/api/prism/campaign/notify',
            data=data, method='POST',
            headers={'Content-Type': 'application/json'},
        )
        ur.urlopen(req, timeout=2)
    except Exception:
        pass  # PRISM notification is best-effort


def _notify_email(state: Dict[str, Any]) -> None:
    """Fire email notification when posts ready for review."""
    import urllib.request as ur
    try:
        data = json.dumps({'state': state}).encode()
        req = ur.Request(
            f'http://127.0.0.1:{EMAIL_PORT}/send_campaign_review',
            data=data, method='POST',
            headers={'Content-Type': 'application/json'},
        )
        ur.urlopen(req, timeout=3)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/start', methods=['POST'])
def start_campaign():
    """
    Start a new campaign from a brief.
    Returns session_id and the first approval package.
    """
    brief      = request.get_json(force=True) or {}
    session_id = f'soc_{uuid.uuid4().hex[:10]}'

    state = _run_pipeline(brief)

    session = {
        'session_id':   session_id,
        'brief':        brief,
        'revision':     1,
        'state':        state,
        'history':      [],
        'decisions':    {},
        'status':       'pending_approval',
        'created_at':   _now(),
        'updated_at':   _now(),
    }
    put_session(session_id, session)
    _notify_prism(session_id, state)
    _notify_email(state)

    return jsonify({
        'session_id':  session_id,
        'revision':    1,
        'qc_passed':   state.get('qc_passed'),
        'qc_score':    state.get('qc_report', {}).get('campaign_score'),
        'posts':       _format_posts(state),
        'status':      'pending_approval',
    })


@app.route('/approve', methods=['POST'])
def approve_package():
    """
    Approve the full package. Triggers publishing.
    """
    data       = request.get_json(force=True) or {}
    session_id = data.get('session_id', '')
    actor      = data.get('actor', 'operator')

    session = get_session(session_id)
    if not session:
        return jsonify({'error': 'session not found'}), 404

    session['status']     = 'approved'
    session['approved_by'] = actor
    session['approved_at'] = _now()
    session['updated_at']  = _now()

    # Simulate publish (real API wired in connectors)
    state    = session['state']
    post_ids = {}
    for platform in (state.get('platforms') or []):
        post_id = f'{platform}_{session_id}_approved'
        state   = mark_published(state, platform, post_id)
        post_ids[platform] = post_id

    session['state']     = state
    session['post_ids']  = post_ids
    put_session(session_id, session)

    return jsonify({
        'session_id': session_id,
        'status':     'approved',
        'post_ids':   post_ids,
        'message':    'All posts approved. Publishing initiated.',
    })


@app.route('/decline', methods=['POST'])
def decline_post():
    """
    Decline a specific platform post with a note.
    Rewrites that post, re-runs QC, resubmits full package.
    """
    data       = request.get_json(force=True) or {}
    session_id = data.get('session_id', '')
    platform   = data.get('platform', '')
    note       = data.get('note', '').strip()

    if not platform or not note:
        return jsonify({'error': 'platform and note are required'}), 400

    session = get_session(session_id)
    if not session:
        return jsonify({'error': 'session not found'}), 404

    if session['revision'] >= MAX_REVISIONS:
        return jsonify({'error': f'Maximum revisions ({MAX_REVISIONS}) reached. Manual review required.'}), 400

    # Archive current state in history
    session['history'].append({
        'revision':   session['revision'],
        'state':      session['state'],
        'decline':    {'platform': platform, 'note': note},
        'archived_at': _now(),
    })

    # Rewrite and resubmit
    new_state = _rewrite_one_post(session['state'], platform, note)
    session['revision']   += 1
    session['state']       = new_state
    session['status']      = 'pending_approval'
    session['updated_at']  = _now()
    put_session(session_id, session)

    _notify_prism(session_id, new_state)
    _notify_email(new_state)

    return jsonify({
        'session_id':  session_id,
        'revision':    session['revision'],
        'qc_passed':   new_state.get('qc_passed'),
        'qc_score':    new_state.get('qc_report', {}).get('campaign_score'),
        'posts':       _format_posts(new_state),
        'status':      'pending_approval',
        'message':     f'Revision {session["revision"]} ready. Full package resubmitted for approval.',
    })


@app.route('/edit', methods=['POST'])
def edit_post():
    """
    Operator edits post text directly. Runs QC on the edit.
    Returns updated score. If passes, resubmits full package.
    """
    data       = request.get_json(force=True) or {}
    session_id = data.get('session_id', '')
    platform   = data.get('platform', '')
    new_text   = data.get('text', '').strip()

    if not platform or not new_text:
        return jsonify({'error': 'platform and text are required'}), 400

    session = get_session(session_id)
    if not session:
        return jsonify({'error': 'session not found'}), 404

    state  = dict(session['state'])
    drafts = dict(state.get('platform_drafts') or {})

    # Apply the manual edit
    if platform in drafts:
        drafts[platform] = {**drafts[platform], 'body': new_text}
    else:
        drafts[platform] = {'body': new_text, 'hashtags': [], 'platform': platform}
    state['platform_drafts'] = drafts

    # Re-run QC on the edited package
    state['qc_iteration'] = 0
    for _ in range(3):
        state = run_quality_check(state)
        if state.get('qc_passed') or state.get('qc_hard_fail'):
            break
        state = build_rewrite_instructions(state)

    session['state']      = state
    session['updated_at'] = _now()
    if state.get('qc_passed'):
        session['status'] = 'pending_approval'
    put_session(session_id, session)

    if state.get('qc_passed'):
        _notify_prism(session_id, state)

    return jsonify({
        'session_id': session_id,
        'qc_passed':  state.get('qc_passed'),
        'qc_score':   state.get('qc_report', {}).get('campaign_score'),
        'posts':      _format_posts(state),
        'status':     session['status'],
    })


@app.route('/session/<session_id>', methods=['GET'])
def get_session_state(session_id):
    session = get_session(session_id)
    if not session:
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'session_id': session_id,
        'revision':   session.get('revision'),
        'status':     session.get('status'),
        'qc_score':   session['state'].get('qc_report', {}).get('campaign_score'),
        'posts':      _format_posts(session['state']),
    })


@app.route('/sessions', methods=['GET'])
def list_sessions():
    sessions = load_sessions()
    result = []
    for sid, s in sessions.items():
        result.append({
            'session_id':  sid,
            'topic':       s.get('brief', {}).get('topic', ''),
            'revision':    s.get('revision', 1),
            'status':      s.get('status', ''),
            'qc_score':    s['state'].get('qc_report', {}).get('campaign_score'),
            'updated_at':  s.get('updated_at', ''),
        })
    result.sort(key=lambda x: x['updated_at'], reverse=True)
    return jsonify({'sessions': result, 'count': len(result)})


@app.route('/')
def serve_ui():
    return send_file(Path(__file__).parent / 'chat.html')


@app.route('/api/health')
def health():
    return jsonify({
        'status':  'online',
        'service': 'social_chat_operator',
        'version': '1.0.0',
        'port':    PORT,
    })


# ---------------------------------------------------------------------------
# Internal formatting
# ---------------------------------------------------------------------------

def _format_posts(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Format platform drafts + QC scores for the chat UI."""
    drafts     = state.get('platform_drafts') or {}
    qc_report  = state.get('qc_report') or {}
    post_reports = qc_report.get('post_reports') or {}
    result = []
    for platform, draft in drafts.items():
        report = post_reports.get(platform) or {}
        result.append({
            'platform':  platform,
            'body':      draft.get('body', ''),
            'hashtags':  draft.get('hashtags', []),
            'score':     report.get('total_score', 0),
            'passed':    (report.get('total_score') or 0) >= MIN_POST_SCORE,
            'signals':   report.get('signals', {}),
            'improvements': report.get('improvements', []),
        })
    return result


if __name__ == '__main__':
    print(f'Social Chat Operator → http://localhost:{PORT}')
    app.run(host='127.0.0.1', port=PORT, debug=False, threaded=True)
