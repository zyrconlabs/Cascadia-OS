"""
Cascadia Memory Governor feature flags.

Flags live in their own module so submodules can import
them without circular import risk. __init__.py re-exports
these for public consumption.

All flags default to false. Skeleton phase: enabling any
flag triggers NotImplementedError where applicable.
"""

import os


def _flag(name: str) -> bool:
    """Read env var; treat 'true' (case-insensitive) as True."""
    return os.environ.get(name, "false").lower() == "true"


MEMORY_GOVERNOR_ENABLED    = _flag("MEMORY_GOVERNOR_ENABLED")
OUTBOX_ENABLED             = _flag("OUTBOX_ENABLED")
RAM_LOG_BUFFER_ENABLED     = _flag("RAM_LOG_BUFFER_ENABLED")
MISSION_COMPACTION_ENABLED = _flag("MISSION_COMPACTION_ENABLED")
