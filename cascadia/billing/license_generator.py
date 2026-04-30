"""
license_generator.py — Cascadia OS v0.46
Generates and delivers license keys on successful Stripe payment.
Owns: key generation, email delivery via HANDSHAKE, key storage in VAULT.
Does not own: Stripe event processing (StripeHandler), tier validation (TierValidator).
"""
# MATURITY: PRODUCTION — Keys HMAC-signed, stored in VAULT, delivered via HANDSHAKE SMTP.
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.request
from typing import Any

from cascadia.shared.logger import get_logger

logger = get_logger('license_gen')

STRIPE_TIER_MAP = {
    'price_pro_monthly':       'pro',
    'price_pro_annual':        'pro',
    'price_pro_workspace':     'pro_workspace',
    'price_business_starter':  'business_starter',
    'price_business_growth':   'business_growth',
    'price_business_max':      'business_max',
    'price_enterprise':        'enterprise',
}


class LicenseGenerator:
    """Owns license key generation, VAULT storage, and email delivery."""

    def __init__(self, config_or_secret, handshake_port: int = None,
                 vault_port: int = None) -> None:
        if isinstance(config_or_secret, dict):
            self._secret = config_or_secret.get('license_secret', '')
            ports = {c['name']: c.get('port') for c in config_or_secret.get('components', [])}
            self._handshake_port = ports.get('handshake', 6203)
            self._vault_port = ports.get('vault', 5101)
        else:
            self._secret = config_or_secret
            self._handshake_port = handshake_port or 6203
            self._vault_port = vault_port or 5101

    def generate_key(self, tier: str, customer_id: str, days: int = 365) -> str:
        """Generate a signed license key. Same format as scripts/generate_license.py."""
        expiry = int(time.time()) + (days * 86400)
        message = f'zyrcon_{tier}_{customer_id}_{expiry}'.encode()
        sig = hmac.new(self._secret.encode(), message, hashlib.sha256).hexdigest()
        return f'zyrcon_{tier}_{customer_id}_{expiry}_{sig}'

    def store_in_vault(self, customer_id: str, license_key: str,
                       customer_email: str) -> bool:
        """Store issued license in VAULT under key licenses:{customer_id}"""
        payload = json.dumps({
            'key': f'licenses:{customer_id}',
            'value': json.dumps({
                'license_key': license_key,
                'customer_email': customer_email,
                'issued_at': time.time(),
            })
        }).encode()
        try:
            req = urllib.request.Request(
                f'http://127.0.0.1:{self._vault_port}/api/vault/set',
                data=payload, method='POST',
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=3)
            return True
        except Exception as e:
            logger.error('LicenseGen: VAULT store failed: %s', e)
            return False

    def deliver_by_email(self, customer_email: str, license_key: str,
                         tier: str) -> bool:
        """Send license key to customer via HANDSHAKE SMTP."""
        body = (
            f'Thank you for subscribing to Zyrcon {tier.title()}.\n\n'
            f'Your license key:\n\n  {license_key}\n\n'
            f'To activate:\n'
            f'  1. Open your config.json file\n'
            f'  2. Set "license_key": "{license_key}"\n'
            f'  3. Restart Cascadia OS (./stop.sh && ./start.sh)\n\n'
            f'Your dashboard will confirm the tier on next load.\n\n'
            f'— Zyrcon Labs\n   Houston, TX\n   support@zyrcon.ai'
        )
        payload = json.dumps({
            'to': customer_email,
            'subject': f'Your Zyrcon {tier.title()} License Key',
            'body': body,
        }).encode()
        try:
            req = urllib.request.Request(
                f'http://127.0.0.1:{self._handshake_port}/api/handshake/smtp/send',
                data=payload, method='POST',
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=5)
            logger.info('LicenseGen: key delivered to %s', customer_email)
            return True
        except Exception as e:
            logger.error('LicenseGen: email delivery failed: %s', e)
            return False

    def activate(self, customer_email: str, customer_id: str,
                 tier: str, days: int = 365) -> str:
        """Full activation flow: generate → store → deliver. Returns the key."""
        key = self.generate_key(tier, customer_id, days)
        self.store_in_vault(customer_id, key, customer_email)
        self.deliver_by_email(customer_email, key, tier)
        logger.info('LicenseGen: activated %s for %s', tier, customer_email)
        return key
