# MATURITY: PRODUCTION — Lightweight audit events per run.
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from cascadia.durability.run_store import RunStore


class RunTrace:
    """Owns lightweight run audit events. Does not own dashboards or metrics backends."""

    def __init__(self, run_store: RunStore) -> None:
        self.run_store = run_store

    def log(self, run_id: str, event_type: str, step_index: int | None, payload: Dict[str, Any]) -> None:
        """Owns appending trace events. Does not own caller-side event selection."""
        self.run_store.trace_event(run_id, event_type, step_index, payload, datetime.now(timezone.utc).isoformat())
