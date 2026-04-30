"""
cascadia/depot/purchase_webhook.py — Task A5
Purchase → Auto-Install Webhook · Zyrcon Labs · v1.0.0

Owns: receiving Stripe checkout.session.completed webhook events for DEPOT
      operator purchases, validating the signature, and triggering auto-install
      via the installer module.  Publishes sync events after install.
Does not own: subscription billing events (cascadia/billing/stripe_handler.py),
              payment creation/Stripe API calls, credential storage.

Stripe event metadata schema for DEPOT purchases:
  metadata.operator_id   — DEPOT operator/connector id
  metadata.customer_id   — Cascadia customer id (may differ from Stripe customer)
  metadata.package_url   — optional download URL for the zip bundle

HTTP endpoint: POST /webhook/stripe/purchase
Stripe must send: Stripe-Signature header
"""
from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cascadia.depot.installer import install, InstallResult
from cascadia.depot.manifest_validator import validate_depot_manifest

NAME = "purchase-webhook"
VERSION = "1.0.0"
PORT = int(os.environ.get('PURCHASE_WEBHOOK_PORT', '6209'))

# Stripe webhook signing secret — set via environment
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

# Tolerance for Stripe timestamp (5 minutes)
_STRIPE_TIMESTAMP_TOLERANCE = 300

log = logging.getLogger('depot.purchase_webhook')

_start_time = time.time()

# Replay protection — set of processed event IDs
_processed_events: set = set()
_processed_lock = threading.Lock()

# Shared NATS connection for sync publishing (injected at startup)
_nc: Any = None


# ── Stripe signature verification ─────────────────────────────────────────────

def verify_stripe_signature(payload: bytes, sig_header: str, secret: str,
                              tolerance: int = _STRIPE_TIMESTAMP_TOLERANCE) -> bool:
    """
    Verify a Stripe webhook signature.
    sig_header format: t=<timestamp>,v1=<sig>[,v1=<sig>...]
    Returns False if secret is empty (soft-mode: accept all).
    """
    if not secret:
        return True  # no secret configured — accept in dev/test

    try:
        parts = dict(p.split('=', 1) for p in sig_header.split(','))
        timestamp = parts.get('t', '')
        signature = parts.get('v1', '')
        if not timestamp or not signature:
            return False

        # Replay check
        ts = int(timestamp)
        if tolerance and abs(time.time() - ts) > tolerance:
            return False

        signed = f'{timestamp}.'.encode() + payload
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


# ── Event parsing ─────────────────────────────────────────────────────────────

