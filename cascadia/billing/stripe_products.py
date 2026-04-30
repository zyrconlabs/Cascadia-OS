"""
stripe_products.py — Cascadia OS
Canonical Stripe product catalog.
Owns: product definitions, price-to-tier mapping, pricing display.
Does not own: key generation, webhook handling, or Stripe API calls.

Stripe price IDs are configured in stripe.config.json (gitignored).
Use this module for tier resolution and display — not for secrets.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PRODUCTS: List[Dict[str, Any]] = [
    {
        'name':          'Lite',
        'tier':          'lite',
        'price_monthly': 0,
        'price_annual':  0,
        'contact':       False,
        'description':   'Free forever — up to 2 operators, local only',
    },
    {
        'name':          'Pro',
        'tier':          'pro',
        'price_monthly': 1900,   # cents
        'price_annual':  19000,  # cents/year (flat)
        'contact':       False,
        'description':   '$19/mo — up to 6 operators, NATS, vault, HANDSHAKE',
    },
    {
        'name':          'Business',
        'tier':          'business',
        'price_monthly': 49900,   # cents
        'price_annual':  499000,  # cents/year (flat)
        'contact':       False,
        'description':   '$499/mo — up to 12 operators, fleet, audit log',
    },
    {
        'name':          'Enterprise',
        'tier':          'enterprise',
        'price_monthly': None,
        'price_annual':  None,
        'contact':       True,
        'description':   'Contact us — unlimited operators, SLA, SSO, custom deployment',
    },
]

# Maps Stripe price IDs → tier. Actual price IDs come from stripe.config.json at runtime.
# These names are canonical logical identifiers used in code; real IDs are substituted by
# the stripe_webhook module at load time.
STRIPE_TIER_MAP: Dict[str, str] = {
    'price_pro_monthly':      'pro',
    'price_pro_annual':       'pro',
    'price_business_monthly': 'business',
    'price_business_annual':  'business',
    'price_enterprise':       'enterprise',
}

_TIER_INDEX: Dict[str, Dict[str, Any]] = {p['tier']: p for p in PRODUCTS}


def get_product(tier: str) -> Optional[Dict[str, Any]]:
    return _TIER_INDEX.get(tier)


def price_display(tier: str) -> str:
    p = get_product(tier)
    if p is None:
        return 'unknown'
    if p['contact']:
        return 'Contact us'
    monthly = p['price_monthly']
    if monthly == 0:
        return 'Free'
    return f'${monthly // 100}/mo'
