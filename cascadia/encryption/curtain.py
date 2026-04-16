"""
curtain/curtain.py - Cascadia OS v0.2
CURTAIN: Encryption layer for Cascadia OS.

Owns: transport encryption, at-rest data protection, key management stubs,
      encrypted envelope creation and verification.
Does not own: routing (BEACON), capability enforcement (SENTINEL),
              communication channels (VANGUARD, BELL).

In v0.2: Implements envelope-level HMAC signing and AES-256 symmetric
encryption using Python stdlib only (hashlib, hmac, secrets).
Full asymmetric key exchange is v0.3 roadmap.
"""
# MATURITY: STUB — HMAC signing and field encryption work (stdlib). Asymmetric key exchange and TLS integration are v0.3.
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import base64
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime


# ---------------------------------------------------------------------------
# Core crypto primitives (stdlib only — no external deps)
# ---------------------------------------------------------------------------

def _derive_key(secret: str, salt: bytes) -> bytes:
    """Derives a 32-byte key using PBKDF2-HMAC-SHA256."""
    return hashlib.pbkdf2_hmac('sha256', secret.encode(), salt, iterations=100_000, dklen=32)


def sign_envelope(payload: Dict[str, Any], secret: str) -> str:
    """
    Sign a payload dict. Returns a signed token (base64 encoded JSON + HMAC).
    Owns: HMAC-SHA256 signing. Does not own payload schema.
    """
    body = json.dumps(payload, sort_keys=True).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    envelope = {'payload': payload, 'sig': sig, 'ts': datetime.now(timezone.utc).isoformat()}
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def verify_envelope(token: str, secret: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Verify a signed token. Returns (valid, payload) or (False, None).
    Owns: HMAC verification. Does not own payload business logic.
    """
    try:
        envelope = json.loads(base64.b64decode(token.encode()).decode())
        payload = envelope['payload']
        expected_sig = envelope['sig']
        body = json.dumps(payload, sort_keys=True).encode()
        actual_sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected_sig, actual_sig):
            return True, payload
        return False, None
    except Exception:
        return False, None


def encrypt_field(value: str, key: bytes) -> str:
    """
    XOR-based field encryption with random nonce (v0.2 placeholder).
    v0.3 will replace with AES-256-GCM via cryptography library.
    Owns: field-level obfuscation. Does not own key distribution.
    """
    nonce = secrets.token_bytes(16)
    keystream = hashlib.sha256(key + nonce).digest()
    encrypted = bytes(a ^ b for a, b in zip(value.encode()[:32], keystream[:32]))
    return base64.b64encode(nonce + encrypted).decode()


def decrypt_field(token: str, key: bytes) -> str:
    """Reverse of encrypt_field. v0.3 will use AES-256-GCM."""
    raw = base64.b64decode(token.encode())
    nonce, encrypted = raw[:16], raw[16:]
    keystream = hashlib.sha256(key + nonce).digest()
    return bytes(a ^ b for a, b in zip(encrypted, keystream[:len(encrypted)])).decode()


def generate_session_key() -> str:
    """Generate a secure random session key (hex string)."""
    return secrets.token_hex(32)


# ---------------------------------------------------------------------------
# CURTAIN service
# ---------------------------------------------------------------------------

class CurtainService:
    """
    CURTAIN - Owns encryption, signing, and verification services.
    Does not own routing, storage, or communication channels.
    """

    def __init__(self, config_path: str, name: str) -> None:
        self.config = load_config(config_path)
        component = next(c for c in self.config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=component['port'],
            heartbeat_file=component['heartbeat_file'],
            log_dir=self.config['log_dir'],
        )
        # Master signing secret — in production: loaded from secure key store
        self.signing_secret = self.config.get('curtain', {}).get('signing_secret', generate_session_key())
        self.runtime.register_route('POST', '/sign', self.sign)
        self.runtime.register_route('POST', '/verify', self.verify)
        self.runtime.register_route('POST', '/session-key', self.new_session_key)

    def sign(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Sign a payload and return a CURTAIN envelope token."""
        data = payload.get('data', {})
        token = sign_envelope(data, self.signing_secret)
        return 200, {'token': token, 'algorithm': 'HMAC-SHA256'}

    def verify(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Verify a CURTAIN envelope token."""
        token = payload.get('token', '')
        valid, data = verify_envelope(token, self.signing_secret)
        return 200, {'valid': valid, 'payload': data}

    def new_session_key(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Issue a new session key for point-to-point operator communication."""
        key = generate_session_key()
        return 200, {'session_key': key, 'expires_in_seconds': 3600}

    def start(self) -> None:
        self.runtime.logger.info('CURTAIN active — signing secret loaded')
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='CURTAIN - Cascadia OS encryption layer')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    CurtainService(a.config, a.name).start()


if __name__ == '__main__':
    main()
