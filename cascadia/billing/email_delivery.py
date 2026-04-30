"""
email_delivery.py — Cascadia OS
Transactional email delivery for billing events.
Owns: email templates, SMTP delivery for billing.
Does not own: SMTP config (uses HANDSHAKE config), license generation (license_generator),
              subscription state (subscription_manager).
"""
from __future__ import annotations
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from cascadia.shared.logger import get_logger

logger = get_logger('email_delivery')

SUPPORT_EMAIL = 'hello@zyrcon.ai'
FROM_NAME     = 'Zyrcon Labs'


class EmailDelivery:
    def __init__(self, config: dict) -> None:
        smtp_cfg = config.get('smtp', {})
        self._host     = smtp_cfg.get('host', '')
        self._port     = smtp_cfg.get('port', 587)
        self._user     = smtp_cfg.get('username', '')
        self._password = smtp_cfg.get('password', '')
        self._enabled  = bool(self._host and self._user)

    def _send(self, to_email: str, subject: str, body: str) -> bool:
        if not self._enabled:
            logger.info('EmailDelivery: SMTP not configured. Would send to %s: %s', to_email, subject)
            return True  # Soft fail — log and continue
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = f'{FROM_NAME} <{self._user}>'
            msg['To']      = to_email
            msg.attach(MIMEText(body, 'plain'))
            with smtplib.SMTP(self._host, self._port) as server:
                server.ehlo()
                server.starttls()
                server.login(self._user, self._password)
                server.sendmail(self._user, to_email, msg.as_string())
            logger.info('EmailDelivery: sent to %s', to_email)
            return True
        except Exception as e:
            logger.error('EmailDelivery: failed %s: %s', to_email, e)
            return False

    def send_welcome(self, to_email: str, tier: str, license_key: str) -> bool:
        tier_display = tier.replace('_', ' ').title()
        subject = f'Your Zyrcon {tier_display} license is ready'
        body = f"""Welcome to Zyrcon {tier_display}.

Your license key:

  {license_key}

Getting started:

  1. Install Cascadia OS:
     https://github.com/zyrconlabs/cascadia-os

  2. Quick start guide:
     https://github.com/zyrconlabs/cascadia-os/blob/main/QUICKSTART.md

  3. During install, enter your license key
     when prompted. Your {tier_display} features
     activate immediately.

  4. Open PRISM dashboard (port 6300) to
     confirm your tier is active.

Questions? Reply to this email or contact:
  {SUPPORT_EMAIL}

Zyrcon Labs · Houston, TX · zyrcon.ai
"""
        return self._send(to_email, subject, body)

    def send_payment_failed(self, to_email: str, tier: str) -> bool:
        subject = 'Action required — Zyrcon payment failed'
        body = f"""Your Zyrcon payment did not go through.

Your {tier.replace('_', ' ').title()} subscription
remains active for now, but please update your
payment method to avoid interruption.

Update your payment method:
  Log into PRISM → Settings → Billing →
  Manage Subscription

Or contact us:
  {SUPPORT_EMAIL}

Zyrcon Labs · Houston, TX · zyrcon.ai
"""
        return self._send(to_email, subject, body)

    def send_cancellation(self, to_email: str, tier: str) -> bool:
        subject = 'Your Zyrcon subscription has been cancelled'
        body = f"""Your Zyrcon {tier.replace('_', ' ').title()} subscription has been cancelled.

Your access continues until the end of your
current billing period.

After that, your account moves to Lite (free).
Your data, workflows, and run history are
preserved and exportable at any time.

Changed your mind? Resubscribe any time:
  https://zyrcon.ai/pricing

Questions:
  {SUPPORT_EMAIL}

Zyrcon Labs · Houston, TX · zyrcon.ai
"""
        return self._send(to_email, subject, body)

    def send_hardware_confirmation(self, to_email: str, product: str, amount: float) -> bool:
        subject = f'Zyrcon {product} order confirmed'
        body = f"""Thank you for ordering the {product}.

Order total: ${amount:.2f}

We will contact you within 2 business days to
confirm shipping details and estimated delivery.

First units ship Q3 2026. You are in the founding
hardware cohort — setup fee waived.

Questions:
  {SUPPORT_EMAIL}

Zyrcon Labs · Houston, TX · zyrcon.ai
"""
        return self._send(to_email, subject, body)

    def send_waitlist_confirmation(self, to_email: str, product: str = 'Zyrcon') -> bool:
        subject = f"You're on the {product} waitlist"
        body = f"""You are on the {product} waitlist.

We will contact you when access opens.

In the meantime:
  Try Cascadia OS free:
  https://github.com/zyrconlabs/cascadia-os

  Read the docs:
  https://github.com/zyrconlabs/cascadia-os/blob/main/QUICKSTART.md

Questions:
  {SUPPORT_EMAIL}

Zyrcon Labs · Houston, TX · zyrcon.ai
"""
        return self._send(to_email, subject, body)
