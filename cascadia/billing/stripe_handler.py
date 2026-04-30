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
    'price_pro_annual':       'pro',
    'price_business_monthly': 'business',
    'price_business_annual':  'business',
    'price_enterprise':       'enterprise',
}


class StripeHandler:
    """
    Owns Stripe event processing and tier lifecycle.
    Does not own key generation or email delivery.
    """

    def __init__(self, webhook_secret: str, price_map: Dict[str, str] = None,
                 sub_manager=None, email=None, license_gen=None) -> None:
        self._secret = webhook_secret.encode()
        self._price_map = price_map or STRIPE_TIER_MAP
        self._processed_events: set = set()  # in-memory fallback; persistent dedup via sub_manager
        self._sub_manager = sub_manager
        self._email = email
        self._license_gen = license_gen

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
        Replay-safe: duplicate event_ids are ignored (persistent if sub_manager available).
        """
        event_id = event.get('id', '')
        if event_id:
            # Prefer persistent dedup via SubscriptionManager; fall back to in-memory set
            if self._sub_manager and hasattr(self._sub_manager, 'is_event_processed'):
                if self._sub_manager.is_event_processed(event_id):
                    logger.warning('Stripe: duplicate event %s — skipping', event_id)
                    return None
                self._sub_manager.mark_event_processed(event_id)
            elif event_id in self._processed_events:
                logger.warning('Stripe: duplicate event %s — skipping', event_id)
                return None
            else:
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

    def handle(self, body: bytes, sig: str) -> Optional[Dict[str, Any]]:
        """Full lifecycle: verify → parse → process → act on result using injected dependencies."""
        if not self.verify_signature(body, sig):
            logger.warning('Stripe handle: invalid signature — rejected')
            return None
        try:
            event = json.loads(body)
        except Exception as exc:
            logger.error('Stripe handle: invalid JSON: %s', exc)
            return None
        result = self.process_event(event)
        if result is None:
            return None
        action = result.get('action')
        if action == 'activate':
            customer_id = result.get('customer_id', '')
            email_addr = result.get('customer_email', '')
            tier = result.get('tier', 'lite')
            license_key = ''
            if self._license_gen:
                try:
                    license_key = self._license_gen.generate_key(tier, customer_id)
                except Exception as exc:
                    logger.error('Stripe handle: license gen failed: %s', exc)
            if self._sub_manager:
                try:
                    self._sub_manager.upsert_customer(
                        stripe_customer_id=customer_id,
                        email=email_addr,
                        tier=tier,
                        license_key=license_key or None,
                    )
                except Exception as exc:
                    logger.error('Stripe handle: sub_manager upsert failed: %s', exc)
            if self._email and email_addr and license_key:
                try:
                    self._email.send_welcome(email_addr, tier, license_key)
                except Exception as exc:
                    logger.error('Stripe handle: welcome email failed: %s', exc)
        elif action == 'deactivate':
            customer_id = result.get('customer_id', '')
            if self._sub_manager:
                try:
                    customer = self._sub_manager.get_customer(customer_id)
                    if customer and self._email:
                        self._email.send_cancellation(customer['email'], customer['tier'])
                    self._sub_manager.downgrade_to_lite(customer_id)
                except Exception as exc:
                    logger.error('Stripe handle: deactivate failed: %s', exc)
        return result
