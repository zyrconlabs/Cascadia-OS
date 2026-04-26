"""
discovery.py — Cascadia OS Task 6
Local network discovery (mDNS) and device pairing for iOS companion app.
mDNS registration via zeroconf (optional dep — silently skips if not installed).
Pairing: 6-digit code, 5-minute TTL, single-use.
"""
from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── mDNS ─────────────────────────────────────────────────────────────────────

class MdnsRegistrar:
    """
    Registers this Cascadia OS node as _cascadia._tcp.local. via mDNS.
    Requires the 'zeroconf' package — skips silently if not installed.
    """

    def __init__(self, port: int = 6300, instance_name: str = 'Cascadia OS') -> None:
        self.port = port
        self.instance_name = instance_name
        self._zeroconf: Any = None
        self._service_info: Any = None
        self._registered = False

    def register(self) -> bool:
        """Register mDNS service. Returns True on success, False if zeroconf unavailable."""
        try:
            import socket
            from zeroconf import Zeroconf, ServiceInfo
            local_ip = socket.gethostbyname(socket.gethostname())
            self._zeroconf = Zeroconf()
            self._service_info = ServiceInfo(
                '_cascadia._tcp.local.',
                f'{self.instance_name}._cascadia._tcp.local.',
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                properties={b'version': b'0.44', b'api': b'/api/prism/overview'},
            )
            self._zeroconf.register_service(self._service_info)
            self._registered = True
            return True
        except ImportError:
            return False
        except Exception:
            return False

    def unregister(self) -> None:
        if self._zeroconf and self._registered:
            try:
                self._zeroconf.unregister_service(self._service_info)
                self._zeroconf.close()
            except Exception:
                pass
            self._registered = False


# ── Pairing codes ─────────────────────────────────────────────────────────────

_PAIR_TTL_SECONDS = 300  # 5 minutes


class PairingManager:
    """
    Issues single-use 6-digit pairing codes for iOS companion app authentication.
    Codes expire after 5 minutes and are consumed on first successful use.
    """

    def __init__(self) -> None:
        self._codes: Dict[str, Dict[str, Any]] = {}  # code → {created_at, used}
        self._lock = threading.Lock()

    def generate_code(self) -> str:
        """Generate a fresh 6-digit code. Prunes expired codes as a side effect."""
        self._prune()
        # secrets.randbelow(900000) gives 0-899999 + 100000 = 100000-999999
        code = str(secrets.randbelow(900000) + 100000)
        with self._lock:
            self._codes[code] = {
                'created_at': time.time(),
                'used': False,
            }
        return code

    def validate_code(self, code: str) -> bool:
        """
        Validate a pairing code. Returns True only if code exists, unexpired, and unused.
        Marks code as used on success (single-use).
        """
        with self._lock:
            entry = self._codes.get(code)
            if entry is None or entry['used']:
                return False
            if time.time() - entry['created_at'] > _PAIR_TTL_SECONDS:
                del self._codes[code]
                return False
            entry['used'] = True
            return True

    def _prune(self) -> None:
        now = time.time()
        with self._lock:
            expired = [c for c, e in self._codes.items()
                       if e['used'] or now - e['created_at'] > _PAIR_TTL_SECONDS]
            for c in expired:
                del self._codes[c]

    def pending_count(self) -> int:
        self._prune()
        with self._lock:
            return len(self._codes)


# Module-level singletons — used by FLINT and PRISM
_mdns = MdnsRegistrar()
_pairing = PairingManager()


def start_discovery(port: int = 6300, name: str = 'Cascadia OS') -> bool:
    """Start mDNS discovery. Returns True if zeroconf registered successfully."""
    global _mdns
    _mdns = MdnsRegistrar(port=port, instance_name=name)
    return _mdns.register()


def stop_discovery() -> None:
    _mdns.unregister()


def generate_pairing_code() -> str:
    return _pairing.generate_code()


def validate_pairing_code(code: str) -> bool:
    return _pairing.validate_code(code)


def pairing_status() -> Dict[str, Any]:
    return {
        'mdns_registered': _mdns._registered,
        'pending_codes': _pairing.pending_count(),
        'ttl_seconds': _PAIR_TTL_SECONDS,
        'generated_at': _now_utc(),
    }
