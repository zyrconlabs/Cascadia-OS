"""
cascadia/core/stripe_webhook.py — Cascadia OS
Stripe webhook receiver and checkout URL service. Port 6101.
Owns: loading stripe config, webhook signature verification,
      config introspection, and checkout URL routing.
Does not own: license key generation, email delivery, tier validation.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from typing import Any, Dict

# Allow running standalone or as part of the cascadia package
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cascadia.billing.stripe_handler import StripeHandler

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [stripe_webhook] %(message)s')
logger = logging.getLogger('stripe_webhook')

PORT = int(os.environ.get('STRIPE_WEBHOOK_PORT', '6101'))

CONFIG_FILE = _ROOT / 'stripe.config.json'


def _load_stripe_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        logger.warning('stripe.config.json not found — using env vars')
        return {}


_stripe_cfg = _load_stripe_config()

# Build PRICE_TO_TIER from config at module load
PRICE_TO_TIER: Dict[str, str] = {
    v: k for k, v in _stripe_cfg.get('price_ids', {}).items()
    if 'REPLACE' not in v and v
}

# Webhook secret: prefer config, fall back to env var
_cfg_secret = _stripe_cfg.get('webhook_secret', '')
WEBHOOK_SECRET: str = (
    _cfg_secret
    if _cfg_secret and 'REPLACE' not in _cfg_secret
    else os.environ.get('STRIPE_WEBHOOK_SECRET', '')
)

_handler = StripeHandler(
    webhook_secret=WEBHOOK_SECRET or 'placeholder',
    price_map=PRICE_TO_TIER or None,
)

logger.info(
    'Stripe config loaded — tiers mapped: %d, webhook_secret: %s, mode: %s',
    len(PRICE_TO_TIER),
    'configured' if WEBHOOK_SECRET else 'missing',
    _stripe_cfg.get('mode', 'unknown'),
)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _handle_stripe_config() -> tuple[int, Dict[str, Any]]:
    """GET /api/stripe/config — returns non-secret config status."""
    return 200, {
        'mode': _stripe_cfg.get('mode', 'unknown'),
        'price_ids_configured': len(PRICE_TO_TIER) >= 1,
        'webhook_secret_configured': bool(WEBHOOK_SECRET),
        'tiers_available': list(_stripe_cfg.get('price_ids', {}).keys()),
    }


def _handle_checkout_url(tier: str) -> tuple[int, Dict[str, Any]]:
    """GET /api/stripe/checkout-url?tier=pro"""
    links = _stripe_cfg.get('checkout_links', {})
    url = links.get(tier, '')
    if not url or 'REPLACE' in url:
        logger.warning('Checkout URL not configured for tier: %s', tier)
        return 404, {'error': 'not_configured', 'tier': tier}
    return 200, {'url': url, 'tier': tier}


def _handle_webhook(raw_body: bytes, sig_header: str) -> tuple[int, Dict[str, Any]]:
    """POST /api/stripe/webhook — verify and process event."""
    if not _handler.verify_signature(raw_body, sig_header):
        logger.warning('Stripe webhook: invalid signature')
        return 400, {'error': 'invalid_signature'}
    try:
        event = json.loads(raw_body)
    except Exception:
        return 400, {'error': 'invalid_json'}
    result = _handler.process_event(event)
    if result:
        logger.info('Stripe event processed: action=%s', result.get('action'))
    return 200, {'received': True, 'action': result.get('action') if result else None}


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class StripeRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.debug(fmt, *args)

    def _send_json(self, status: int, body: Dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        qs = parse_qs(parsed.query)

        if path == '/api/stripe/config':
            status, body = _handle_stripe_config()
            self._send_json(status, body)

        elif path == '/api/stripe/checkout-url':
            tier = qs.get('tier', [''])[0]
            if not tier:
                self._send_json(400, {'error': 'tier param required'})
            else:
                status, body = _handle_checkout_url(tier)
                self._send_json(status, body)

        elif path == '/api/health':
            self._send_json(200, {
                'service': 'stripe_webhook',
                'status': 'ok',
                'port': PORT,
                'tiers_configured': len(PRICE_TO_TIER),
            })

        else:
            self._send_json(404, {'error': 'not_found', 'path': path})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        length = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(length) if length else b''

        if path == '/api/stripe/webhook':
            sig_header = self.headers.get('Stripe-Signature', '')
            status, body = _handle_webhook(raw_body, sig_header)
            self._send_json(status, body)
        else:
            self._send_json(404, {'error': 'not_found', 'path': path})


def run() -> None:
    server = HTTPServer(('0.0.0.0', PORT), StripeRequestHandler)
    logger.info('stripe_webhook starting on port %d', PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info('stripe_webhook stopped')


if __name__ == '__main__':
    run()
