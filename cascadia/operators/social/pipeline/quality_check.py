"""
zyrcon/social/quality_check.py
--------------------------------
Quality Check Operator

Responsibility:
  Score every platform draft and produce an overall campaign score.
  Flag any post below the minimum threshold and generate specific
  improvement instructions for the content engine to act on.

Scoring model:
  Each post is scored across 8 signals (0–1 each), averaged to a
  0.0–1.0 score.  The campaign score is the mean of all post scores.

  Signals:
    1. hook_strength      — first sentence creates curiosity or tension
    2. specificity        — contains concrete number, name, or fact
    3. cta_clarity        — CTA asks for one specific action
    4. value_to_reader    — teaches, solves, or gives a reason to care
    5. platform_fit       — length, hashtag count, tone match platform norms
    6. viral_potential    — opinion/take, tension, or shareable insight
    7. brand_consistency  — tone matches brief, no forbidden claims
    8. no_generic_filler  — no "excited to share", "game-changing", "innovative"

Pass threshold: 0.80 per post, 0.80 campaign average.
Max QC iterations before hard fail: 3.
"""

from __future__ import annotations

# Import platform limits from Creative Director — single source of truth
try:
    from operators.social.pipeline.creative_director import PLATFORM_LIMITS as _CD_LIMITS
    _PLATFORM_LIMITS = _CD_LIMITS
except ImportError:
    _PLATFORM_LIMITS = {
        'x':         {'hard': 280,   'min': 60},
        'facebook':  {'hard': 63206, 'min': 80},
        'instagram': {'hard': 2200,  'min': 80},
        'linkedin':  {'hard': 3000,  'min': 100},
    }

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_POST_SCORE    = 0.80
MIN_CAMPAIGN_SCORE = 0.80
MAX_QC_ITERATIONS  = 3

# Platform-specific rules used in platform_fit signal
PLATFORM_RULES = {
    'x': {
        'max_chars':    280,
        'max_hashtags': 3,
        'min_chars':    60,
    },
    'facebook': {
        'max_chars':    500,
        'max_hashtags': 5,
        'min_chars':    80,
    },
    'linkedin': {
        'max_chars':    700,
        'max_hashtags': 5,
        'min_chars':    100,
    },
    'instagram': {
        'max_chars':    2200,
        'max_hashtags': 25,
        'min_chars':    80,
    },
}

# Filler phrases that kill credibility and engagement
GENERIC_FILLER = [
    "excited to share",
    "game.?changing",
    "innovative solution",
    "proud to announce",
    "thrilled to",
    "delighted to",
    "cutting.?edge",
    "state.?of.?the.?art",
    "best.?in.?class",
    "synergy",
    "leverage",
    "paradigm shift",
    "disruptive",
    "holistic approach",
    "seamlessly",
    "robust solution",
    "take it to the next level",
]

# Hook patterns that signal strong openings
STRONG_HOOK_PATTERNS = [
    r"^\d+",                          # starts with a number
    r"^(most|every|no one|nobody|the truth|here'?s|what if|stop|don'?t|why)",
    r"\?$",                           # ends first sentence with question
    r"(never|always|the problem|the mistake|the reason|the secret)",
    r"^(we built|i built|we learned|i learned|after \d+)",
]

