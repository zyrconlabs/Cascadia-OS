"""Shared v3 (Ed25519) license-key helpers for the test suite.

TierValidator is verify-only — signing lives in cascadia/licensing/license_signer.py,
which is export-ignored and needs the real private key. Tests therefore mint with
an EPHEMERAL keypair generated in-process: no dependency on ~/.config/zyrcon/,
so the suite runs anywhere, and no test can accidentally exercise the production
signing key.

Typical use:

    from tests._v3_keys import ephemeral_keys, mint, bundle_file

    KEYS = ephemeral_keys()
    key  = mint('pro', 'acme', FUTURE, KEYS)
    TierValidator(KEYS.public_b64).validate(key)          # direct
    os.environ['ZYRCON_LICENSE_KEYS_PATH'] = bundle_file(KEYS)   # via the gate
"""
from __future__ import annotations

import base64
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

KEY_VERSION = 'v3'
TEST_KEY_ID = 'test-ephemeral'


@dataclass
class Keys:
    private: Ed25519PrivateKey
    public_b64: str


def ephemeral_keys() -> Keys:
    """A fresh Ed25519 keypair, valid only for this process."""
    priv = Ed25519PrivateKey.generate()
    raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return Keys(priv, base64.urlsafe_b64encode(raw).rstrip(b'=').decode())


def mint(tier: str, customer_id: str, expiry: int, keys: Keys) -> str:
    """Mint a v3 key. Mirrors LicenseSigner.generate exactly (hex signature)."""
    message = f'zyrcon_{tier}_{customer_id}_{expiry}_{KEY_VERSION}'.encode()
    return f'{message.decode()}_{keys.private.sign(message).hex()}'


def bundle_dict(keys: Keys, key_id: str = TEST_KEY_ID) -> dict:
    return {key_id: keys.public_b64}


def bundle_file(keys: Keys, key_id: str = TEST_KEY_ID) -> str:
    """Write a bundle to a temp file and return its path.

    For pointing ZYRCON_LICENSE_KEYS_PATH at, which is how license_gate is told
    to use test keys instead of the shipped bundle.
    """
    fd = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
    json.dump(bundle_dict(keys, key_id), fd)
    fd.close()
    return fd.name
