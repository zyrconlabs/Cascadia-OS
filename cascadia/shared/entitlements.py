"""entitlements.py - Cascadia OS v0.47
Canonical single source of truth for capability strings and risk levels.
Owns: capability registry, risk classification, helper functions.
Does not own: enforcement (VANTAGE), compliance rules (SENTINEL), routing (BEACON).
"""
# MATURITY: PRODUCTION — Single source of truth for capability vocabulary.
from __future__ import annotations

import re
from typing import Dict, List, Optional

RISK_LEVELS: Dict[str, dict] = {
    "critical": {
        "description": "Irreversible system-level actions",
        "requires_approval": True,
        "sentinel_check": True,
        "examples": ["shell.exec", "vault.write", "system.destroy"],
    },
    "high": {
        "description": "Destructive or financial actions",
        "requires_approval": True,
        "sentinel_check": True,
        "examples": [
            "billing.write", "payment.create", "invoice.create",
            "email.delete", "file.delete", "crm.delete",
        ],
    },
    "medium": {
        "description": "Reversible write actions",
        "requires_approval": False,
        "sentinel_check": False,
        "examples": ["email.send", "file.write", "calendar.write", "crm.write", "browser.submit"],
    },
    "low": {
        "description": "Read-only or non-destructive actions",
        "requires_approval": False,
        "sentinel_check": False,
        "examples": ["file.read", "calendar.read", "vault.read", "crm.read"],
    },
}

CAPABILITY_REGISTRY: Dict[str, str] = {
    # Email
    "email.send":           "medium",
    "email.read":           "low",
    "email.delete":         "high",
    "email.draft":          "low",
    "email.search":         "low",
    # CRM
    "crm.read":             "low",
    "crm.write":            "medium",
    "crm.delete":           "high",
    "crm.search":           "low",
    "crm.export":           "low",
    # File / Storage
    "file.read":            "low",
    "file.write":           "medium",
    "file.delete":          "high",
    "file.overwrite":       "medium",
    "file.share":           "medium",
    "file.search":          "low",
    # Calendar
    "calendar.read":        "low",
    "calendar.write":       "medium",
    "calendar.delete":      "high",
    # Billing / Payments
    "billing.read":         "low",
    "billing.write":        "high",
    "payment.create":       "high",
    "payment.read":         "low",
    "invoice.create":       "high",
    "invoice.read":         "low",
    # Vault / Secrets
    "vault.read":           "low",
    "vault.write":          "critical",
    "vault.delete":         "critical",
    # System
    "shell.exec":           "critical",
    "system.restart":       "critical",
    "system.destroy":       "critical",
    "browser.submit":       "medium",
    "browser.read":         "low",
    # Messaging / Comms
    "message.send":         "medium",
    "message.read":         "low",
    "message.delete":       "high",
    "notification.send":    "medium",
    # Data / Analytics
    "data.read":            "low",
    "data.write":           "medium",
    "data.delete":          "high",
    "data.export":          "low",
    "report.read":          "low",
    "report.create":        "medium",
    # HR / Identity
    "identity.read":        "low",
    "identity.write":       "high",
    "identity.delete":      "critical",
    "hr.read":              "low",
    "hr.write":             "high",
    # Commerce / Inventory
    "inventory.read":       "low",
    "inventory.write":      "medium",
    "order.read":           "low",
    "order.write":          "medium",
    "order.cancel":         "high",
    # Connector
    "connector.read":       "low",
    "connector.write":      "medium",
    "connector.delete":     "high",
    "connector.sync":       "medium",
    "connector.auth":       "high",
    # Gateway
    "gateway.route":        "low",
    "gateway.enforce":      "medium",
    "gateway.audit":        "low",
}

_CRITICAL_VERBS = frozenset(("exec", "run", "execute", "destroy", "purge"))
_HIGH_VERBS = frozenset(("delete", "remove", "drop", "cancel", "revoke"))
_MEDIUM_VERBS = frozenset((
    "write", "create", "update", "send", "post", "publish",
    "upload", "submit", "overwrite", "add",
))
_LOW_VERBS = frozenset(("read", "get", "list", "search", "fetch", "query", "view", "export", "check", "inspect"))


def get_risk_level(capability: str) -> str:
    """Canonical risk level for a capability string.

    Resolution order:
    1. Exact match in CAPABILITY_REGISTRY
    2. Prefix ancestor match (e.g. email.send_batch → email.send → medium)
    3. Verb-root fallback (split on _ to handle compound verbs)
    4. Safe default: medium
    """
    if capability in CAPABILITY_REGISTRY:
        return CAPABILITY_REGISTRY[capability]

    # Prefix ancestor loop — finds longest registered prefix
    for key, level in CAPABILITY_REGISTRY.items():
        if capability.startswith(key.replace(".*", "")):
            return level

    # Verb-root fallback — handles compound verbs like delete_record, create_contact
    verb = capability.rsplit(".", 1)[-1].lower() if "." in capability else capability.lower()
    verb_root = verb.split("_")[0]
    if verb_root in _CRITICAL_VERBS:
        return "critical"
    if verb_root in _HIGH_VERBS:
        return "high"
    if verb_root in _MEDIUM_VERBS:
        return "medium"
    if verb_root in _LOW_VERBS:
        return "low"

    return "medium"  # safe default — unknown capability treated as write-tier


def requires_approval(capability: str) -> bool:
    """True if capability requires human approval before execution."""
    return RISK_LEVELS[get_risk_level(capability)]["requires_approval"]


def requires_sentinel(capability: str) -> bool:
    """True if capability requires a SENTINEL check before execution."""
    return RISK_LEVELS[get_risk_level(capability)]["sentinel_check"]


def validate_capability(capability: str) -> bool:
    """True if capability follows dot-notation format (lowercase, allows wildcard segments)."""
    return bool(re.match(r'^[a-z][a-z0-9_]*(\.[a-z*][a-z0-9_*]*)+$', capability))


def list_capabilities(risk_level: Optional[str] = None) -> List[str]:
    """List all registered capabilities, optionally filtered by risk level."""
    if risk_level:
        return [k for k, v in CAPABILITY_REGISTRY.items() if v == risk_level]
    return list(CAPABILITY_REGISTRY.keys())
