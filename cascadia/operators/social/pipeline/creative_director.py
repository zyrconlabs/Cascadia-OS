"""
operators/social/pipeline/creative_director.py
------------------------------------------------
Creative Director Operator

Responsibility:
  - Build the master content package from a campaign brief
  - Render platform-native drafts for each selected platform
  - Enforce hard character limits BEFORE content reaches QC
  - Stamp UTM parameters on every outbound link
  - Produce theme-aware, varied copy across campaign days

Rules:
  - Does NOT publish
  - Does NOT score (QC owns scoring)
  - Hard char limit enforcement is final — never produces over-length content
  - UTM tagging happens at render time, not at publish time
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Platform limits — single source of truth shared with QC
# ---------------------------------------------------------------------------

PLATFORM_LIMITS = {
    'x':         {'hard': 280,   'soft': 240,  'hashtag_max': 3,  'min': 60},
    'facebook':  {'hard': 63206, 'soft': 500,  'hashtag_max': 5,  'min': 80},
    'instagram': {'hard': 2200,  'soft': 1800, 'hashtag_max': 25, 'min': 80},
    'linkedin':  {'hard': 3000,  'soft': 700,  'hashtag_max': 5,  'min': 100},
}

# X body budget — conservative to leave room for hashtags appended by connector
X_CHAR_BUDGET = 240


# ---------------------------------------------------------------------------
# Platform list normaliser
# ---------------------------------------------------------------------------

def _clean_platforms(value: Any) -> List[str]:
    valid = {'x', 'instagram', 'facebook', 'linkedin'}
    if isinstance(value, str):
        raw = [p.strip().lower() for p in value.replace(';', ',').split(',')]
    else:
        raw = [str(p).strip().lower() for p in (value or [])]
    seen, allowed = set(), []
    for item in raw:
        if item in valid and item not in seen:
            allowed.append(item)
            seen.add(item)
    return allowed or ['x']


# ---------------------------------------------------------------------------
# UTM builder
# ---------------------------------------------------------------------------

def build_utm_url(base_url: str, campaign_id: str, platform: str, day: int) -> str:
    """Stamp UTM parameters on any outbound link."""
    if not base_url:
        return ''
    sep    = '&' if '?' in base_url else '?'
    slug   = re.sub(r'[^a-z0-9]+', '-', campaign_id.lower()).strip('-')[:40]
    params = (
        f'utm_source={platform}'
        f'&utm_medium=social'
        f'&utm_campaign={slug}'
        f'&utm_content=day{day}'
    )
    return f'{base_url}{sep}{params}'


# ---------------------------------------------------------------------------
# Hard character limit enforcement
# ---------------------------------------------------------------------------

def enforce_char_limit(text: str, platform: str) -> str:
    """
    Hard truncate to platform limit. Never produces over-length content.
    Tries sentence boundary first, then word boundary, then hard cut.
    """
    limit = X_CHAR_BUDGET if platform == 'x' else PLATFORM_LIMITS.get(platform, {}).get('hard', 280)

    if len(text) <= limit:
        return text

    truncated = text[:limit]

    # Sentence boundary
    last_sent = max(
        truncated.rfind('. '),
        truncated.rfind('.\n'),
        truncated.rfind('! '),
        truncated.rfind('? '),
    )
    if last_sent > int(limit * 0.6):
        return text[:last_sent + 1].rstrip() + '…'

    # Word boundary
    last_space = truncated.rfind(' ')
    if last_space > int(limit * 0.5):
        return text[:last_space].rstrip() + '…'

    return text[:limit - 1].rstrip() + '…'


# ---------------------------------------------------------------------------
# Shared hashtag pool
# ---------------------------------------------------------------------------

def _build_hashtag_pool(topic: str, proof_points: List[str]) -> List[str]:
    base = ['#BuildInPublic', '#AI', '#LocalAI', '#Automation', '#DevTools', '#OpenSource', '#founder', '#startup']
    topic_tags = [f'#{w.strip().replace(" ","").replace("-","")}' for w in topic.split() if len(w) > 3]
    proof_tags = [f'#{pp.strip().replace(" ","").replace("-","")}' for pp in proof_points[:3] if len(pp) > 4]
    return list(dict.fromkeys(topic_tags + proof_tags + base))[:20]


def _select_hashtags(pool: List[str], platform: str) -> List[str]:
    limit = PLATFORM_LIMITS.get(platform, {}).get('hashtag_max', 3)
    return (sorted(pool, key=len) if platform == 'x' else pool)[:limit]


# ---------------------------------------------------------------------------
# Theme-aware X copy
# ---------------------------------------------------------------------------

def _render_x_body(theme: str, topic: str, proof_points: List[str], cta: str, utm_link: str) -> str:
    pp = proof_points
    p1 = pp[0] if pp else 'reliable execution'
    p2 = pp[1] if len(pp) > 1 else p1
    p3 = pp[2] if len(pp) > 2 else p2
    link = f'\n\n{utm_link}' if utm_link else ''

    templates = {
        'product_highlight': f"{topic} just got better.\n\nThe thing people underestimate: {p1}.\n\nThat alone changes how you build.\n\n{cta}{link}",
        'problem_insight':   f"Most AI systems fail silently.\n\n{topic} doesn't. Built around {p1} from day one.\n\n{cta}{link}",
        'proof_point':       f"{p1.capitalize()}.\n\nNot a promise — it's how {topic} is built.\n\n{cta}{link}",
        'founder_voice':     f"We built {topic} because we kept hitting the same wall.\n\nEvery crash lost work. Every restart started over.\n\nNot anymore. {cta}{link}",
        'social_proof':      f"What builders keep telling us about {topic}:\n\n\"{p1} is the thing I didn't know I needed.\"\n\n{cta}{link}",
        'contrarian_take':   f"Unpopular opinion: {p1} matters more than any feature list.\n\nThat's what we built {topic} around.\n\n{cta}{link}",
        'how_it_works':      f"How {topic} handles {p1}:\n\n→ {p2}\n→ {p3}\n\nThat's it. No magic.\n\n{cta}{link}",
        'urgency_or_trend':  f"AI is moving fast. The gap between reliable and unreliable systems is widening.\n\n{topic} is on the right side. {p1}.\n\n{cta}{link}",
        'cta_direct':        f"If your AI workflow has ever crashed mid-run and lost everything —\n\n{topic} was built for you.\n\n{p1}. {cta}{link}",
        'community_invite':  f"Building with AI operators?\n\nWe're shipping {topic} — {p1} baked in.\n\nWould love your take. {cta}{link}",
    }
    body = templates.get(theme, templates['product_highlight'])
    return enforce_char_limit(body, 'x')


# ---------------------------------------------------------------------------
# Platform renderers
# ---------------------------------------------------------------------------

def _render_x(state: Dict[str, Any], utm_link: str) -> Dict[str, Any]:
    pkg    = state.get('master_content_package') or {}
    topic  = pkg.get('topic', 'Zyrcon update')
    cta    = pkg.get('cta', 'Reply if you want details.')
    proofs = pkg.get('proof_points') or []
    days   = state.get('campaign_days') or []
    day_idx = state.get('current_day_index') or 0
    theme  = days[day_idx]['theme'] if day_idx < len(days) else 'product_highlight'
    pool   = _build_hashtag_pool(topic, proofs)

    body = _render_x_body(theme, topic, proofs, cta, utm_link)
    return {
        'platform':   'x',
        'body':       body,
        'hashtags':   _select_hashtags(pool, 'x'),
        'theme':      theme,
        'char_count': len(body),
        'utm_link':   utm_link,
        'status':     'draft_ready',
    }


def _render_facebook(state: Dict[str, Any], utm_link: str) -> Dict[str, Any]:
    pkg    = state.get('master_content_package') or {}
    topic  = pkg.get('topic', 'Zyrcon update')
    cta    = pkg.get('cta', 'Reply if you want details.')
    proofs = pkg.get('proof_points') or []
    short  = ', '.join(proofs[:3]) if proofs else 'durable AI operations'
    link   = f'\n\n{utm_link}' if utm_link else ''

    body = enforce_char_limit(
        f"We've been quietly building something we think matters.\n\n"
        f"{topic} — built around {short}.\n\n"
        f"{cta}{link}", 'facebook'
    )
    return {
        'platform':   'facebook',
        'body':       body,
        'hashtags':   _select_hashtags(_build_hashtag_pool(topic, proofs), 'facebook'),
        'char_count': len(body),
        'utm_link':   utm_link,
        'status':     'draft_ready',
    }


def _render_instagram(state: Dict[str, Any], utm_link: str) -> Dict[str, Any]:
    pkg    = state.get('master_content_package') or {}
    topic  = pkg.get('topic', 'Zyrcon update')
    cta    = pkg.get('cta', 'Reply if you want details.')
    proofs = pkg.get('proof_points') or []
    pp_lines = '\n'.join(f'✦ {p.capitalize()}' for p in proofs[:5])
    link   = f'\n\n{utm_link}' if utm_link else ''

    body = enforce_char_limit(
        f"{topic}\n\nBuilt to make AI dependable in real work:\n{pp_lines}\n\n{cta}{link}",
        'instagram'
    )
    return {
        'platform':   'instagram',
        'body':       body,
        'hashtags':   _select_hashtags(_build_hashtag_pool(topic, proofs), 'instagram'),
        'char_count': len(body),
        'utm_link':   utm_link,
        'status':     'draft_ready',
    }


def _render_linkedin(state: Dict[str, Any], utm_link: str) -> Dict[str, Any]:
    pkg    = state.get('master_content_package') or {}
    topic  = pkg.get('topic', 'Zyrcon update')
    cta    = pkg.get('cta', 'Reply if you want details.')
    proofs = pkg.get('proof_points') or []
    pp_lines = '\n'.join(f'→ {p.capitalize()}' for p in proofs[:4])
    link   = f'\n\n{utm_link}' if utm_link else ''

    body = enforce_char_limit(
        f"We've spent months focused on one problem: making AI workflows reliable.\n\n"
        f"{topic} is the result.\n\nWhat it delivers:\n{pp_lines}\n\n{cta}{link}",
        'linkedin'
    )
    return {
        'platform':   'linkedin',
        'body':       body,
        'hashtags':   _select_hashtags(_build_hashtag_pool(topic, proofs), 'linkedin'),
        'char_count': len(body),
        'utm_link':   utm_link,
        'status':     'draft_ready',
    }


# ---------------------------------------------------------------------------
# Public API — called by workflow_runtime
# ---------------------------------------------------------------------------

def build_master_package(state: Dict[str, Any]) -> Dict[str, Any]:
    """Creative Director entry point: build the master content package."""
    topic        = (state.get('topic') or state.get('content') or 'Zyrcon update').strip()
    audience     = (state.get('audience') or 'builders, operators, and technical buyers').strip()
    goal         = (state.get('campaign_goal') or state.get('objective') or 'share a meaningful product update').strip()
    cta          = (state.get('cta') or 'Reply if you want details or an early look.').strip()
    proof_points = state.get('proof_points') or ['durable execution', 'approval gates', 'crash recovery', 'local-first reliability']
    if isinstance(proof_points, str):
        proof_points = [p.strip() for p in proof_points.split(',') if p.strip()]
    tone      = state.get('tone') or 'founder-built, practical, direct'
    platforms = _clean_platforms(state.get('platforms'))

    return {
        **state,
        'platforms': platforms,
        'master_content_package': {
            'topic':          topic,
            'audience':       audience,
            'campaign_goal':  goal,
            'cta':            cta,
            'proof_points':   proof_points,
            'tone':           tone,
            'master_message': f"{topic}. Built for {audience}. Goal: {goal}. Key proof: {', '.join(proof_points[:4])}. CTA: {cta}",
        },
        'draft_preview': f'Creative Director package ready for {", ".join(platforms)}.',
    }


def render_platform(state: Dict[str, Any], platform: str) -> Dict[str, Any]:
    """
    Render a platform-native draft from the master package.
    Enforces char limits hard. Stamps UTM links.
    """
    plan        = state.get('campaign_plan') or {}
    day_idx     = state.get('current_day_index') or 0
    campaign_id = plan.get('campaign_id') or state.get('topic', 'campaign')
    base_url    = state.get('site_url') or state.get('utm_base_url') or ''
    utm_link    = build_utm_url(base_url, campaign_id, platform, day_idx + 1) if base_url else ''

    renderers = {'x': _render_x, 'facebook': _render_facebook, 'instagram': _render_instagram, 'linkedin': _render_linkedin}
    renderer  = renderers.get(platform)

    if not renderer:
        pkg  = state.get('master_content_package') or {}
        body = enforce_char_limit(pkg.get('master_message', platform), 'x')
        rendered = {'platform': platform, 'body': body, 'hashtags': ['#AI'], 'status': 'draft_ready', 'char_count': len(body)}
    else:
        rendered = renderer(state, utm_link)

    new_state = dict(state)
    drafts    = dict(new_state.get('platform_drafts') or {})
    drafts[platform]          = rendered
    new_state['platform_drafts'] = drafts
    new_state['draft_preview']   = f'{platform.title()} draft ready ({rendered.get("char_count", 0)} chars). Approval required.'
    return new_state


def mark_published(state: Dict[str, Any], platform: str, post_id: str) -> Dict[str, Any]:
    """Record a successful publish. Called by the X connector after real post."""
    new_state = dict(state)
    published = dict(new_state.get('published_posts') or {})
    published[platform] = {'post_id': post_id, 'published': True}
    new_state['published_posts'] = published
    statuses  = dict(new_state.get('publish_status') or {})
    statuses[platform]       = 'published'
    new_state['publish_status'] = statuses
    new_state['draft_preview']  = f'{platform.title()} post published.'
    return new_state
