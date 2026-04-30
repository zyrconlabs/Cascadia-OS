"""
Billing failure tests — Step 4.
Covers: duplicate webhook, expired checkout, invalid signature, revoked license.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest

from cascadia.billing.stripe_handler import StripeHandler, STRIPE_TIER_MAP
from cascadia.billing.subscription_manager import SubscriptionManager
from cascadia.licensing.tier_validator import TierValidator

SECRET = 'testsecret-abc123'
FUTURE = int(time.time()) + 86400 * 365
PAST   = int(time.time()) - 86400 * 2


def _make_sig(secret: str, body: bytes) -> str:
    ts = str(int(time.time()))
    signed = f'{ts}.'.encode() + body
    v1 = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f't={ts},v1={v1}'


def _checkout_event(event_id: str, price_id: str = 'price_pro_monthly',
                    email: str = 'test@example.com',
                    customer_id: str = 'cus_test1') -> dict:
    return {
        'id': event_id,
        'type': 'checkout.session.completed',
        'data': {'object': {
            'customer': customer_id,
            'customer_details': {'email': email},
            'metadata': {'price_id': price_id},
        }},
    }


# ---------------------------------------------------------------------------
# 1. Duplicate webhook
# ---------------------------------------------------------------------------

class TestDuplicateWebhook(unittest.TestCase):

    def test_duplicate_event_ignored_in_memory(self) -> None:
        """Same event_id processed twice → second call returns None (in-memory dedup)."""
        handler = StripeHandler(webhook_secret=SECRET)
        event = _checkout_event('evt_duplicate_001')
        first  = handler.process_event(event)
        second = handler.process_event(event)
        self.assertIsNotNone(first)
        self.assertEqual(first['action'], 'activate')
        self.assertIsNone(second)

    def test_duplicate_event_ignored_persistent(self) -> None:
        """Same event_id processed twice → second call returns None (persistent dedup)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'subs.db')
            sub_mgr = SubscriptionManager(db_path)
            handler = StripeHandler(webhook_secret=SECRET, sub_manager=sub_mgr)
            event = _checkout_event('evt_duplicate_002')
            first  = handler.process_event(event)
            second = handler.process_event(event)
            self.assertIsNotNone(first)
            self.assertIsNone(second)

    def test_duplicate_handle_call_rejected_by_signature(self) -> None:
        """handle() deduplication via signature + event_id."""
        handler = StripeHandler(webhook_secret=SECRET)
        event = _checkout_event('evt_dup_handle_001')
        body = json.dumps(event).encode()
        sig  = _make_sig(SECRET, body)
        first  = handler.handle(body, sig)
        # Second call: same body/sig (replay within 5-minute window) → deduped
        second = handler.handle(body, sig)
        self.assertIsNotNone(first)
        self.assertIsNone(second)


# ---------------------------------------------------------------------------
# 2. Expired checkout
# ---------------------------------------------------------------------------

class TestExpiredCheckout(unittest.TestCase):

    def test_expired_session_event_ignored(self) -> None:
        """checkout.session.expired events produce no action."""
        handler = StripeHandler(webhook_secret=SECRET)
        event = {
            'id': 'evt_expired_001',
            'type': 'checkout.session.expired',
            'data': {'object': {'customer': 'cus_expired'}},
        }
        result = handler.process_event(event)
        self.assertIsNone(result)

    def test_unknown_event_type_ignored(self) -> None:
        """Unrecognised event types produce no action."""
        handler = StripeHandler(webhook_secret=SECRET)
        event = {'id': 'evt_unknown_001', 'type': 'invoice.payment_succeeded',
                 'data': {'object': {}}}
        self.assertIsNone(handler.process_event(event))


# ---------------------------------------------------------------------------
# 3. Invalid signature
# ---------------------------------------------------------------------------

class TestInvalidSignature(unittest.TestCase):

    def test_wrong_secret_rejected(self) -> None:
        handler = StripeHandler(webhook_secret=SECRET)
        event = _checkout_event('evt_badsig_001')
        body  = json.dumps(event).encode()
        bad_sig = _make_sig('wrong-secret-xyz', body)
        self.assertFalse(handler.verify_signature(body, bad_sig))

    def test_missing_sig_rejected(self) -> None:
        handler = StripeHandler(webhook_secret=SECRET)
        body = b'{"id":"evt_nosig","type":"checkout.session.completed"}'
        self.assertFalse(handler.verify_signature(body, ''))

    def test_replayed_old_timestamp_rejected(self) -> None:
        """Timestamp more than 5 minutes old should fail (replay prevention)."""
        handler = StripeHandler(webhook_secret=SECRET)
        body = b'{"id":"evt_replay","type":"checkout.session.completed"}'
        stale_ts = str(int(time.time()) - 400)
        v1 = hmac.new(
            SECRET.encode(),
            f'{stale_ts}.'.encode() + body,
            hashlib.sha256,
        ).hexdigest()
        stale_sig = f't={stale_ts},v1={v1}'
        self.assertFalse(handler.verify_signature(body, stale_sig))

    def test_handle_returns_none_on_invalid_sig(self) -> None:
        """handle() must return None (not process event) when signature is invalid."""
        handler = StripeHandler(webhook_secret=SECRET)
        event = _checkout_event('evt_invalid_handle_001')
        body  = json.dumps(event).encode()
        result = handler.handle(body, 'bad-signature-value')
        self.assertIsNone(result)

    def test_valid_signature_accepted(self) -> None:
        """Sanity: correctly signed payload is accepted."""
        handler = StripeHandler(webhook_secret=SECRET)
        event = _checkout_event('evt_goodsig_001')
        body  = json.dumps(event).encode()
        sig   = _make_sig(SECRET, body)
        self.assertTrue(handler.verify_signature(body, sig))


# ---------------------------------------------------------------------------
# 4. Revoked / invalid license
# ---------------------------------------------------------------------------

class TestRevokedLicense(unittest.TestCase):

    def setUp(self) -> None:
        self.validator = TierValidator(
            'e4806882a41883d35af2aa4ecfa20e89939b3cc53adf103e342f45e1e9661e4d'
        )

    def test_expired_license_invalid(self) -> None:
        key = self.validator.generate('pro', 'custA', PAST)
        result = self.validator.validate(key)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'expired')

    def test_tampered_license_invalid(self) -> None:
        key = self.validator.generate('pro', 'custB', FUTURE)
        tampered = key.replace('_pro_', '_enterprise_', 1)
        result = self.validator.validate(tampered)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'invalid_signature')

    def test_v1_format_rejected(self) -> None:
        import hmac as _hmac
        import hashlib as _hs
        secret = 'e4806882a41883d35af2aa4ecfa20e89939b3cc53adf103e342f45e1e9661e4d'
        message = f'zyrcon_pro_custC_{FUTURE}'.encode()
        sig = _hmac.new(secret.encode(), message, _hs.sha256).hexdigest()
        v1_key = f'zyrcon_pro_custC_{FUTURE}_{sig}'
        result = self.validator.validate(v1_key)
        self.assertFalse(result['valid'])
        self.assertEqual(result['error'], 'key_version_rejected')

    def test_completely_invalid_key(self) -> None:
        result = self.validator.validate('not-a-real-key')
        self.assertFalse(result['valid'])

    def test_valid_key_accepted(self) -> None:
        key = self.validator.generate('business', 'custD', FUTURE)
        result = self.validator.validate(key)
        self.assertTrue(result['valid'])
        self.assertEqual(result['tier'], 'business')


if __name__ == '__main__':
    unittest.main()
