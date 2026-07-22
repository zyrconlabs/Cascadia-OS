"""
license_signer.py — Cascadia OS
Ed25519 license key SIGNING. VENDOR-SIDE ONLY.

██ THIS MODULE MUST NEVER SHIP TO A CUSTOMER ██
    It is export-ignored in .gitattributes, so `git archive` excludes it from
    the payload. It requires the PRIVATE signing key, which lives outside every
    repository at ~/.config/zyrcon/licensing.key (mode 0600) and is never
    committed. A customer node holds only the public key and uses
    tier_validator.TierValidator to VERIFY.

    This split is the whole point of v3: under the old symmetric HMAC scheme the
    verify secret was the sign secret, so anything able to check a licence was
    also able to mint one.

Usage (vendor machine):
    from cascadia.licensing.license_signer import LicenseSigner
    key = LicenseSigner().generate('enterprise', 'acme-corp', expiry_epoch)

The emitted key must be byte-identical to what TierValidator verifies:
    zyrcon_{tier}_{customer_id}_{expiry}_v3_{sig_hex}
signed over:
    b"zyrcon_{tier}_{customer_id}_{expiry}_v3"
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cascadia.licensing.tier_validator import CURRENT_KEY_VERSION, VALID_TIERS

DEFAULT_KEY_PATH = Path.home() / '.config' / 'zyrcon' / 'licensing.key'
ENV_KEY_PATH = 'ZYRCON_LICENSING_KEY_PATH'
DEFAULT_KEY_ID = 'zyrcon-licensing-2026-q3'


class LicenseSigner:
    """Signs license keys with the Ed25519 private key.

    Mirrors depot.signing.LocalSigner: raw 32-byte seed on disk, PEM tolerated.
    Unlike the validator, this DOES raise on missing/unusable key material —
    failing to sign must be loud, whereas failing to verify must be a quiet deny.
    """

    def __init__(self, key_path: Optional[str] = None,
                 key_id: str = DEFAULT_KEY_ID) -> None:
        path = Path(key_path or os.environ.get(ENV_KEY_PATH, str(DEFAULT_KEY_PATH)))
        if not path.exists():
            raise FileNotFoundError(
                f'licensing private key not found: {path}. Generate one with:\n'
                f'  python3 scripts/generate_signing_key.py '
                f'--key-id {DEFAULT_KEY_ID} --output {path}'
            )
        raw = path.read_bytes()
        if len(raw) == 32:
            self._private_key = Ed25519PrivateKey.from_private_bytes(raw)
        else:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            self._private_key = load_pem_private_key(raw, password=None)
        self._key_id = key_id

    def key_id(self) -> str:
        return self._key_id

    def public_key_b64(self) -> str:
        """base64url public key (no padding) — for the shipped bundle."""
        import base64
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        raw = self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()

    def generate(self, tier: str, customer_id: str, expiry: int) -> str:
        """Return a signed v3 license key. expiry is a Unix epoch timestamp."""
        if tier not in VALID_TIERS:
            raise ValueError(f'invalid tier: {tier!r} (expected one of {VALID_TIERS})')
        if not customer_id:
            raise ValueError('customer_id is required')
        # customer_id may contain underscores (the parser rejoins them), but a
        # trailing/leading one would shift the positional split.
        if customer_id.startswith('_') or customer_id.endswith('_'):
            raise ValueError('customer_id must not start or end with "_"')
        expiry = int(expiry)

        message = f'zyrcon_{tier}_{customer_id}_{expiry}_{CURRENT_KEY_VERSION}'.encode()
        # HEX, never base64url: the key parser splits on '_' and base64url's
        # alphabet contains it. See tier_validator's module docstring.
        sig_hex = self._private_key.sign(message).hex()
        return f'{message.decode()}_{sig_hex}'
