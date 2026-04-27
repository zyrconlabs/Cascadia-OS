"""
cascadia/routing/beacon.py — Cascadia OS Sprint 3
Lightweight routing registry for Sprint 3 inter-service calls.
Owns: mapping service names to their ports and health paths,
      building target URLs for inter-component HTTP calls.
Does not own: capability checking (orchestrator/beacon.py),
              execution, approval decisions, or service startup.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


# Default port assignments for Cascadia OS core services.
# Populated from config at startup; these are fallback values only.
_DEFAULT_PORTS: Dict[str, int] = {
    'prism':     6300,
    'crew':      5100,
    'sentinel':  5102,
    'beacon':    6200,
    'stitch':    6201,
    'vault':     6202,
    'almanac':   6205,
    'handshake': 6203,
    'bell':      6204,
    'flint':     5101,
}


class RoutingBeacon:
    """
    Sprint 3 routing registry — resolves service names to base URLs.
    Intended as a lightweight companion to orchestrator/beacon.py for
    services that only need URL resolution, not full capability routing.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self._ports: Dict[str, int] = dict(_DEFAULT_PORTS)
        if config:
            for comp in config.get('components', []):
                name = comp.get('name', '')
                port = comp.get('port')
                if name and port:
                    self._ports[name] = int(port)
            flint_port = config.get('flint', {}).get('status_port')
            if flint_port:
                self._ports['flint'] = int(flint_port)

    def base_url(self, service: str) -> Optional[str]:
        """Return http://127.0.0.1:<port> for a named service, or None if unknown."""
        port = self._ports.get(service)
        return f'http://127.0.0.1:{port}' if port else None

    def port(self, service: str) -> Optional[int]:
        """Return the port number for a named service."""
        return self._ports.get(service)

    def all_services(self) -> Dict[str, int]:
        """Return the full name→port mapping."""
        return dict(self._ports)
