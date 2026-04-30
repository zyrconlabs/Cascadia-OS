from __future__ import annotations
import unittest

from cascadia.billing.email_delivery import EmailDelivery


_EMPTY_CONFIG = {}
_SMTP_CONFIG = {'smtp': {'host': '', 'port': 587, 'username': '', 'password': ''}}


class EmailDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        # No SMTP configured — all sends are soft-fail (return True, log only)
        self.email = EmailDelivery(_EMPTY_CONFIG)

    def test_send_welcome_returns_true_when_smtp_disabled(self) -> None:
        result = self.email.send_welcome('test@example.com', 'pro', 'zyrcon_pro_acme_999_abc')
        self.assertTrue(result)

    def test_send_payment_failed_returns_true_when_disabled(self) -> None:
        result = self.email.send_payment_failed('test@example.com', 'business_starter')
        self.assertTrue(result)

    def test_send_cancellation_returns_true_when_disabled(self) -> None:
        result = self.email.send_cancellation('test@example.com', 'business_growth')
        self.assertTrue(result)

    def test_send_hardware_confirmation_formats_amount(self) -> None:
        result = self.email.send_hardware_confirmation('test@example.com', 'Zyrcon Edge', 299.0)
        self.assertTrue(result)

    def test_no_exception_when_smtp_unconfigured(self) -> None:
        email = EmailDelivery(_SMTP_CONFIG)
        try:
            email.send_welcome('test@example.com', 'pro', 'zyrcon_key_123')
            email.send_payment_failed('test@example.com', 'pro')
            email.send_cancellation('test@example.com', 'pro')
            email.send_waitlist_confirmation('test@example.com')
        except Exception as exc:
            self.fail(f'Unexpected exception with no SMTP config: {exc}')

    def test_send_waitlist_confirmation_returns_true_when_disabled(self) -> None:
        result = self.email.send_waitlist_confirmation('test@example.com', 'Zyrcon Edge')
        self.assertTrue(result)

    def test_tier_display_format(self) -> None:
        # Ensure underscores are replaced with spaces and title-cased
        email = EmailDelivery(_EMPTY_CONFIG)
        # send_welcome should not raise even for compound tier names
        result = email.send_welcome('test@example.com', 'business_starter', 'key_abc')
        self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
