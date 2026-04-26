"""
stripe_handler.py — Cascadia OS v0.46
Stripe webhook receiver and subscription lifecycle manager.
Owns: receiving Stripe events, validating webhook signatures,
      mapping subscription status to tier changes.
Does not own: license key generation (license_generator.py),
              email delivery (HANDSHAKE), tier validation (TierValidator).
"""
# MATURITY: PRODUCTION — Webhook signature verified. Replay-safe via event_id dedup.
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from cascadia.shared.logger import get_logger

logger = get_logger('stripe')

STRIPE_TIER_MAP = {
    'price_pro_monthly':      'pro',
    'price_pro_workspace':    'pro',
    'price_business_starter': 'business',
    'price_business_growth':  'business',
    'price_business_max':     'business',
}


class StripeHandler:
    """
    Owns Stripe event processing and tier lifecycle.
    Does not own key generation or email delivery.
    """

    def __init__(self, webhook_secret: str, price_map: Dict[str, str] = None) -> None:
        self._secret = webhook_secret.encode()
        self._price_map = price_map or STRIPE_TIER_MAP
        self._processed_events: set = set()  # replay protection

    def verify_signature(self, payload: bytes, sig_header: str) -> bool:
        """Verify Stripe-Signature header. Return False if invalid or replayed."""
        try:
            parts = dict(p.split('=', 1) for p in sig_header.split(','))
            timestamp = parts.get('t', '')
            v1 = parts.get('v1', '')
            if not timestamp or not v1:
                return False
            signed = f'{timestamp}.'.encode() + payload
            expected = hmac.new(self._secret, signed, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, v1):
                return False
            # Reject replays older than 5 minutes
            if abs(time.time() - int(timestamp)) > 300:
                return False
            return True
        except Exception:
            return False

    def process_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Process a Stripe event dict.
        Returns action dict or None if no action needed.
        Replay-safe: duplicate event_ids are ignored.
        """
        event_id = event.get('id', '')
        if event_id in self._processed_events:
            logger.warning('Stripe: duplicate event %s — skipping', event_id)
            return None
        self._processed_events.add(event_id)

        etype = event.get('type', '')
        data = event.get('data', {}).get('object', {})

        if etype == 'checkout.session.completed':
            customer_email = data.get('customer_details', {}).get('email', '')
            customer_id = data.get('customer', '')
            # price_id from line_items if expanded, else from metadata
            price_id = (
                data.get('metadata', {}).get('price_id', '') or
                data.get('line_items', {}).get('data', [{}])[0]
                    .get('price', {}).get('id', '')
            )
            tier = self._price_map.get(price_id, 'pro')
            logger.info('Stripe: new subscription — %s for %s', tier, customer_email)
            return {
                'action': 'activate',
                'customer_email': customer_email,
                'tier': tier,
                'customer_id': customer_id,
            }

        if etype in ('customer.subscription.deleted', 'customer.subscription.paused'):
            customer_id = data.get('customer', '')
            logger.info('Stripe: subscription ended for %s', customer_id)
            return {'action': 'deactivate', 'customer_id': customer_id}

        return None
