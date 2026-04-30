from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest

from cascadia.billing.stripe_handler import StripeHandler, STRIPE_TIER_MAP


def _make_sig(secret: str, payload: bytes, timestamp: str) -> str:
    signed = f'{timestamp}.'.encode() + payload
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f't={timestamp},v1={sig}'


SECRET = 'whsec_test_secret_abc123'


class StripeHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = StripeHandler(SECRET)

    def _make_event(self, etype: str, data: dict) -> dict:
        return {'id': f'evt_{time.time_ns()}', 'type': etype, 'data': {'object': data}}

    def test_valid_signature_accepted(self) -> None:
        payload = b'{"id":"evt_1","type":"ping"}'
        ts = str(int(time.time()))
        sig = _make_sig(SECRET, payload, ts)
        self.assertTrue(self.handler.verify_signature(payload, sig))

    def test_wrong_secret_rejected(self) -> None:
        payload = b'{"id":"evt_1","type":"ping"}'
        ts = str(int(time.time()))
        sig = _make_sig('wrong_secret', payload, ts)
        self.assertFalse(self.handler.verify_signature(payload, sig))

    def test_replay_rejected(self) -> None:
        payload = b'{"id":"evt_1","type":"ping"}'
        old_ts = str(int(time.time()) - 400)  # > 5 minutes old
        sig = _make_sig(SECRET, payload, old_ts)
        self.assertFalse(self.handler.verify_signature(payload, sig))

    def test_tampered_payload_rejected(self) -> None:
        payload = b'{"id":"evt_1","type":"ping"}'
        ts = str(int(time.time()))
        sig = _make_sig(SECRET, payload, ts)
        self.assertFalse(self.handler.verify_signature(b'tampered', sig))

    def test_checkout_completed_returns_activate(self) -> None:
        event = {
            'id': 'evt_checkout_1',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'customer_details': {'email': 'owner@acme.com'},
                    'customer': 'cus_abc123',
                    'metadata': {'price_id': 'price_pro_monthly'},
                }
            }
        }
        result = self.handler.process_event(event)
        self.assertIsNotNone(result)
        self.assertEqual(result['action'], 'activate')
        self.assertEqual(result['tier'], 'pro')
        self.assertEqual(result['customer_email'], 'owner@acme.com')

    def test_subscription_deleted_returns_deactivate(self) -> None:
        event = {
            'id': 'evt_sub_del_1',
            'type': 'customer.subscription.deleted',
            'data': {'object': {'customer': 'cus_abc123'}}
        }
        result = self.handler.process_event(event)
        self.assertIsNotNone(result)
        self.assertEqual(result['action'], 'deactivate')

    def test_duplicate_event_skipped(self) -> None:
        event = {
            'id': 'evt_duplicate',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'customer_details': {'email': 'x@x.com'},
                    'customer': 'cus_x',
                    'metadata': {'price_id': 'price_pro_monthly'},
                }
            }
        }
        first = self.handler.process_event(event)
        second = self.handler.process_event(event)
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_unknown_event_type_returns_none(self) -> None:
        event = {'id': 'evt_unknown', 'type': 'invoice.paid', 'data': {'object': {}}}
        self.assertIsNone(self.handler.process_event(event))

    def test_business_tier_mapping(self) -> None:
        event = {
            'id': 'evt_biz',
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'customer_details': {'email': 'biz@corp.com'},
                    'customer': 'cus_biz',
                    'metadata': {'price_id': 'price_business_growth'},
                }
            }
        }
        result = self.handler.process_event(event)
        self.assertEqual(result['tier'], 'business_growth')

    def test_empty_sig_header_rejected(self) -> None:
        self.assertFalse(self.handler.verify_signature(b'data', ''))


if __name__ == '__main__':
    unittest.main()
