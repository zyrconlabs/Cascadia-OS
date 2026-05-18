"""
DEPRECATED — use cascadia.licensing.license_gate (port 6100) instead.
This 49-line middleware shim has the same name as the real License Gate
service, creating a maintenance hazard. It uses Format A HMAC validation
while the rest of the stack uses Format C (ZYRCON-{TIER}-{16hex}).
No active callers. Will be removed in next major version.

cascadia/core/license_gate.py — Cascadia OS Sprint 3
License gate middleware for service-level tier enforcement.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def check_license(config: Dict[str, Any], required_tier: str = 'lite') -> Tuple[bool, Optional[str]]:
    """
    Validate the license key in config against required_tier.
    Returns (allowed: bool, error_message: Optional[str]).
    Fails open if TierValidator is unavailable — never blocks on infrastructure error.
    """
    try:
        from cascadia.licensing.tier_validator import TierValidator, TIER_RANKS
        key    = config.get('license_key', '')
        secret = config.get('license_secret', '')
        if not key or key.startswith('replace-'):
            return False, 'license_key not configured'
        if not secret:
            return True, None  # fail-open: no secret means validation not enforced
        validator = TierValidator(secret)
        info = validator.validate(key)
        if not info or not info.get('valid'):
            return False, info.get('error', 'invalid_license') if info else 'invalid_license'
        user_rank     = TIER_RANKS.get(info.get('tier', 'lite'), 0)
        required_rank = TIER_RANKS.get(required_tier, 0)
        if user_rank < required_rank:
            return False, f'tier_required:{required_tier} current:{info.get("tier")}'
        return True, None
    except Exception:
        return True, None  # fail-open on infrastructure error


def gate_response(required_tier: str = 'lite') -> Dict[str, Any]:
    """
    Return a standardised 403 body for tier-gate rejections.
    Callers: return (403, gate_response('pro')) directly from route handlers.
    """
    return {
        'error':       'tier_required',
        'tier_required': required_tier,
        'upgrade_url': 'https://zyrcon.store',
    }
