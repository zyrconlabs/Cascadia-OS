"""
Persistence policy decisions.

should_persist() answers: 'given this event, does it
deserve disk?' Defers to classifier.py.
"""

from cascadia.memory_governor.classifier import classify_event

_DURABLE = {"checkpoint", "audit", "security", "business_memory"}


def should_persist(event_type: str,
                   value_score: int = 0) -> bool:
    """Decide if an event should hit disk."""
    category = classify_event(event_type, value_score)
    return category in _DURABLE
