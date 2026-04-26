#!/usr/bin/env python3
"""
generate_license.py — Cascadia OS
CLI tool to generate Cascadia OS license keys.

Usage:
  python scripts/generate_license.py --tier pro --customer acme --days 365 --secret <secret>

The secret must match the 'license_secret' value in config.json.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cascadia.licensing.tier_validator import TierValidator, VALID_TIERS


def main() -> None:
    p = argparse.ArgumentParser(description='Generate a Cascadia OS license key')
    p.add_argument('--tier', required=True, choices=VALID_TIERS, help='License tier')
    p.add_argument('--customer', required=True, help='Customer ID (alphanumeric, no underscores)')
    p.add_argument('--days', type=int, default=365, help='Days until expiry (default: 365)')
    p.add_argument('--secret', required=True, help='HMAC signing secret (must match config license_secret)')
    a = p.parse_args()

    if '_' in a.customer:
        p.error('Customer ID must not contain underscores')

    expiry_epoch = int(time.time()) + a.days * 86400
    validator = TierValidator(a.secret)
    key = validator.generate(a.tier, a.customer, expiry_epoch)

    result = validator.validate(key)
    print(f'License key: {key}')
    print(f'  Tier:     {result["tier"]}')
    print(f'  Customer: {result["customer_id"]}')
    print(f'  Expires:  {result["expires_at"]}  ({result["days_remaining"]} days)')
    print(f'  Valid:    {result["valid"]}')


if __name__ == '__main__':
    main()
