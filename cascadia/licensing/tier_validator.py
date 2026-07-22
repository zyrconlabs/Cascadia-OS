"""
tier_validator.py — Cascadia OS
Ed25519 license key VALIDATION (verify-only).
Owns: key format, signature verification, expiry checking, version gating.
Does not own: key storage, key SIGNING, email delivery, Stripe events.

Key format (v3):
    zyrcon_{tier}_{customer_id}_{expiry_epoch}_v3_{ed25519_sig_hex}

Old formats (rejected):
    v2  zyrcon_{tier}_{customer_id}_{expiry_epoch}_v2_{hmac_sha256}
    v1  zyrcon_{tier}_{customer_id}_{expiry_epoch}_{hmac_sha256}

WHY ASYMMETRIC
    v1/v2 were symmetric HMAC: the secret that VERIFIED a key was the same
    secret that SIGNED one. That secret therefore could never safely ship, so a
    customer node had no way to verify anything — activation could not work at
    all. Under v3 a node holds only the PUBLIC key: it can verify, and cannot
    forge. Signing lives in license_signer.py, which never ships.

FAIL-CLOSED CONTRACT
    Whenever this module cannot verify — no public key, an unreadable bundle, a
    malformed key, a signature that does not decode, or any error out of the
    crypto layer — it returns a not-valid result. It never returns valid, and it
    never raises. Callers grant tier only on {'valid': True}.
    Enforced by tests/test_license_ed25519.py.

SIGNATURE ENCODING — HEX, NOT BASE64URL
    Keys are parsed by splitting on '_' and reading positionally from the end.
    base64url (what depot/signing.py uses for package manifests) includes '_' in
    its alphabet, so a base64url signature would corrupt that split for whichever
    signatures happened to contain one — an intermittent, data-dependent parse
    bug. Hex is 0-9a-f only, so it can never collide with the delimiter.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

CURRENT_KEY_VERSION = 'v3'

# The public key bundle shipped with the product: {key_id: base64url_public_key}.
# Deliberately separate from cascadia/depot/zyrcon_signing_keys.json — depot
# treats any key in its bundle as authorised to sign mission packages, and a
# licensing key must never carry that authority.
DEFAULT_BUNDLE_PATH = Path(__file__).parent / 'zyrcon_license_keys.json'

_SIG_BYTES = 64          # ed25519 signature length
_SIG_HEX_CHARS = 128     # ...as hex

VALID_TIERS = ('lite', 'pro', 'pro_workspace', 'business', 'enterprise')

TIER_RANKS: Dict[str, int] = {
    'lite':          0,
    'pro':           1,
    'pro_workspace': 2,
    'business':      3,
    'enterprise':    4,
}

TIER_MAX_USERS: Dict[str, int] = {
    'lite':          1,
    'pro':           1,
    'pro_workspace': 3,
    'business':      10,
    'enterprise':    999,
}


def get_max_users(tier: str) -> int:
    """Return the maximum number of users allowed for a given tier."""
    return TIER_MAX_USERS.get(tier, 1)


_VERSION_PREFIX = 'v'


def _is_version_tag(s: str) -> bool:
    return s.startswith(_VERSION_PREFIX) and s[1:].isdigit()


def _decode_public_key(b64: str) -> Optional[Ed25519PublicKey]:
    """base64url (padding optional) → Ed25519PublicKey, or None if unusable.

    Never raises: malformed key material is a deny, not a crash.
    """
    try:
        s = (b64 or '').strip()
        if not s:
            return None
        raw = base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        return None


def load_key_bundle(path: Union[str, Path, None] = None) -> Dict[str, str]:
    """Load {key_id: base64url_public_key}. Returns {} if unreadable.

    Absence is a normal state on an unconfigured node, so it is reported as an
    empty bundle rather than an exception; the validator then denies.
    """
    try:
        p = Path(path) if path else DEFAULT_BUNDLE_PATH
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _coerce_public_keys(source: Any) -> List[str]:
    """Accept any reasonable spelling of "the public keys" → list of base64url.

    Supports: None/'' (none) · a bare base64url key · a bundle dict ·
    a path to a bundle JSON. Anything unrecognised yields [] (deny).
    """
    try:
        if source is None:
            return []
        if isinstance(source, dict):
            return [v for v in source.values() if isinstance(v, str)]
        if isinstance(source, Path):
            return list(load_key_bundle(source).values())
        if isinstance(source, str):
            s = source.strip()
            if not s:
                return []
            # A path to a bundle, or the key itself.
            if s.endswith('.json'):
                return list(load_key_bundle(s).values())
            return [s]
        if isinstance(source, Iterable):
            return [v for v in source if isinstance(v, str)]
    except Exception:
        pass
    return []


class _UseDefaultBundle:
    """Sentinel: the caller passed nothing, so fall back to the shipped bundle.

    Deliberately NOT None. An explicit None means "I have no key material",
    which must DENY — collapsing the two would turn a caller that failed to
    obtain a key into one that silently verifies against the shipped bundle.
    """


_DEFAULT = _UseDefaultBundle()


class TierValidator:
    """Validates Ed25519-signed license keys. Holds PUBLIC keys only.

    public_keys accepts a bundle dict, a bundle path, a single base64url public
    key, or None. Omit the argument entirely to use the shipped bundle;
    passing None explicitly means "no keys" and every key then fails to verify.
    """

    def __init__(self, public_keys: Any = _DEFAULT,
                 key_version: str = CURRENT_KEY_VERSION) -> None:
        self._key_version = key_version
        if isinstance(public_keys, _UseDefaultBundle):
            public_keys = load_key_bundle()
        self._public_keys: List[Ed25519PublicKey] = [
            pk for pk in (_decode_public_key(b) for b in _coerce_public_keys(public_keys))
            if pk is not None
        ]

    @property
    def has_keys(self) -> bool:
        """True when at least one usable public key was loaded.

        Lets a caller distinguish "this node cannot verify anything"
        (indeterminate) from "this key is genuinely bad" (definitive).
        """
        return bool(self._public_keys)

    def _verify(self, message: bytes, sig_hex: str) -> bool:
        """True only if some loaded public key verifies the signature."""
        if len(sig_hex) != _SIG_HEX_CHARS:
            return False
        try:
            sig = bytes.fromhex(sig_hex)
        except ValueError:
            return False
        if len(sig) != _SIG_BYTES:
            return False
        for pub in self._public_keys:
            try:
                pub.verify(sig, message)
                return True
            except InvalidSignature:
                continue
            except Exception:
                # Any other crypto-layer failure is a deny for THIS key, never
                # an exception to the caller.
                continue
        return False

    def validate(self, key: str) -> Dict[str, Any]:
        """
        Parse and cryptographically verify a license key.
        Returns dict with 'valid' bool and details, or 'error' on failure.
        Never raises.
        """
        if not key or not isinstance(key, str) or not key.startswith('zyrcon_'):
            return {'valid': False, 'error': 'invalid_format'}

        # Match tier by prefix (handles tiers that contain underscores, e.g. pro_workspace)
        tier = None
        for t in sorted(VALID_TIERS, key=len, reverse=True):
            if key.startswith(f'zyrcon_{t}_'):
                tier = t
                break
        if not tier:
            return {'valid': False, 'error': 'invalid_tier'}

        # remainder is everything after 'zyrcon_{tier}_'
        remainder_parts = key[len(f'zyrcon_{tier}_'):].split('_')

        # v2+: [...customer_id..., expiry, version_tag, sig]
        # v1:  [...customer_id..., expiry, sig]
        if len(remainder_parts) >= 4 and _is_version_tag(remainder_parts[-2]):
            sig = remainder_parts[-1]
            key_version = remainder_parts[-2]
            expiry_str = remainder_parts[-3]
            customer_id = '_'.join(remainder_parts[:-3])
        elif len(remainder_parts) >= 3:
            sig = remainder_parts[-1]
            expiry_str = remainder_parts[-2]
            customer_id = '_'.join(remainder_parts[:-2])
            key_version = 'v1'
        else:
            return {'valid': False, 'error': 'invalid_format'}

        if not customer_id:
            return {'valid': False, 'error': 'invalid_format'}

        if key_version != self._key_version:
            return {'valid': False, 'error': 'key_version_rejected',
                    'key_version': key_version, 'expected': self._key_version}

        try:
            expiry_ts = int(expiry_str)
        except ValueError:
            return {'valid': False, 'error': 'invalid_expiry'}

        # No usable public key → this node cannot verify anything. Deny, and say
        # so distinctly so the gate can treat it as indeterminate rather than as
        # a definitive "this key is forged".
        if not self._public_keys:
            return {'valid': False, 'error': 'no_verify_key'}

        # Ed25519 verification — over exactly the key minus the trailing _{sig}.
        message = f'zyrcon_{tier}_{customer_id}_{expiry_str}_{key_version}'.encode()
        if not self._verify(message, sig):
            return {'valid': False, 'error': 'invalid_signature'}

        # Signature is good; only now does expiry matter (so an expired but
        # genuine key reports 'expired', not 'invalid_signature').
        now = int(time.time())
        if expiry_ts < now:
            return {'valid': False, 'error': 'expired',
                    'days_expired': (now - expiry_ts) // 86400}

        return {
            'valid':         True,
            'tier':          tier,
            'customer_id':   customer_id,
            'expires_at':    expiry_ts,
            'days_remaining': (expiry_ts - now) // 86400,
        }
