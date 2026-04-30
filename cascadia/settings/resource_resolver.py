"""
cascadia/settings/resource_resolver.py
Owns: detecting installed connectors/operators, resolving availability,
      and suggesting fallbacks when a resource is missing.
Does not own: installation, VAULT, settings persistence.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default connectors path relative to package root
_CONNECTORS_DIR = Path(__file__).parent.parent / "connectors"
_OPERATORS_DIR  = Path(__file__).parent.parent / "operators"

# DEPOT API base (only queried when DEPOT service is reachable)
_DEPOT_PORT = 6208

# ── Fallback rules ────────────────────────────────────────────────────────────
# Maps missing resource_id → suggested fallback resource_id

_FALLBACK_MAP: Dict[str, str] = {
    # CRM missing → Google Sheets
    "hubspot":        "google_sheets",
    "salesforce":     "google_sheets",
    "zoho-crm":       "google_sheets",
    "pipedrive":      "google_sheets",
    # Field service missing → Google Sheets + Gmail
    "jobber":         "google_sheets",
    "servicetitan":   "google_sheets",
    # Accounting missing → Google Sheets (CSV mode)
    "quickbooks":     "google_sheets",
    "xero":           "google_sheets",
    "netsuite":       "google_sheets",
    # SMS missing → Email
    "twilio":         "gmail",
    "sms":            "gmail",
    # Website form missing → Webhook
    "typeform":       "webhook",
    "jotform":        "webhook",
    # Google Sheets fallback → Gmail as a last resort
    "google_sheets":  "gmail",
}

# Resources that are always available (built-in connectors)
_BUILTIN_IDS = frozenset({
    "gmail", "google", "google_sheets", "google_drive", "google_docs",
    "slack", "webhook", "email", "sms",
    "discord", "telegram", "whatsapp", "teams", "outlook",
    "zapier", "rest", "scheduler", "calendar", "google_calendar",
    "approval",
})


def get_installed_connectors(connectors_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return list of installed connector manifest dicts from the connectors directory."""
    base = connectors_dir or _CONNECTORS_DIR
    results: List[Dict[str, Any]] = []
    if not base.is_dir():
        return results
    for child in sorted(base.iterdir()):
        manifest_path = child / "manifest.json"
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                results.append(data)
            except Exception:
                pass
    return results


def get_depot_available_connectors() -> List[Dict[str, Any]]:
    """Query DEPOT API for available connectors. Returns [] if DEPOT is offline."""
    try:
        import urllib.request
        with urllib.request.urlopen(
            f"http://127.0.0.1:{_DEPOT_PORT}/v1/operators?category=integration",
            timeout=1.5,
        ) as r:
            data = json.loads(r.read().decode())
            return [
                op for op in data.get("operators", [])
                if op.get("type") == "connector"
            ]
    except Exception:
        return []


def resolve_resource(
    resource_id: str,
    installed: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Resolve the status of a resource by ID.
    Returns a ResourceStatus dict:
      {id, name, status, install_url, fallback_for}
    """
    installed_ids = _installed_id_set(installed)

    # Installed in local connectors dir
    if resource_id in installed_ids or resource_id in _BUILTIN_IDS:
        return _status(resource_id, "installed")

    # Available on DEPOT
    depot = get_depot_available_connectors()
    depot_ids = {op.get("id", "") for op in depot}
    if resource_id in depot_ids:
        return _status(resource_id, "depot_available",
                       install_url=f"http://localhost:6208/#/store/{resource_id}")

    # Suggest fallback
    fallback = _FALLBACK_MAP.get(resource_id)
    if fallback:
        return _status(resource_id, "fallback",
                       fallback_for=resource_id,
                       install_url=None,
                       name=resource_id,
                       _fallback_id=fallback)

    return _status(resource_id, "unavailable")


def suggest_fallback(
    resource_id: str,
    installed: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Return a ResourceStatus for the fallback connector, or None if no fallback exists."""
    fallback_id = _FALLBACK_MAP.get(resource_id)
    if not fallback_id:
        return None
    return resolve_resource(fallback_id, installed)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _installed_id_set(installed: Optional[List[Dict[str, Any]]]) -> frozenset:
    if installed is None:
        installed = get_installed_connectors()
    ids: set = set()
    for m in installed:
        ids.add(m.get("id", ""))
        # Some manifests use 'operator_id' or directory name
        ids.add(m.get("operator_id", ""))
    return frozenset(ids)


def _status(
    resource_id: str,
    status: str,
    install_url: Optional[str] = None,
    fallback_for: Optional[str] = None,
    name: Optional[str] = None,
    _fallback_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": _fallback_id if _fallback_id else resource_id,
        "name": name or resource_id,
        "status": status,
        "install_url": install_url,
        "fallback_for": fallback_for,
    }
