"""
External action outbox.

Prevents duplicate external side effects after a crash.
Phase 3 implements actual SQLite operations.
"""

from typing import Optional
from cascadia.memory_governor.flags import OUTBOX_ENABLED


class Outbox:
    """Idempotent outbox for external actions."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._enabled = OUTBOX_ENABLED

    def enqueue(self,
                run_id: str,
                action_type: str,
                payload_hash: str,
                idempotency_key: str) -> Optional[int]:
        if not self._enabled:
            return None
        raise NotImplementedError(
            "Outbox.enqueue() is a Phase 3 capability. "
            "Set OUTBOX_ENABLED=false (default) until then."
        )

    def mark_completed(self,
                       outbox_id: int,
                       external_result_id: str) -> None:
        if not self._enabled:
            return
        raise NotImplementedError(
            "Outbox.mark_completed() is a Phase 3 capability."
        )

    def get_pending(self) -> list:
        if not self._enabled:
            return []
        raise NotImplementedError(
            "Outbox.get_pending() is a Phase 3 capability."
        )
