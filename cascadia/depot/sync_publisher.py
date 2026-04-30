"""
cascadia/depot/sync_publisher.py — Task A4
Desktop → iOS Auto-Sync · Zyrcon Labs · v1.0.0

Owns: publishing catalog-change events to NATS so iOS (and any other
      subscriber) stays in sync with the desktop operator state.
      Publishes on install, uninstall, update, and full-catalog-snapshot.
Does not own: transport to the device (Cascadia iOS app subscribes to NATS),
              install execution (installer.py), registry reads (CREW).

Event subjects:
  cascadia.sync.operators.installed    — new install
  cascadia.sync.operators.uninstalled  — removal
  cascadia.sync.operators.updated      — version change
  cascadia.sync.catalog.snapshot       — full catalog push (on request or startup)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

try:
    import nats
    _NATS_AVAILABLE = True
except ImportError:
    _NATS_AVAILABLE = False

NAME = "depot-sync"
VERSION = "1.0.0"

log = logging.getLogger('depot.sync')


# ── Event model ───────────────────────────────────────────────────────────────

@dataclass
class SyncEvent:
    event_type: str        # installed | uninstalled | updated | snapshot
    operator_id: str
    operator_name: str
    version: str
    port: Optional[int]
    tier_required: str
    category: str
    source: str            # depot | purchase | local | sync
    health_ok: bool
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_nats_payload(self) -> bytes:
        return json.dumps({
            'publisher': NAME,
            **self.to_dict(),
        }).encode()

    @property
    def subject(self) -> str:
        if self.event_type == 'snapshot':
            return 'cascadia.sync.catalog.snapshot'
        return f'cascadia.sync.operators.{self.event_type}'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_event(
    event_type: str,
    manifest: Dict[str, Any],
    source: str = 'depot',
    health_ok: bool = False,
) -> SyncEvent:
    return SyncEvent(
        event_type=event_type,
        operator_id=manifest.get('id', ''),
        operator_name=manifest.get('name', ''),
        version=manifest.get('version', ''),
        port=manifest.get('port'),
        tier_required=manifest.get('tier_required', 'lite'),
        category=manifest.get('category', ''),
        source=source,
        health_ok=health_ok,
        timestamp=_now(),
    )


# ── In-memory event queue (used when NATS is unavailable) ────────────────────

_pending: List[SyncEvent] = []
_pending_lock = asyncio.Lock()


# ── NATS publisher ────────────────────────────────────────────────────────────

async def _publish(nc, event: SyncEvent) -> bool:
    """
    Publish a single SyncEvent.  Returns True on success.
    Falls back to in-memory queue if nc is None or publish fails.
    """
    if nc is None:
        async with _pending_lock:
            _pending.append(event)
        log.debug('NATS unavailable — queued sync event %s/%s',
                  event.event_type, event.operator_id)
        return False

    try:
        await nc.publish(event.subject, event.to_nats_payload())
        log.info('Sync event published: %s → %s', event.subject, event.operator_id)
        return True
    except Exception as exc:
        log.warning('Sync publish failed (%s) — queuing: %s', exc, event.operator_id)
        async with _pending_lock:
            _pending.append(event)
        return False


async def _drain_pending(nc) -> int:
    """Attempt to publish queued events.  Returns number successfully sent."""
    async with _pending_lock:
        to_send = list(_pending)
        _pending.clear()

    sent = 0
    failed: List[SyncEvent] = []
    for event in to_send:
        try:
            await nc.publish(event.subject, event.to_nats_payload())
            sent += 1
        except Exception:
            failed.append(event)

    if failed:
        async with _pending_lock:
            _pending.extend(failed)

    if sent:
        log.info('Drained %s queued sync events', sent)
    return sent


# ── Public sync API ───────────────────────────────────────────────────────────

async def publish_installed(nc, manifest: Dict[str, Any],
                             source: str = 'depot', health_ok: bool = False) -> bool:
    event = build_event('installed', manifest, source, health_ok)
    return await _publish(nc, event)


async def publish_uninstalled(nc, manifest: Dict[str, Any]) -> bool:
    event = build_event('uninstalled', manifest)
    return await _publish(nc, event)


async def publish_updated(nc, manifest: Dict[str, Any],
                           source: str = 'depot', health_ok: bool = False) -> bool:
    event = build_event('updated', manifest, source, health_ok)
    return await _publish(nc, event)


async def publish_snapshot(nc, operators: List[Dict[str, Any]]) -> int:
    """Publish one snapshot event per operator.  Returns count published."""
    count = 0
    for manifest in operators:
        event = build_event('snapshot', manifest, source='sync')
        if await _publish(nc, event):
            count += 1
    return count


# ── Sync listener (receives requests from iOS) ────────────────────────────────

async def handle_sync_request(nc, subject: str, raw: bytes,
                               catalog_fn: Optional[Callable[[], List[Dict[str, Any]]]] = None
                               ) -> None:
    """
    Handle a sync request from iOS (or any subscriber).
    Subjects:
      cascadia.sync.request.snapshot  — publish full catalog snapshot
      cascadia.sync.request.ping      — reply with health/version
    """
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}

    if subject == 'cascadia.sync.request.snapshot':
        operators = catalog_fn() if catalog_fn else []
        count = await publish_snapshot(nc, operators)
        await nc.publish(
            'cascadia.sync.response.snapshot',
            json.dumps({
                'publisher': NAME, 'operators_sent': count,
                'timestamp': _now(),
            }).encode(),
        )
        log.info('Snapshot published: %s operators', count)

    elif subject == 'cascadia.sync.request.ping':
        await nc.publish(
            'cascadia.sync.response.pong',
            json.dumps({
                'publisher': NAME, 'version': VERSION,
                'queued_events': len(_pending),
                'timestamp': _now(),
            }).encode(),
        )


# ── Entry point (standalone) ──────────────────────────────────────────────────

async def main(catalog_fn: Optional[Callable[[], List[Dict[str, Any]]]] = None) -> None:
    if not _NATS_AVAILABLE:
        log.warning('nats-py not installed — sync publisher in no-op mode')
        await asyncio.sleep(float('inf'))
        return

    nc = await nats.connect('nats://localhost:4222')
    log.info('%s v%s connected to NATS', NAME, VERSION)

    # Drain any pending events accumulated before NATS was available
    await _drain_pending(nc)

    # Subscribe to sync requests from iOS
    async def _handler(msg):
        await handle_sync_request(nc, msg.subject, msg.data, catalog_fn)

    await nc.subscribe('cascadia.sync.request.>', cb=_handler)
    log.info('Listening on cascadia.sync.request.>')
    await asyncio.sleep(float('inf'))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s [depot-sync] %(message)s')
    asyncio.run(main())
