"""
Cascadia Memory Governor.

Selective, RAM-first, value-aware persistence for Cascadia.

This module is the foundation for:
  - RAM ring buffer (hot logs)
  - Outbox for external actions (idempotent, crash-safe)
  - Mission summary compaction
  - Value-based persistence policy
  - Retention enforcement

All capabilities are disabled by default. Each is gated
behind its own environment flag (defined in flags.py):
  MEMORY_GOVERNOR_ENABLED=false
  OUTBOX_ENABLED=false
  RAM_LOG_BUFFER_ENABLED=false
  MISSION_COMPACTION_ENABLED=false

Cascadia must behave identically when all flags are false.
"""

VERSION = "0.1.0-skeleton"

# Re-export flags from flags.py
from cascadia.memory_governor.flags import (
    MEMORY_GOVERNOR_ENABLED,
    OUTBOX_ENABLED,
    RAM_LOG_BUFFER_ENABLED,
    MISSION_COMPACTION_ENABLED,
)

# Re-export public API from submodules
from cascadia.memory_governor.classifier import classify_event
from cascadia.memory_governor.ring_buffer import RingBuffer
from cascadia.memory_governor.outbox import Outbox
from cascadia.memory_governor.compactor import compact_mission
from cascadia.memory_governor.policy import should_persist
from cascadia.memory_governor.retention import enforce_retention

__all__ = [
    "VERSION",
    "MEMORY_GOVERNOR_ENABLED",
    "OUTBOX_ENABLED",
    "RAM_LOG_BUFFER_ENABLED",
    "MISSION_COMPACTION_ENABLED",
    "classify_event",
    "RingBuffer",
    "Outbox",
    "compact_mission",
    "should_persist",
    "enforce_retention",
]
