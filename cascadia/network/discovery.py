"""
discovery.py — Cascadia OS Task 6
Local network discovery (mDNS) and device pairing for iOS companion app.
mDNS registration via zeroconf (optional dep — silently skips if not installed).
Pairing: 6-digit code, 5-minute TTL, single-use.
"""
from __future__ import annotations

import secrets
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_lan_ip() -> str:
    """Determine the real outbound LAN IP for this host.

    Uses a UDP socket 'connect' (no packets are actually sent) to learn which
    local interface would route to the internet. This avoids the macOS pitfall
    where socket.gethostbyname(gethostname()) returns 127.0.0.1 due to
    /etc/hosts behavior, which would make mDNS advertise an unreachable
    loopback address to LAN clients.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


# ── mDNS ─────────────────────────────────────────────────────────────────────

class MdnsRegistrar:
    """
    Registers this Cascadia OS node as _cascadia._tcp.local. via mDNS.
    Requires the 'zeroconf' package — skips silently if not installed.
    """

    def __init__(self, port: int = 6300, instance_name: str = 'Cascadia OS',
                 role: Optional[str] = None) -> None:
        self.port = port
        self.instance_name = instance_name
        self.role = role
        self._zeroconf: Any = None
        self._service_info: Any = None
        self._registered = False

    def register(self) -> bool:
        """Register mDNS service. Returns True on success, False if zeroconf unavailable."""
        try:
            from zeroconf import Zeroconf, ServiceInfo
            hostname = socket.gethostname().split('.')[0]
            local_ip = _get_lan_ip()
            self._zeroconf = Zeroconf()
            self._service_info = ServiceInfo(
                '_cascadia._tcp.local.',
                f'{self.instance_name}._cascadia._tcp.local.',
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                properties={
                    b'version': b'0.44',
                    b'api': b'/api/prism/overview',
                    b'role': (self.role or '').encode(),
                    b'host': hostname.encode(),
                },
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
_WINDOW_TTL_SECONDS = 300  # a pairing WINDOW auto-closes 5 min after it is opened


class PairingManager:
    """
    Issues single-use 6-digit pairing codes for iOS companion app authentication.
    Codes expire after 5 minutes and are consumed on first successful use.
    """

    def __init__(self) -> None:
        self._codes: Dict[str, Dict[str, Any]] = {}  # code → {created_at, used}
        self._lock = threading.Lock()
        self._window_expires_at = 0.0  # 0 = CLOSED (default); else wall-clock deadline

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

    # --- pairing window (default CLOSED) -----------------------------------
    def open_window(self) -> float:
        """Open the pairing window; returns its expiry epoch. Default state is CLOSED."""
        with self._lock:
            self._window_expires_at = time.time() + _WINDOW_TTL_SECONDS
            return self._window_expires_at

    def close_window(self) -> None:
        with self._lock:
            self._window_expires_at = 0.0

    def window_open(self) -> bool:
        """True ONLY while an unexpired window is open. Any other/unknown state → closed."""
        with self._lock:
            return self._window_expires_at > time.time()

    def window_expiry(self) -> float:
        """Expiry epoch while open, else 0.0 (never leak while closed)."""
        with self._lock:
            return self._window_expires_at if self._window_expires_at > time.time() else 0.0


# Module-level singletons — used by FLINT and PRISM
_mdns = MdnsRegistrar()
_pairing = PairingManager()


def node_display_name(role: Optional[str] = None) -> str:
    """Human-readable, per-node name for mDNS/pairing, e.g. 'Zyrcon (air)'."""
    import socket
    host = socket.gethostname().split('.')[0]
    return f'Zyrcon ({role or host})'


def start_discovery(port: int = 6300, name: Optional[str] = None,
                    role: Optional[str] = None) -> bool:
    """Start mDNS discovery. Returns True if zeroconf registered successfully."""
    global _mdns
    display = name or node_display_name(role)
    _mdns = MdnsRegistrar(port=port, instance_name=display, role=role)
    return _mdns.register()


def stop_discovery() -> None:
    _mdns.unregister()


def generate_pairing_code() -> str:
    return _pairing.generate_code()


def validate_pairing_code(code: str) -> bool:
    return _pairing.validate_code(code)


def open_pairing_window() -> float:
    return _pairing.open_window()


def close_pairing_window() -> None:
    _pairing.close_window()


def pairing_window_open() -> bool:
    return _pairing.window_open()


def pairing_status() -> Dict[str, Any]:
    return {
        'mdns_registered': _mdns._registered,
        'pending_codes': _pairing.pending_count(),
        'ttl_seconds': _PAIR_TTL_SECONDS,
        'pairing_open': _pairing.window_open(),
        'window_expires_at': _pairing.window_expiry(),
        'generated_at': _now_utc(),
    }
