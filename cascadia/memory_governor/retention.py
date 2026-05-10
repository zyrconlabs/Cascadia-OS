"""
Retention enforcement.

Phase 3 enforces max disk bytes, high-water cleanup, etc.
Skeleton is a no-op when MEMORY_GOVERNOR_ENABLED=false.
"""

from cascadia.memory_governor.flags import MEMORY_GOVERNOR_ENABLED


def enforce_retention(directory: str,
                      max_bytes: int,
                      high_water_bytes: int) -> dict:
    """Sweep a directory for retention enforcement."""
    if not MEMORY_GOVERNOR_ENABLED:
        return {
            "enforced":      False,
            "reason":        "MEMORY_GOVERNOR_ENABLED=false",
            "bytes_freed":   0,
            "files_removed": 0,
        }
    raise NotImplementedError(
        "enforce_retention() is a Phase 3 capability."
    )
