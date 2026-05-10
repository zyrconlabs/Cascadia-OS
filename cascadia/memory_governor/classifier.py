"""
Event classifier.

Maps event types to one of seven categories:
  ephemeral       — RAM only, never persisted
  checkpoint      — persist before/after risky action
  audit           — durable record
  security        — durable record, immediate
  business_memory — long-term value
  debug_only      — RAM only unless debug mode
  discard         — drop entirely

Skeleton: returns "ephemeral" by default unless event type
is in known persistent or RAM-only categories. Phase 3
expands rules.
"""

from typing import Literal

EventCategory = Literal[
    "ephemeral",
    "checkpoint",
    "audit",
    "security",
    "business_memory",
    "debug_only",
    "discard",
]

_PERSISTENT_TYPES = {
    "approval_required":         "checkpoint",
    "approval_granted":          "audit",
    "external_action_pending":   "checkpoint",
    "external_action_completed": "audit",
    "security_event":            "security",
    "run_completed":             "audit",
    "run_failed":                "audit",
}

_RAM_ONLY_TYPES = {
    "heartbeat_ok",
    "health_check_ok",
    "operator_ping",
    "intermediate_result",
}


def classify_event(event_type: str,
                   value_score: int = 0) -> EventCategory:
    """Classify an event by type."""
    if event_type in _PERSISTENT_TYPES:
        return _PERSISTENT_TYPES[event_type]
    if event_type in _RAM_ONLY_TYPES:
        return "ephemeral"
    return "ephemeral"
