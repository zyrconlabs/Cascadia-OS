"""State types for the Apple local connector.

Phase 1 did not run migrations, create a database, or start background sync.
Stage 3A added an in-memory pending-approval store: a mutating action that
arrives without approval is parked (keyed by a short request_id) until the
owner taps Approve/Deny in Telegram, at which point the callback handler pops
it and executes.

Stage 3B makes that store durable across restarts. The connector now runs as a
KeepAlive LaunchAgent (ai.zyrcon.apple-local) rather than a Terminal session,
so a crash-and-respawn must not silently drop approvals the owner has not yet
answered. Entries are persisted to a JSON file under the CORE data/runtime/
directory — the same convention current_release.json / crew_registry.json use
— and reloaded on process startup. Writes are atomic (temp-file + os.replace)
so an ungraceful kill mid-write can never leave a corrupt file behind.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Durable pending-approval store ────────────────────────────────────────
_pending_lock = threading.Lock()

# Tests point this at a temp file so they never touch the production store.
_ENV_STORE_OVERRIDE = "APPLE_LOCAL_PENDING_STORE"


def _store_path() -> Path:
    """Resolve the on-disk store path.

    Defaults to the CORE data/runtime/ dir resolved relative to this file
    (state.py → apple_local → connectors → cascadia → repo root == parents[3]),
    never a hardcoded /Users/... path. Overridable via APPLE_LOCAL_PENDING_STORE
    for tests.
    """
    override = os.environ.get(_ENV_STORE_OVERRIDE)
    if override:
        return Path(override).expanduser()
    return (
        Path(__file__).resolve().parents[3]
        / "data"
        / "runtime"
        / "apple_local_pending_approvals.json"
    )


def _load_from_disk() -> dict[str, dict[str, Any]]:
    """Read persisted approvals, tolerating a missing or corrupt file.

    A truncated/garbage file (e.g. a crash mid-write under a legacy non-atomic
    writer) must never crash startup — we start empty rather than raise.
    """
    path = _store_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _persist(entries: dict[str, dict[str, Any]]) -> None:
    """Atomically write the full store to disk.

    Serialise to a temp file in the SAME directory, fsync, then os.replace() —
    a power loss or SIGKILL leaves either the previous file or the new one
    intact on disk, never a half-written JSON document. Caller holds the lock.
    """
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=".apple_local_pending.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# Reload any approvals parked before a restart so they survive process death.
_pending_approvals: dict[str, dict[str, Any]] = _load_from_disk()


def add_pending_approval(action: str, payload: dict[str, Any]) -> str:
    """Park a mutating request awaiting owner approval; return its request_id.

    The id is a 12-char hex slug so the Telegram callback_data
    (``apple:approve:<id>`` = 26 bytes) stays well under Telegram's 64-byte cap.
    The store is persisted before returning, so a crash immediately after the
    owner is notified cannot lose the parked request.
    """
    request_id = uuid.uuid4().hex[:12]
    with _pending_lock:
        _pending_approvals[request_id] = {
            "action": action,
            "payload": payload,
            "created_at": time.time(),
        }
        _persist(_pending_approvals)
    return request_id


def pop_pending_approval(request_id: str) -> dict[str, Any] | None:
    """Atomically remove and return a pending entry, or None if unknown/expired.

    The removal is persisted so a restart cannot resurrect an already-handled
    approval.
    """
    with _pending_lock:
        entry = _pending_approvals.pop(request_id, None)
        if entry is not None:
            _persist(_pending_approvals)
        return entry


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
