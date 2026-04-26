"""
depot_client.py — Cascadia OS v0.46
Client for the DEPOT operator marketplace API.
Owns: fetching operator listings, categories, and download URLs.
Does not own: payment processing (Stripe), installation (CREW), display (PRISM).
"""
# MATURITY: FUNCTIONAL — Read-only browse. Purchase flow in Sprint v3.
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Optional

from cascadia.shared.logger import get_logger

logger = get_logger('depot')

DEPOT_API = 'https://depot.zyrcon.ai/api/v1'

# Fallback catalogue shown when DEPOT API is unreachable
_FALLBACK_CATALOGUE: List[Dict[str, Any]] = [
    {
        'id': 'social-pro',
        'name': 'Social Pro',
        'category': 'marketing',
        'description': 'LinkedIn + X scheduling with AI quality scoring',
        'price_usd': 29,
        'tier_required': 'pro',
        'stripe_payment_link': '',
    },
    {
        'id': 'crm-sync',
        'name': 'CRM Sync',
        'category': 'sales',
        'description': 'Two-way sync with HubSpot, Pipedrive, and Salesforce',
        'price_usd': 0,
        'tier_required': 'pro',
        'stripe_payment_link': '',
    },
    {
        'id': 'invoice-ai',
        'name': 'Invoice AI',
        'category': 'operations',
        'description': 'Auto-generate and send invoices from completed job records',
        'price_usd': 19,
        'tier_required': 'business',
        'stripe_payment_link': '',
    },
]


class DEPOTClient:
    """Owns DEPOT marketplace browsing. Does not own payment or installation."""

    def list_operators(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        url = f'{DEPOT_API}/operators'
        if category:
            url += f'?category={category}'
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return json.loads(r.read()).get('operators', [])
        except Exception as e:
            logger.warning('DEPOT: API unreachable (%s), using fallback catalogue', e)
            if category:
                return [op for op in _FALLBACK_CATALOGUE if op.get('category') == category]
            return list(_FALLBACK_CATALOGUE)

    def get_operator(self, operator_id: str) -> Optional[Dict[str, Any]]:
        try:
            url = f'{DEPOT_API}/operators/{operator_id}'
            with urllib.request.urlopen(url, timeout=5) as r:
                return json.loads(r.read())
        except Exception:
            return next((op for op in _FALLBACK_CATALOGUE if op['id'] == operator_id), None)
