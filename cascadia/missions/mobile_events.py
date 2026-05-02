"""Mobile Event Bridge — converts internal mission events to mobile-safe payloads.

Delivery priority (highest to lowest):
  1. ServiceRuntime.broadcast_event() — WebSocket push to connected mobile clients
  2. NATS internal publish (cascadia.missions.*) — server-side bus only
  3. In-process deque — REST polling fallback via /api/missions/events/pending

ARCHITECTURE NOTE:
  Mobile devices connect via WebSocket to port 6207 (/missions/ws) or
  REST-poll /api/missions/events/pending. They NEVER connect to NATS directly.
  NATS is used only for internal server-to-server event routing.
"""
from __future__ import annotations

import collections
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

QUEUE_MAX = 100
NATS_URL = "nats://localhost:4222"


# ── Mobile payload format ─────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_mobile_event(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "event": event_type,
        "mission_id": payload.get("mission_id", ""),
        "mission_run_id": payload.get("mission_run_id", ""),
        "title": payload.get("title", event_type),
        "summary": payload.get("summary", ""),
        "timestamp": _now_iso(),
        "data": {
            k: v for k, v in payload.items()
            if k not in ("mission_id", "mission_run_id", "title", "summary")
        },
    }


# ── Bridge ────────────────────────────────────────────────────────────────────

class MobileMissionEventBridge:
    """
    Converts mission lifecycle events into mobile-safe payloads and delivers
    them via WebSocket broadcast, NATS internal publish, and in-process queue.
    """

    def __init__(self) -> None:
        self._queue: collections.deque = collections.deque(maxlen=QUEUE_MAX)
        self._lock = threading.Lock()
        self._ws_runtime: Any = None

    def set_ws_runtime(self, runtime: Any) -> None:
        """Wire a ServiceRuntime instance to enable WebSocket broadcasts."""
        self._ws_runtime = runtime

    def publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Convert and deliver event. Never raises."""
        try:
            event = _to_mobile_event(event_type, payload)
        except Exception as exc:
            log.warning("mobile_events: format failed for %s: %s", event_type, exc)
            return

        # 1. WebSocket broadcast (mobile clients on /missions/ws)
        if self._ws_runtime is not None:
            try:
                self._ws_runtime.broadcast_event(event)
            except Exception as exc:
                log.warning("mobile_events: WS broadcast failed: %s", exc)

        # 2. NATS internal publish — never exposed to mobile directly
        _nats_publish_async(
            f"cascadia.missions.{event_type.replace('.', '_')}",
            event,
        )

        # 3. In-process queue for REST polling fallback
        with self._lock:
            self._queue.append(event)

    def get_pending_events(self, since_timestamp: Optional[str] = None) -> List[dict]:
        """Return queued events newer than since_timestamp (ISO 8601), or all if None."""
        with self._lock:
            events = list(self._queue)
        if not since_timestamp:
            return events
        try:
            since = datetime.fromisoformat(since_timestamp.replace("Z", "+00:00"))
            return [
                e for e in events
                if datetime.fromisoformat(
                    e["timestamp"].replace("Z", "+00:00")
                ) > since
            ]
        except Exception:
            return events

    def clear_delivered(self, event_ids: List[str]) -> int:
        """Remove events by event_id. Returns count removed."""
        id_set = set(event_ids)
        with self._lock:
            remaining = [e for e in self._queue if e.get("event_id") not in id_set]
            removed = len(self._queue) - len(remaining)
            self._queue.clear()
            self._queue.extend(remaining)
        return removed


# ── NATS helper ───────────────────────────────────────────────────────────────

def _nats_publish_async(subject: str, payload: Dict[str, Any]) -> None:
    """Fire-and-forget NATS publish in a daemon thread. Never raises."""
    def _run() -> None:
        try:
            import asyncio
            import nats  # type: ignore

            async def _pub() -> None:
                nc = await nats.connect(NATS_URL)
                await nc.publish(subject, json.dumps(payload).encode())
                await nc.drain()

            loop = asyncio.new_event_loop()
            loop.run_until_complete(_pub())
            loop.close()
        except Exception as exc:
            log.debug("NATS publish skipped (non-fatal): %s", exc)

    threading.Thread(target=_run, daemon=True, name="nats-missions-pub").start()


# ── Module-level singleton ────────────────────────────────────────────────────

_bridge: Optional[MobileMissionEventBridge] = None
_bridge_lock = threading.Lock()


def get_bridge() -> MobileMissionEventBridge:
    """Return the module-level bridge singleton, creating it on first call."""
    global _bridge
    if _bridge is None:
        with _bridge_lock:
            if _bridge is None:
                _bridge = MobileMissionEventBridge()
    return _bridge
