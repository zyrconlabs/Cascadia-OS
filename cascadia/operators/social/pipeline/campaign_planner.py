"""
zyrcon/social/campaign_planner.py
-----------------------------------
Campaign Planner Operator

Responsibility:
  Take one campaign brief (goal, audience, topic, platforms, duration)
  and break it into a structured daily post queue — one entry per day,
  each with a theme, angle, proof point, and CTA.

Rules:
  - Does NOT write post copy (that belongs to content_composer_operator).
  - Does NOT publish.
  - Owns only the campaign structure and daily brief generation.
  - Output becomes the input feed for the content engine day by day.

Supported durations:
  - daily   : 1 day  (1 post per platform)
  - weekly  : 7 days
  - monthly : 30 days
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Theme rotation — used to ensure variety across a campaign
# ---------------------------------------------------------------------------

DAILY_THEMES = [
    {"theme": "product_highlight",  "angle": "Lead with the strongest feature or proof point."},
    {"theme": "problem_insight",    "angle": "Open with a problem your audience faces. Position the product as the solution."},
    {"theme": "proof_point",        "angle": "Lead with a specific, verifiable fact or result. Make it concrete."},
    {"theme": "founder_voice",      "angle": "Write from a personal founder perspective. Why this was built. What was learned."},
    {"theme": "social_proof",       "angle": "Lead with what users or builders are saying. Real validation."},
    {"theme": "contrarian_take",    "angle": "Challenge a common assumption in your industry. Bold, direct, specific."},
    {"theme": "how_it_works",       "angle": "Explain one mechanism clearly. Teach something real in the post itself."},
    {"theme": "urgency_or_trend",   "angle": "Connect the product to something happening now. Why this matters today."},
    {"theme": "cta_direct",         "angle": "Lead with the outcome the reader gets. Make the CTA the whole post."},
    {"theme": "community_invite",   "angle": "Invite participation. Ask a question. Build dialogue."},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Main planner function
# ---------------------------------------------------------------------------

def build_campaign_plan(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Entry point called by workflow_runtime for the 'plan_campaign' step.

    Reads from state:
      topic           : what the campaign is about
      objective       : campaign goal (e.g. "announce release")
      cta             : the desired action
      tone            : brand voice
      audience        : target audience
      proof_points    : list of supporting facts
      platforms       : selected platforms
      campaign_duration: "daily" | "weekly" | "monthly"
      posts_per_day   : int, default 1
      campaign_start  : ISO date string, default today

    Writes to state:
      campaign_plan   : full structured plan
      campaign_days   : list of day briefs ready for content engine
      current_day_index: 0 (content engine processes one day at a time)
      total_days      : int
      draft_preview   : human-readable summary
    """
    topic         = (state.get('topic') or 'Zyrcon-X update').strip()
    objective     = (state.get('objective') or 'share a product update').strip()
    cta           = (state.get('cta') or 'Follow for updates.').strip()
    tone          = (state.get('tone') or 'founder-built, direct, practical').strip()
    audience      = (state.get('audience') or 'builders, developers, operators').strip()
    proof_points  = state.get('proof_points') or ['durable execution', 'crash recovery', 'approval gates']
    platforms     = state.get('platforms') or ['x']
    duration      = (state.get('campaign_duration') or 'daily').lower().strip()
    posts_per_day = int(state.get('posts_per_day') or 1)
    start_str     = state.get('campaign_start') or datetime.now(timezone.utc).date().isoformat()

    # Resolve total days
    duration_map = {'daily': 1, 'weekly': 7, 'monthly': 30}
    total_days   = duration_map.get(duration, 1)

    # Build the day-by-day plan
    campaign_days: List[Dict[str, Any]] = []
    start_date = datetime.fromisoformat(start_str).date() if 'T' not in start_str else datetime.fromisoformat(start_str).date()

    for day_index in range(total_days):
        post_date  = start_date + timedelta(days=day_index)
        theme_data = DAILY_THEMES[day_index % len(DAILY_THEMES)]
        proof      = proof_points[day_index % len(proof_points)]

        day_brief = {
            'day_index':    day_index,
            'day_number':   day_index + 1,
            'post_date':    post_date.isoformat(),
            'theme':        theme_data['theme'],
            'angle':        theme_data['angle'],
            'topic':        topic,
            'objective':    objective,
            'cta':          cta,
            'tone':         tone,
            'audience':     audience,
            'focus_proof':  proof,
            'platforms':    platforms,
            'posts_per_day': posts_per_day,
            'status':       'pending',  # pending | composed | qc_passed | qc_failed | approved | published
        }
        campaign_days.append(day_brief)

    campaign_plan = {
        'campaign_id':       f"camp_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        'topic':             topic,
        'objective':         objective,
        'duration':          duration,
        'total_days':        total_days,
        'total_posts':       total_days * posts_per_day * len(platforms),
        'platforms':         platforms,
        'start_date':        start_date.isoformat(),
        'created_at':        _now(),
        'qc_threshold':      float(state.get('qc_threshold') or 0.80),
        'min_post_score':    float(state.get('min_post_score') or 0.80),
    }

    preview = (
        f"Campaign planned: {total_days} day(s), {len(platforms)} platform(s), "
        f"{total_days * posts_per_day * len(platforms)} total posts. "
        f"First theme: {campaign_days[0]['theme']}. Ready for content engine."
    )

    return {
        **state,
        'campaign_plan':      campaign_plan,
        'campaign_days':      campaign_days,
        'current_day_index':  0,
        'total_days':         total_days,
        'qc_iteration':       0,
        'draft_preview':      preview,
    }


def advance_campaign_day(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Move to the next day in the campaign queue.
    Called after a day's posts are approved and published.
    """
    idx        = int(state.get('current_day_index') or 0)
    total      = int(state.get('total_days') or 1)
    next_idx   = idx + 1
    complete   = next_idx >= total

    days = list(state.get('campaign_days') or [])
    if idx < len(days):
        days[idx] = {**days[idx], 'status': 'published'}

    return {
        **state,
        'campaign_days':     days,
        'current_day_index': next_idx,
        'campaign_complete': complete,
        'draft_preview':     'Campaign complete.' if complete else f'Day {next_idx + 1} of {total} ready.',
    }
