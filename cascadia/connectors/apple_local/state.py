"""State types for the Apple local connector.

Phase 1 did not run migrations, create a database, or start background sync.
Stage 3A adds an in-memory pending-approval store: a mutating action that
arrives without approval is parked here (keyed by a short request_id) until
the owner taps Approve/Deny in Telegram, at which point the callback handler
pops it and executes. The store is process-local by design — it lives only
for the lifetime of the running connector, which is exactly the Terminal-run
session scope of Stage 3A (durability across restarts is Stage 3B).
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ── Pending-approval store ────────────────────────────────────────────────
_pending_lock = threading.Lock()
_pending_approvals: dict[str, dict[str, Any]] = {}


def add_pending_approval(action: str, payload: dict[str, Any]) -> str:
    """Park a mutating request awaiting owner approval; return its request_id.

    The id is a 12-char hex slug so the Telegram callback_data
    (``apple:approve:<id>`` = 26 bytes) stays well under Telegram's 64-byte cap.
    """
    request_id = uuid.uuid4().hex[:12]
    with _pending_lock:
        _pending_approvals[request_id] = {
            "action": action,
            "payload": payload,
            "created_at": time.time(),
        }
    return request_id


def pop_pending_approval(request_id: str) -> dict[str, Any] | None:
    """Atomically remove and return a pending entry, or None if unknown/expired."""
    with _pending_lock:
        return _pending_approvals.pop(request_id, None)


def get_pending_approval(request_id: str) -> dict[str, Any] | None:
    with _pending_lock:
        entry = _pending_approvals.get(request_id)
        return dict(entry) if entry else None


def pending_count() -> int:
    with _pending_lock:
        return len(_pending_approvals)


@dataclass
class AppleLocalState:
    schema_version: int = 1
    sync_enabled: bool = False
    last_sync_by_domain: dict[str, str | None] = field(
        default_factory=lambda: {
            "calendar": None,
            "reminders": None,
            "notes": None,
        }
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sync_enabled": self.sync_enabled,
            "last_sync_by_domain": dict(self.last_sync_by_domain),
            "metadata": dict(self.metadata),
        }
