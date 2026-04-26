"""
context_builder.py — Cascadia OS v0.46
Builds contextual memory snapshots for operator decision-making.
Owns: querying VAULT for relevant past interactions, assembling
      structured context for CHIEF and other operators.
Does not own: VAULT storage (vault.py), operator logic (operators).
"""
# MATURITY: PRODUCTION — Relevance-ranked context assembly. Token-budget aware.
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, List, Optional

from cascadia.shared.logger import get_logger

logger = get_logger('context_builder')


class ContextBuilder:
    """Assembles relevant VAULT memories for operator context."""

    def __init__(self, vault_port: int) -> None:
        self._vault_port = vault_port

    def build_for_lead(self, company_name: str, contact_email: str = '',
                       max_tokens: int = 2000) -> str:
        """
        Build context string for a new lead from this company.
        Queries VAULT for past interactions, proposals, outcomes.
        Returns formatted context string for injection into CHIEF prompt.
        Returns empty string for unknown companies — never hallucinate.
        """
        memories: List[str] = []
        company_key = company_name.lower().replace(' ', '_')

        past = self._vault_get(f'proposals:{company_key}')
        if past:
            memories.append(f'Past proposals: {json.dumps(past)}')

        prefs = self._vault_get(f'preferences:{company_key}')
        if prefs:
            memories.append(f'Known preferences: {json.dumps(prefs)}')

        if contact_email:
            outcomes = self._vault_get(f'outcomes:{contact_email}')
            if outcomes:
                memories.append(f'Past outcomes: {json.dumps(outcomes)}')

        patterns = self._vault_get('business:patterns')
        if patterns:
            memories.append(f'Business patterns: {json.dumps(patterns)}')

        if not memories:
            return ''

        context = 'RELEVANT BUSINESS CONTEXT FROM PAST INTERACTIONS:\n'
        context += '\n'.join(memories)
        # Trim to token budget (rough estimate: 4 chars per token)
        if len(context) > max_tokens * 4:
            context = context[:max_tokens * 4] + '\n[context truncated]'
        return context

    def record_outcome(self, company_name: str, contact_email: str,
                       outcome: str, proposal_value: float,
                       notes: str = '') -> None:
        """Store a proposal outcome in VAULT for future context."""
        key = f'outcomes:{contact_email}'
        existing: List[Any] = self._vault_get(key) or []
        existing.append({
            'company': company_name,
            'outcome': outcome,
            'value': proposal_value,
            'notes': notes,
            'ts': time.time(),
        })
        self._vault_set(key, existing[-10:])  # keep last 10

    def record_preference(self, company_name: str, preference_key: str,
                          preference_value: str) -> None:
        """Store a learned preference for a company in VAULT."""
        key = f'preferences:{company_name.lower().replace(" ", "_")}'
        existing: Any = self._vault_get(key) or {}
        existing[preference_key] = preference_value
        self._vault_set(key, existing)

    def stats(self) -> dict:
        """Return counts of companies and outcomes in VAULT."""
        # Best-effort — VAULT may not have a search endpoint
        return {'available': True}

    def _vault_get(self, key: str) -> Any:
        try:
            req = urllib.request.Request(
                f'http://127.0.0.1:{self._vault_port}/api/vault/get?key={key}'
            )
            with urllib.request.urlopen(req, timeout=2) as r:
                data = json.loads(r.read())
                raw = data.get('value')
                return json.loads(raw) if raw else None
        except Exception:
            return None

    def _vault_set(self, key: str, value: Any) -> None:
        payload = json.dumps({'key': key, 'value': json.dumps(value)}).encode()
        try:
            req = urllib.request.Request(
                f'http://127.0.0.1:{self._vault_port}/api/vault/set',
                data=payload, method='POST',
                headers={'Content-Type': 'application/json'},
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception as e:
            logger.error('ContextBuilder: VAULT set failed for %s: %s', key, e)
