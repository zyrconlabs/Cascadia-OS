"""
Mission summary compactor.

When MISSION_COMPACTION_ENABLED=false, returns input trace
unchanged. Phase 3 implements actual compaction.
"""

from cascadia.memory_governor.flags import MISSION_COMPACTION_ENABLED


def compact_mission(mission_id: str,
                    full_trace: list) -> dict:
    if not MISSION_COMPACTION_ENABLED:
        return {
            "mission_id": mission_id,
            "trace":      full_trace,
            "compacted":  False,
        }
    raise NotImplementedError(
        "compact_mission() is a Phase 3 capability."
    )
