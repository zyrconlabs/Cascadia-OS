#!/usr/bin/env python3
"""Generate a local Ed25519 signing keypair for Cascadia OS mission package signing.

Private key is written to ~/.config/zyrcon/signing.key (raw 32-byte seed).
The matching public key (base64url, no padding) is printed for adding to
cascadia/depot/zyrcon_signing_keys.json.

The private key is never committed to the repo. Add the public key to the
key bundle manually after generation.

Usage:
    python3 scripts/generate_signing_key.py [--key-id KEY_ID]
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
except ImportError:
    print("ERROR: cryptography package not installed. Run: pip install cryptography")
    sys.exit(1)

_DEFAULT_KEY_PATH = Path.home() / ".config" / "zyrcon" / "signing.key"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Zyrcon Ed25519 signing keypair")
    parser.add_argument("--key-id", default="zyrcon-2026-q2",
                        help="Key ID to assign (for key bundle entry)")
    parser.add_argument("--output", default=str(_DEFAULT_KEY_PATH),
                        help=f"Private key output path (default: {_DEFAULT_KEY_PATH})")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing key file")
    args = parser.parse_args()

    out_path = Path(args.output)
    if out_path.exists() and not args.force:
        print(f"ERROR: {out_path} already exists. Use --force to overwrite.")
        sys.exit(1)

    private_key = Ed25519PrivateKey.generate()
    pub = private_key.public_key()
    pub_bytes = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()

    # Write raw 32-byte seed (private key bytes)
    from cryptography.hazmat.primitives.serialization import PrivateFormat, NoEncryption
    priv_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(priv_bytes)
    out_path.chmod(0o600)

    print(f"Private key written to: {out_path}")
    print(f"\nAdd to cascadia/depot/zyrcon_signing_keys.json:")
    print(f'  "{args.key_id}": "{pub_b64}"')
    print(f"\nSet env var to use this key:")
    print(f"  export ZYRCON_SIGNING_KEY_PATH={out_path}")


if __name__ == "__main__":
    main()