SPECIFICITY_PATTERNS = [
    r"\d+[%xX]",            # percentage or multiplier
    r"\$[\d,]+",            # dollar amount
    r"\d+ (days?|hours?|minutes?|weeks?|months?|years?)",
    r"\d{1,3}(,\d{3})+",   # large numbers with commas
    r"(v\d+\.\d+|\d+\.\d+\.\d+)",  # version numbers
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Individual signal scorers
# ---------------------------------------------------------------------------

def _score_hook_strength(text: str) -> Tuple[float, str]:
    """First line creates curiosity, tension, or a strong claim."""
    first_line = text.strip().split('\n')[0][:200]
    for pattern in STRONG_HOOK_PATTERNS:
        if re.search(pattern, first_line, re.IGNORECASE):
            return 1.0, "Strong hook detected."
    if len(first_line) < 40:
        return 0.4, "Opening line too short to create impact."
    if first_line.lower().startswith(('we are', 'today we', 'introducing', 'announcing')):
        return 0.3, "Weak hook — opens with announcement language instead of tension or insight."
    return 0.5, "Hook is neutral — could be stronger. Try opening with a number, a question, or a bold claim."


def _score_specificity(text: str) -> Tuple[float, str]:
    """Contains at least one concrete fact, number, or named proof point."""
    for pattern in SPECIFICITY_PATTERNS:
        if re.search(pattern, text):
            return 1.0, "Contains specific, concrete detail."
    if re.search(r'\b(always|never|everyone|nobody|all)\b', text, re.IGNORECASE):
        return 0.6, "Uses absolute language but no specific proof. Add a number or example."
    return 0.3, "No specific numbers or facts found. Add at least one concrete data point."


def _score_cta_clarity(text: str) -> Tuple[float, str]:
    """CTA asks for one specific action."""
    strong_ctas = [
        r"reply (with|if|and)",
        r"comment (below|with|if)",
        r"(DM|message) (me|us)",
        r"click the link",
        r"follow (for|us|to)",
        r"share (this|if)",
        r"sign up",
        r"join (the|us|our)",
        r"try (it|this|for free)",
        r"read (the|more|full)",
    ]
    for pattern in strong_ctas:
        if re.search(pattern, text, re.IGNORECASE):
            return 1.0, "Clear, specific CTA found."
    weak_ctas = [r"check (it|this) out", r"learn more", r"find out more", r"visit our"]
    for pattern in weak_ctas:
        if re.search(pattern, text, re.IGNORECASE):
            return 0.5, "CTA is present but generic. Be more specific about what you want the reader to do."
    if re.search(r'(link|url|github|website)', text, re.IGNORECASE):
        return 0.6, "Link present but no explicit CTA instruction. Tell the reader what to do with it."
    return 0.2, "No CTA found. Every post needs one clear action for the reader."


def _score_value_to_reader(text: str) -> Tuple[float, str]:
    """Post teaches, solves, or gives a genuine reason to care."""
    teaching_signals = [
        r"(here'?s how|how to|the reason|why|what (most|many|we) (don'?t|learned|found))",
        r"(the (problem|mistake|secret|key|lesson|trick) (is|with|to))",
        r"(you can|you (don'?t|won'?t) (have to|need to))",
        r"(\d+ (ways?|reasons?|things?|steps?|lessons?))",
    ]
    for pattern in teaching_signals:
        if re.search(pattern, text, re.IGNORECASE):
            return 1.0, "Post delivers clear value to the reader."
    promo_only = re.search(
        r"^(introducing|announcing|check out|we (just|are|have)|our (new|latest))",
        text.strip(), re.IGNORECASE
    )
    if promo_only:
        return 0.3, "Post is purely promotional. Lead with the value or insight first, product second."
    return 0.6, "Post has moderate value signal. Could more clearly state what the reader gains."


def _score_platform_fit(text: str, platform: str, hashtags: List[str]) -> Tuple[float, str]:
    """
    Length, hashtag count, and tone match platform norms.
    HARD FAIL (score=0.0) if post exceeds platform character limit —
    the X API will reject over-length posts outright.
    """
    rules      = PLATFORM_RULES.get(platform, PLATFORM_RULES['x'])
    hard_limit = _PLATFORM_LIMITS.get(platform, {}).get('hard', rules['max_chars'])
    char_count = len(text)
    tag_count  = len(hashtags)
    issues     = []

    # Hard fail — over the API limit, cannot be published at all
    if char_count > hard_limit:
        return 0.0, (
            f"HARD FAIL: {char_count} chars exceeds {platform} API limit of {hard_limit}. "
            f"Creative Director must truncate before this post reaches QC."
        )

    if char_count > rules['max_chars']:
        issues.append(f"Too long ({char_count} chars, recommended max {rules['max_chars']}).")
    if char_count < rules['min_chars']:
        issues.append(f"Too short ({char_count} chars, min {rules['min_chars']}).")
    if tag_count > rules['max_hashtags']:
        issues.append(f"Too many hashtags ({tag_count}, max {rules['max_hashtags']}).")

    if not issues:
        return 1.0, f"Fits {platform} format perfectly ({char_count} chars)."
    score = max(0.2, 1.0 - (len(issues) * 0.3))
    return score, " ".join(issues)


def _score_viral_potential(text: str) -> Tuple[float, str]:
    """Post contains opinion, tension, or a shareable insight."""
    viral_signals = [
        r"(most (people|companies|teams)|no one (talks|mentions)|the truth about)",
        r"(unpopular opinion|hot take|controversial|fight me)",
        r"(we (almost|nearly) (gave up|failed|quit)|almost (killed|broke|lost))",
        r"(changed (my|our|everything)|the moment (i|we) (realized|learned|knew))",
        r"(don'?t (make|do|use|build)|stop (using|building|doing))",
        r"(\d+ (years?|months?) (ago|later|of) (building|running|working))",
    ]
    score = 0.4  # baseline — all posts have some potential
    hits  = []
    for pattern in viral_signals:
        if re.search(pattern, text, re.IGNORECASE):
            score = min(1.0, score + 0.3)
            hits.append(pattern)
    if score >= 1.0:
        return 1.0, "High viral potential — strong opinion, tension, or shareable insight detected."
    if score > 0.6:
        return score, "Moderate viral potential. Consider adding a bold take or personal story element."
    return score, "Low viral potential. Add a tension point, contrarian take, or surprising fact."


def _score_brand_consistency(text: str, tone: str, forbidden: List[str]) -> Tuple[float, str]:
    """Tone matches brief and no forbidden claims present."""
    for claim in (forbidden or []):
        if claim.lower() in text.lower():
            return 0.0, f"Forbidden claim found: '{claim}'. Remove immediately."
    tone_lower = tone.lower()
    if 'bold' in tone_lower or 'direct' in tone_lower:
        if re.search(r'(might|maybe|perhaps|possibly|could potentially)', text, re.IGNORECASE):
            return 0.6, "Tone brief calls for boldness but post uses hedging language."
    if 'technical' in tone_lower:
        if not re.search(r'(api|workflow|operator|runtime|execution|deploy|build|code|stack)', text, re.IGNORECASE):
            return 0.6, "Tone brief calls for technical voice but post lacks technical terms."
    return 1.0, "Brand tone and compliance check passed."


def _score_no_generic_filler(text: str) -> Tuple[float, str]:
    """No clichéd marketing language that kills credibility."""
    found = []
    for phrase in GENERIC_FILLER:
        if re.search(phrase, text, re.IGNORECASE):
            found.append(re.sub(r'\.\\?', ' ', phrase).strip())
    if not found:
        return 1.0, "No generic filler detected."
    return max(0.1, 1.0 - len(found) * 0.25), f"Generic filler found: {', '.join(found[:3])}. Replace with specific language."


# ---------------------------------------------------------------------------
# Per-post scorer
# ---------------------------------------------------------------------------

def score_post(
    post_text: str,
    platform: str,
    hashtags: List[str],
    tone: str,
    forbidden_claims: List[str],
) -> Dict[str, Any]:
    """
    Score one platform post across all 8 signals.
    Returns a detailed score report.
    """
    signals = {}

    s, n = _score_hook_strength(post_text)
    signals['hook_strength'] = {'score': s, 'note': n}

    s, n = _score_specificity(post_text)
    signals['specificity'] = {'score': s, 'note': n}

    s, n = _score_cta_clarity(post_text)
    signals['cta_clarity'] = {'score': s, 'note': n}

    s, n = _score_value_to_reader(post_text)
    signals['value_to_reader'] = {'score': s, 'note': n}

    s, n = _score_platform_fit(post_text, platform, hashtags)
    signals['platform_fit'] = {'score': s, 'note': n}

    s, n = _score_viral_potential(post_text)
    signals['viral_potential'] = {'score': s, 'note': n}

    s, n = _score_brand_consistency(post_text, tone, forbidden_claims)
    signals['brand_consistency'] = {'score': s, 'note': n}

    s, n = _score_no_generic_filler(post_text)
    signals['no_generic_filler'] = {'score': s, 'note': n}

    total_score = round(sum(v['score'] for v in signals.values()) / len(signals), 3)
    passed      = total_score >= MIN_POST_SCORE

    # Collect improvement instructions for any signal below threshold
    improvements = [
        f"{k}: {v['note']}"
        for k, v in signals.items()
        if v['score'] < MIN_POST_SCORE
    ]

    return {
        'platform':        platform,
        'total_score':     total_score,
        'passed':          passed,
        'signals':         signals,
        'improvements':    improvements,
        'char_count':      len(post_text),
        'hashtag_count':   len(hashtags),
        'scored_at':       _now(),
    }


# ---------------------------------------------------------------------------
# Campaign-level QC entry point
# ---------------------------------------------------------------------------

def run_quality_check(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Entry point called by workflow_runtime for the 'quality_check' step.

    Reads from state:
      platform_drafts  : {platform: {body, hashtags, ...}}
      tone             : brand voice from campaign brief
      forbidden_claims : list of disallowed claims
      qc_iteration     : how many times QC has run this cycle
      min_post_score   : threshold per post (default 0.80)
      min_campaign_score: threshold for campaign average (default 0.80)

    Writes to state:
      qc_report        : full per-post and campaign scores
      qc_passed        : bool — true if all posts and campaign meet threshold
      qc_iteration     : incremented
      qc_improvements  : dict of {platform: [improvement instructions]}
      draft_preview    : human-readable QC summary
    """
    drafts           = state.get('platform_drafts') or {}
    tone             = state.get('tone') or 'founder-built, direct, practical'
    forbidden        = state.get('forbidden_claims') or []
    iteration        = int(state.get('qc_iteration') or 0) + 1
    min_post         = float(state.get('min_post_score') or MIN_POST_SCORE)
    min_campaign     = float(state.get('min_campaign_score') or MIN_CAMPAIGN_SCORE)

    post_reports: Dict[str, Any] = {}
    all_scores:   List[float]    = []
    improvements: Dict[str, List[str]] = {}
    failed_platforms: List[str]  = []

    for platform, draft in drafts.items():
        post_text = draft.get('body', '')
        hashtags  = draft.get('hashtags', [])

        if not post_text:
            post_reports[platform] = {
                'platform': platform, 'total_score': 0.0, 'passed': False,
                'signals': {}, 'improvements': ['Post body is empty.'], 'scored_at': _now(),
            }
            failed_platforms.append(platform)
            all_scores.append(0.0)
            continue

        report = score_post(post_text, platform, hashtags, tone, forbidden)
        post_reports[platform] = report
        all_scores.append(report['total_score'])

        if not report['passed']:
            failed_platforms.append(platform)
            improvements[platform] = report['improvements']

    campaign_score = round(sum(all_scores) / len(all_scores), 3) if all_scores else 0.0
    campaign_passed = campaign_score >= min_campaign and len(failed_platforms) == 0
    hard_fail = iteration >= MAX_QC_ITERATIONS and not campaign_passed

    # Build human-readable summary
    score_pct    = int(campaign_score * 100)
    post_summary = ", ".join(
        f"{p}: {int(r['total_score'] * 100)}%"
        for p, r in post_reports.items()
    )

    if campaign_passed:
        preview = (
            f"QC passed (iteration {iteration}). Campaign score: {score_pct}%. "
            f"Posts: {post_summary}. Ready for your approval."
        )
    elif hard_fail:
        preview = (
            f"QC hard fail after {iteration} iterations. Campaign score: {score_pct}%. "
            f"Failed platforms: {', '.join(failed_platforms)}. Manual review required."
        )
    else:
        preview = (
            f"QC iteration {iteration}: campaign score {score_pct}% — below {int(min_campaign * 100)}% threshold. "
            f"Failed: {', '.join(failed_platforms)}. Sending back to content engine for improvement."
        )

    qc_report = {
        'iteration':        iteration,
        'campaign_score':   campaign_score,
        'campaign_passed':  campaign_passed,
        'min_post_score':   min_post,
        'min_campaign_score': min_campaign,
        'post_reports':     post_reports,
        'failed_platforms': failed_platforms,
        'hard_fail':        hard_fail,
        'scored_at':        _now(),
    }

    return {
        **state,
        'qc_report':       qc_report,
        'qc_passed':       campaign_passed,
        'qc_hard_fail':    hard_fail,
        'qc_iteration':    iteration,
        'qc_improvements': improvements,
        'draft_preview':   preview,
    }


# ---------------------------------------------------------------------------
# Rewrite instruction builder
# ---------------------------------------------------------------------------

def build_rewrite_instructions(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Translates QC improvements into specific rewrite instructions
    that the content engine can act on directly.

    Called after a QC fail, before re-running the content engine.
    """
    improvements  = state.get('qc_improvements') or {}
    qc_report     = state.get('qc_report') or {}
    post_reports  = qc_report.get('post_reports') or {}
    rewrite_tasks: Dict[str, Any] = {}

    for platform, notes in improvements.items():
        report = post_reports.get(platform, {})
        signals = report.get('signals', {})

        # Identify the lowest-scoring signals
        sorted_signals = sorted(
            signals.items(),
            key=lambda x: x[1].get('score', 1.0)
        )
        priority_fixes = [
            f"{sig}: {data['note']}"
            for sig, data in sorted_signals[:3]
            if data.get('score', 1.0) < MIN_POST_SCORE
        ]

        rewrite_tasks[platform] = {
            'current_score':  report.get('total_score', 0),
            'target_score':   MIN_POST_SCORE,
            'priority_fixes': priority_fixes,
            'full_notes':     notes,
            'instruction':    (
                f"Rewrite the {platform} post to address these issues in priority order: "
                + "; ".join(priority_fixes)
            ),
        }

    preview = (
        f"Rewrite instructions prepared for {len(rewrite_tasks)} platform(s): "
        f"{', '.join(rewrite_tasks.keys())}. Sending back to content engine."
    )

    return {
        **state,
        'rewrite_tasks': rewrite_tasks,
        'draft_preview': preview,
    }
