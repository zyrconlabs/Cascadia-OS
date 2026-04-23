"""
operators/social/connectors/x_connector.py
-------------------------------------------
X (Twitter) API Connector

Responsibility:
  - Post approved content to X using the v2 API
  - Support single posts and threads
  - Enforce 280 char limit as final safety net before API call
  - Log every post attempt with full result
  - Prevent duplicate posts via content fingerprinting
  - Return post ID and URL on success

Modes:
  simulated  — writes to local log, no real API call (default)
  live       — posts via X v2 API using tweepy

Config: operators/social/connectors/x.config.json
  {
    "mode": "simulated",
    "bearer_token": "",
    "api_key": "",
    "api_secret": "",
    "access_token": "",
    "access_token_secret": ""
  }

Rules:
  - Never rewrites approved content
  - Never posts if fingerprint already in post log
  - Hard 280 char check before every API call
  - Thread posts go out sequentially — tweet 1, reply to 1, reply to 2...
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR  = Path(__file__).parent
CONFIG    = BASE_DIR / 'x.config.json'
LOG_DIR   = BASE_DIR / 'data'
POST_LOG  = LOG_DIR / 'x_posts.json'
DUPE_LOG  = LOG_DIR / 'x_fingerprints.json'

LOG_DIR.mkdir(exist_ok=True)

X_CHAR_LIMIT = 280


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text())
        except Exception:
            pass
    return {'mode': 'simulated'}


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def _fingerprint(text: str) -> str:
    """SHA-256 of normalised post text."""
    normalised = ' '.join(text.lower().split())
    return hashlib.sha256(normalised.encode()).hexdigest()


def _load_fingerprints() -> set:
    if DUPE_LOG.exists():
        try:
            return set(json.loads(DUPE_LOG.read_text()))
        except Exception:
            pass
    return set()


def _save_fingerprint(fp: str) -> None:
    fps = _load_fingerprints()
    fps.add(fp)
    DUPE_LOG.write_text(json.dumps(sorted(fps)))


def is_duplicate(text: str) -> bool:
    return _fingerprint(text) in _load_fingerprints()


# ---------------------------------------------------------------------------
# Post log
# ---------------------------------------------------------------------------

def _log_post(record: Dict[str, Any]) -> None:
    posts: List[Dict] = []
    if POST_LOG.exists():
        try:
            posts = json.loads(POST_LOG.read_text())
        except Exception:
            pass
    posts.insert(0, record)
    POST_LOG.write_text(json.dumps(posts[:500], indent=2, default=str))


# ---------------------------------------------------------------------------
# Character limit safety net
# ---------------------------------------------------------------------------

def _safe_truncate(text: str) -> str:
    """Final safety net before API call — hard cut to 280."""
    if len(text) <= X_CHAR_LIMIT:
        return text
    # Cut at last word before 277, append ellipsis
    cut = text[:277]
    last_space = cut.rfind(' ')
    return (cut[:last_space] if last_space > 200 else cut).rstrip() + '…'


# ---------------------------------------------------------------------------
# Simulated publish
# ---------------------------------------------------------------------------

def _simulate_post(text: str, hashtags: List[str], run_id: str) -> Dict[str, Any]:
    post_id  = f'x_sim_{uuid.uuid4().hex[:12]}'
    post_url = f'https://x.com/i/web/status/{post_id}'
    ts       = datetime.now(timezone.utc).isoformat()

    full_text = text
    if hashtags:
        tag_str = ' '.join(hashtags[:3])
        if len(full_text) + 1 + len(tag_str) <= X_CHAR_LIMIT:
            full_text = f'{full_text}\n\n{tag_str}'

    record = {
        'post_id':   post_id,
        'post_url':  post_url,
        'text':      full_text,
        'char_count': len(full_text),
        'mode':      'simulated',
        'run_id':    run_id,
        'posted_at': ts,
        'status':    'simulated',
    }
    _log_post(record)
    _save_fingerprint(_fingerprint(text))
    return record


# ---------------------------------------------------------------------------
# Real X API publish
# ---------------------------------------------------------------------------

def _live_post(
    text: str,
    hashtags: List[str],
    run_id: str,
    thread_texts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Post to X via tweepy v2.
    Requires tweepy: pip install tweepy
    """
    try:
        import tweepy
    except ImportError:
        return {
            'status': 'failed',
            'error':  'tweepy not installed. Run: pip install tweepy',
            'run_id': run_id,
        }

    cfg = load_config()

    client = tweepy.Client(
        bearer_token=cfg.get('bearer_token', ''),
        consumer_key=cfg.get('api_key', ''),
        consumer_secret=cfg.get('api_secret', ''),
        access_token=cfg.get('access_token', ''),
        access_token_secret=cfg.get('access_token_secret', ''),
        wait_on_rate_limit=True,
    )

    ts = datetime.now(timezone.utc).isoformat()

    # Build full post text with hashtags
    full_text = _safe_truncate(text)
    if hashtags:
        tag_str = ' '.join(hashtags[:3])
        candidate = f'{full_text}\n\n{tag_str}'
        if len(candidate) <= X_CHAR_LIMIT:
            full_text = candidate

    try:
        # Post the main tweet
        response = client.create_tweet(text=full_text)
        tweet_id = str(response.data['id'])
        post_url = f'https://x.com/i/web/status/{tweet_id}'

        thread_ids = [tweet_id]

        # Post thread replies if provided
        if thread_texts:
            for thread_text in thread_texts:
                safe_text = _safe_truncate(thread_text)
                reply = client.create_tweet(
                    text=safe_text,
                    in_reply_to_tweet_id=thread_ids[-1],
                )
                thread_ids.append(str(reply.data['id']))

        record = {
            'post_id':    tweet_id,
            'post_url':   post_url,
            'text':       full_text,
            'char_count': len(full_text),
            'mode':       'live',
            'run_id':     run_id,
            'posted_at':  ts,
            'status':     'published',
            'thread_ids': thread_ids,
        }
        _log_post(record)
        _save_fingerprint(_fingerprint(text))
        return record

    except Exception as exc:
        error_record = {
            'status':    'failed',
            'error':     str(exc),
            'run_id':    run_id,
            'posted_at': ts,
            'mode':      'live',
        }
        _log_post(error_record)
        return error_record


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def post(
    draft: Dict[str, Any],
    run_id: str = '',
    thread_texts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Main entry point. Called by workflow_runtime or the chat operator.

    Parameters
    ----------
    draft        : platform draft dict with 'body' and 'hashtags'
    run_id       : workflow run ID for audit trail
    thread_texts : optional list of follow-up tweet texts for a thread

    Returns
    -------
    Dict with post_id, post_url, status, char_count, mode
    """
    text     = (draft.get('body') or '').strip()
    hashtags = draft.get('hashtags') or []

    if not text:
        return {'status': 'failed', 'error': 'Post body is empty.', 'run_id': run_id}

    # Duplicate check
    if is_duplicate(text):
        return {
            'status':  'skipped',
            'reason':  'Duplicate post detected — this content was already posted.',
            'run_id':  run_id,
        }

    cfg  = load_config()
    mode = cfg.get('mode', 'simulated')

    if mode == 'live':
        return _live_post(text, hashtags, run_id, thread_texts)
    else:
        return _simulate_post(text, hashtags, run_id)


def post_thread(
    intro_draft: Dict[str, Any],
    thread_parts: List[str],
    run_id: str = '',
) -> Dict[str, Any]:
    """
    Post a thread — intro tweet plus follow-up parts.
    Each part is posted as a reply to the previous tweet.
    """
    return post(intro_draft, run_id=run_id, thread_texts=thread_parts)


def get_post_history(limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent post history from the local log."""
    if not POST_LOG.exists():
        return []
    try:
        posts = json.loads(POST_LOG.read_text())
        return posts[:limit]
    except Exception:
        return []


def get_config_status() -> Dict[str, Any]:
    """Return connector status for health checks."""
    cfg  = load_config()
    mode = cfg.get('mode', 'simulated')
    configured = all([
        cfg.get('api_key'),
        cfg.get('api_secret'),
        cfg.get('access_token'),
        cfg.get('access_token_secret'),
    ])
    return {
        'mode':       mode,
        'configured': configured if mode == 'live' else True,
        'post_count': len(get_post_history(500)),
    }
