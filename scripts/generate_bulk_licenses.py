#!/usr/bin/env python3
"""Bulk license key generator for WooCommerce Serial Numbers CSV import."""

import argparse
import csv
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TIERS = {
    "pro":              {"prefix": "CZPRO",  "seats": 1},
    "pro-workspace":    {"prefix": "CZWRK",  "seats": 5},
    "business-starter": {"prefix": "CZBSS",  "seats": 20},
    "business-growth":  {"prefix": "CZBSG",  "seats": 50},
    "business-max":     {"prefix": "CZBSM",  "seats": 999},
    "enterprise":       {"prefix": "CZENT",  "seats": 999},
}

def _generate_key(prefix: str) -> str:
    segments = [secrets.token_hex(2).upper() for _ in range(4)]
    return f"{prefix}-{'-'.join(segments)}"

def generate_batch(tier: str, count: int, days: int, customer: str = "") -> list[dict]:
    if tier not in TIERS:
        raise ValueError(f"Unknown tier '{tier}'. Valid: {', '.join(TIERS)}")

    cfg = TIERS[tier]
    prefix = cfg["prefix"]
    seats = cfg["seats"]
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(days=days)).strftime("%Y-%m-%d") if days > 0 else ""

    rows = []
    for _ in range(count):
        key = _generate_key(prefix)
        rows.append({
            "serial_key": key,
            "status": "available",
            "expire_date": expires,
            "seats": seats,
            "tier": tier,
            "customer": customer,
            "generated_at": now.isoformat(),
        })
    return rows

def write_csv(rows: list[dict], output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate bulk Cascadia OS license keys for WooCommerce CSV import"
    )
    parser.add_argument("--tier", required=True, choices=list(TIERS.keys()),
                        help="License tier")
    parser.add_argument("--count", type=int, default=100,
                        help="Number of keys to generate (default: 100)")
    parser.add_argument("--days", type=int, default=365,
                        help="License validity in days; 0 = no expiry (default: 365)")
    parser.add_argument("--customer", default="",
                        help="Optional customer name tag embedded in CSV metadata")
    parser.add_argument("--output", default="",
                        help="Output CSV path (default: data/license_keys/<tier>_<date>.csv)")
    args = parser.parse_args()

    if not args.output:
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        args.output = f"data/license_keys/{args.tier}_{stamp}.csv"

    rows = generate_batch(args.tier, args.count, args.days, args.customer)
    write_csv(rows, args.output)

    print(f"Generated {len(rows)} keys  →  {args.output}")
    print(f"  Tier:    {args.tier}")
    print(f"  Prefix:  {TIERS[args.tier]['prefix']}")
    print(f"  Expires: {rows[0]['expire_date'] or 'never'}")
    print(f"  Seats:   {rows[0]['seats']}")
    print(f"\nSample keys:")
    for row in rows[:5]:
        print(f"  {row['serial_key']}")
    if len(rows) > 5:
        print(f"  ... ({len(rows) - 5} more)")

if __name__ == "__main__":
    main()
