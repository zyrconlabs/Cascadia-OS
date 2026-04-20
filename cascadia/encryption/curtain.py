"""
curtain/curtain.py - Cascadia OS v0.43
CURTAIN: Encryption layer for Cascadia OS.

Owns: transport encryption, at-rest data protection, key derivation,
      encrypted envelope creation and verification.
Does not own: routing (BEACON), capability enforcement (SENTINEL),
              communication channels (VANGUARD, BELL).

Cryptographic primitives:
  Envelope signing  — HMAC-SHA256 (stdlib)
  Key derivation    — PBKDF2-HMAC-SHA256, 100,000 iterations (stdlib)
  Field encryption  — AES-256-GCM (cryptography library)
                      96-bit random nonce per operation
                      128-bit authentication tag — tamper detection built in
                      No length truncation — full plaintext preserved

Upgrade: encrypt_field/decrypt_field replaced from XOR+SHA256
keystream to AES-256-GCM
(authenticated encryption, arbitrary length, tamper-evident).
Public interface is unchanged — callers require no modification.
"""
# MATURITY: PRODUCTION — HMAC-SHA256 signing and AES-256-GCM field encryption.
# Asymmetric key exchange is planned.
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
import base64
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def _derive_key(secret: str, salt: bytes) -> bytes:
    """
    Derives a 32-byte AES key from a string secret using PBKDF2-HMAC-SHA256.
    100,000 iterations — slow enough to resist brute force, fast enough for
    interactive use.
    Owns: key derivation. Does not own secret storage or rotation.
    """
    return hashlib.pbkdf2_hmac(
        'sha256',
        secret.encode(),
        salt,
        iterations=100_000,
        dklen=32,
    )


# ---------------------------------------------------------------------------
# Envelope signing — HMAC-SHA256 (stdlib, no external deps)
# ---------------------------------------------------------------------------

def sign_envelope(payload: Dict[str, Any], secret: str) -> str:
    """
    Sign a payload dict with HMAC-SHA256. Returns a base64-encoded envelope.
    The signature covers the canonical JSON serialisation (sorted keys).
    Owns: HMAC-SHA256 signing. Does not own payload schema.
    """
    body = json.dumps(payload, sort_keys=True).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    envelope = {
        'payload': payload,
        'sig': sig,
        'ts': datetime.now(timezone.utc).isoformat(),
        'alg': 'HMAC-SHA256',
    }
    return base64.b64encode(json.dumps(envelope).encode()).decode()


def verify_envelope(token: str, secret: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Verify a signed envelope token. Returns (valid, payload) or (False, None).
    Uses hmac.compare_digest to prevent timing attacks.
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


# ---------------------------------------------------------------------------
# Field encryption — AES-256-GCM
# ---------------------------------------------------------------------------

_GCM_NONCE_BYTES = 12   # 96-bit nonce — GCM standard recommendation


def encrypt_field(value: str, key: bytes) -> str:
    """
    Encrypt a string field with AES-256-GCM.

    Token format (base64): nonce(12) || ciphertext+tag(variable)
    The GCM authentication tag (16 bytes) is appended to the ciphertext
    automatically by the library — tamper detection is built in.

    Properties:
    - Authenticated: any bit flip in ciphertext or tag raises InvalidTag
    - Nonce is random per call — safe to encrypt the same value twice
    - No length truncation — full plaintext is preserved
    - key must be exactly 32 bytes (use _derive_key or a 32-byte secret)

    Owns: AES-256-GCM encryption. Does not own key distribution.
    """
    nonce = secrets.token_bytes(_GCM_NONCE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext_with_tag = aesgcm.encrypt(nonce, value.encode('utf-8'), None)
    return base64.b64encode(nonce + ciphertext_with_tag).decode()


def decrypt_field(token: str, key: bytes) -> str:
    """
    Decrypt a field token produced by encrypt_field.
    Raises ValueError on authentication failure (tampered ciphertext or tag).
    Owns: AES-256-GCM decryption. Does not own key distribution.
    """
    try:
        raw = base64.b64decode(token.encode())
        nonce, ciphertext_with_tag = raw[:_GCM_NONCE_BYTES], raw[_GCM_NONCE_BYTES:]
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        return plaintext.decode('utf-8')
    except Exception as exc:
        raise ValueError(f'CURTAIN decryption failed: {exc}') from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_session_key() -> str:
    """Generate a cryptographically secure random session key (64-char hex = 32 bytes)."""
    return secrets.token_hex(32)


def derive_field_key(signing_secret: str) -> bytes:
    """
    Derive a 32-byte AES field-encryption key from the master signing secret.
    Uses a fixed salt labelled 'curtain-field-key' so the derived key is
    deterministic for a given signing_secret but separate from the HMAC key.
    Owns: key derivation for field encryption. Does not own the signing secret.
    """
    return _derive_key(signing_secret, b'curtain-field-key-v1')


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
        curtain_cfg = self.config.get('curtain', {})
        raw_secret = curtain_cfg.get('signing_secret', '')
        self.signing_secret: str = raw_secret if raw_secret else generate_session_key()
        # Derive a separate AES key for field encryption from the master secret
        self._field_key: bytes = derive_field_key(self.signing_secret)

        self.runtime.register_route('POST', '/sign',          self.sign)
        self.runtime.register_route('POST', '/verify',        self.verify)
        self.runtime.register_route('POST', '/encrypt',       self.encrypt)
        self.runtime.register_route('POST', '/decrypt',       self.decrypt_route)
        self.runtime.register_route('POST', '/session-key',   self.new_session_key)
        self.runtime.register_route('GET',  '/capabilities',  self.capabilities)

    # ------------------------------------------------------------------
    # Route handlers
    # ------------------------------------------------------------------

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

    def encrypt(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Encrypt a string field with AES-256-GCM."""
        value = payload.get('value', '')
        if not isinstance(value, str):
            return 400, {'error': 'value must be a string'}
        token = encrypt_field(value, self._field_key)
        return 200, {'token': token, 'algorithm': 'AES-256-GCM'}

    def decrypt_route(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Decrypt a field token."""
        token = payload.get('token', '')
        try:
            value = decrypt_field(token, self._field_key)
            return 200, {'value': value}
        except ValueError as exc:
            self.runtime.logger.warning('CURTAIN decrypt failed: %s', exc)
            return 400, {'error': 'decryption failed — token invalid or tampered'}

    def new_session_key(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Issue a new random session key for point-to-point operator communication."""
        key = generate_session_key()
        return 200, {'session_key': key, 'expires_in_seconds': 3600}

    def capabilities(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Return CURTAIN capability summary."""
        return 200, {
            'signing': 'HMAC-SHA256',
            'field_encryption': 'AES-256-GCM',
            'key_derivation': 'PBKDF2-HMAC-SHA256 (100k iterations)',
            'asymmetric': 'planned',
        }

    def start(self) -> None:
        self.runtime.logger.info(
            'CURTAIN active — AES-256-GCM field encryption, HMAC-SHA256 signing'
        )
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='CURTAIN - Cascadia OS encryption layer')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    CurtainService(a.config, a.name).start()


if __name__ == '__main__':
    main()