def parse_purchase_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract DEPOT purchase info from a Stripe event.
    Returns a purchase dict or None if this event is not a DEPOT purchase.

    Relevant event types:
      checkout.session.completed  — one-time payment for an operator
      payment_intent.succeeded    — alternative trigger (fallback)
    """
    event_id = event.get('id', '')
    event_type = event.get('type', '')
    data = event.get('data', {}).get('object', {})
    metadata = data.get('metadata', {})

    # Only handle events with operator_id in metadata
    operator_id = metadata.get('operator_id', '')
    if not operator_id:
        return None

    if event_type not in ('checkout.session.completed', 'payment_intent.succeeded'):
        return None

    return {
        'event_id': event_id,
        'event_type': event_type,
        'operator_id': operator_id,
        'customer_id': metadata.get('customer_id', '') or data.get('customer', ''),
        'customer_email': (data.get('customer_details', {}).get('email', '') or
                           metadata.get('customer_email', '')),
        'package_url': metadata.get('package_url', ''),
        'amount': data.get('amount_total', 0),
        'currency': data.get('currency', 'usd'),
    }


# ── Purchase processing ───────────────────────────────────────────────────────

def process_purchase(purchase: Dict[str, Any],
                     catalog_lookup: Optional[Any] = None) -> Dict[str, Any]:
    """
    Trigger auto-install for a confirmed purchase.
    catalog_lookup(operator_id) → Optional[manifest_dict]  (from DEPOT API catalog)
    """
    operator_id = purchase['operator_id']
    event_id = purchase['event_id']

    # Replay protection
    with _processed_lock:
        if event_id in _processed_events:
            log.warning('Duplicate purchase event %s — skipping', event_id)
            return {'ok': False, 'error': 'duplicate event', 'event_id': event_id}
        _processed_events.add(event_id)

    log.info('Processing purchase: %s by %s (event %s)',
             operator_id, purchase.get('customer_email', '?'), event_id)

    # Resolve manifest from catalog or package_url
    manifest: Optional[Dict[str, Any]] = None
    package_url: Optional[str] = purchase.get('package_url') or None

    if catalog_lookup is not None:
        manifest = catalog_lookup(operator_id)

    result: InstallResult = install(
        operator_id=operator_id,
        package_source=package_url,
        manifest_override=manifest,
        source='purchase',
        dry_run=False,
    )

    # Publish sync event (best-effort, async)
    if _nc is not None and result.ok and result.manifest:
        import asyncio
        try:
            from cascadia.depot.sync_publisher import publish_installed
            asyncio.run_coroutine_threadsafe(
                publish_installed(_nc, result.manifest, source='purchase',
                                  health_ok=result.health_ok),
                _get_loop(),
            )
        except Exception as exc:
            log.warning('Sync publish failed after purchase: %s', exc)

    outcome = {
        'ok': result.ok,
        'event_id': event_id,
        'operator_id': operator_id,
        'customer_id': purchase.get('customer_id', ''),
        'install_ok': result.ok,
        'health_ok': result.health_ok,
        'error': result.error,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }

    if result.ok:
        log.info('Auto-install succeeded: %s (health=%s)', operator_id, result.health_ok)
    else:
        log.error('Auto-install failed: %s — %s', operator_id, result.error)

    return outcome


# ── Event loop helper ─────────────────────────────────────────────────────────

_loop = None
_loop_lock = threading.Lock()


def _get_loop():
    global _loop
    with _loop_lock:
        if _loop is None:
            import asyncio
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True).start()
    return _loop


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _PurchaseWebhookHandler(http.server.BaseHTTPRequestHandler):

    # Injected at server construction time
    catalog_lookup = None
    stripe_secret = STRIPE_WEBHOOK_SECRET

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> bytes:
        n = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(n) if n else b''

    def do_GET(self) -> None:
        path = self.path.split('?')[0].rstrip('/')
        if path == '/health':
            self._json(200, {
                'status': 'healthy', 'service': NAME, 'version': VERSION,
                'port': PORT,
                'processed_events': len(_processed_events),
                'uptime_seconds': round(time.time() - _start_time),
            })
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self) -> None:
        path = self.path.split('?')[0].rstrip('/')

        if path != '/webhook/stripe/purchase':
            self._json(404, {'error': 'not found'})
            return

        body = self._body()
        sig = self.headers.get('Stripe-Signature', '')

        if not verify_stripe_signature(body, sig, self.stripe_secret):
            log.warning('Invalid Stripe signature from %s', self.client_address)
            self._json(400, {'error': 'invalid signature'})
            return

        try:
            event = json.loads(body)
        except Exception:
            self._json(400, {'error': 'invalid JSON'})
            return

        purchase = parse_purchase_event(event)
        if purchase is None:
            # Not a DEPOT purchase event — acknowledge and ignore
            self._json(200, {'ok': True, 'action': 'ignored'})
            return

        outcome = process_purchase(purchase, catalog_lookup=self.catalog_lookup)
        # Always return 200 to Stripe so it doesn't retry
        self._json(200, outcome)

    def log_message(self, *_args: Any) -> None:
        pass


# ── Server factory ────────────────────────────────────────────────────────────

def create_server(port: int = PORT,
                  catalog_lookup=None,
                  stripe_secret: str = STRIPE_WEBHOOK_SECRET,
                  ) -> http.server.HTTPServer:
    """
    Create the purchase webhook HTTP server.
    catalog_lookup(operator_id) → Optional[manifest_dict]
    """
    handler = type('Handler', (_PurchaseWebhookHandler,), {
        'catalog_lookup': staticmethod(catalog_lookup) if catalog_lookup else None,
        'stripe_secret': stripe_secret,
    })
    return http.server.HTTPServer(('', port), handler)


def start(port: int = PORT,
          catalog_lookup=None,
          stripe_secret: str = STRIPE_WEBHOOK_SECRET,
          block: bool = True) -> http.server.HTTPServer:
    server = create_server(port, catalog_lookup, stripe_secret)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info('%s v%s running on port %s', NAME, VERSION, port)
    if block:
        try:
            t.join()
        except KeyboardInterrupt:
            server.shutdown()
    return server


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s [purchase-webhook] %(message)s')
    start(block=True)
