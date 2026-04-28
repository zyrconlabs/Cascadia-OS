#!/bin/bash
# Test license generation and activation locally

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== Cascadia OS License System Test ==="
echo ""

# Extract license_secret from config.json
SECRET=$(python3 -c "import json; c=json.load(open('config.json')); print(c.get('license_secret',''))" 2>/dev/null || echo "")

if [ -z "$SECRET" ] || echo "$SECRET" | grep -q "replace-"; then
  echo "⚠  license_secret not set in config.json"
  echo "   Run: bash scripts/setup-stripe.sh"
  SECRET="test-secret-do-not-use-in-production"
  echo "   Using temporary test secret for format validation only."
fi

echo ""
echo "--- Generating license keys ---"
python3 scripts/generate_license.py --tier lite     --customer testuser --days 365  --secret "$SECRET"
echo ""
python3 scripts/generate_license.py --tier pro      --customer testuser --days 365  --secret "$SECRET"
echo ""
python3 scripts/generate_license.py --tier enterprise --customer testuser --days 365 --secret "$SECRET"
echo ""

echo "--- License gate status endpoint ---"
echo "  (requires license_gate running on port 6100)"
curl -sf http://localhost:6100/api/license/status 2>/dev/null && echo "" || echo "  service not running — start with: python -m cascadia.licensing.license_gate"

echo ""
echo "--- Stripe webhook service status ---"
echo "  (requires stripe_webhook running on port 6101)"
curl -sf http://localhost:6101/api/health 2>/dev/null && echo "" || echo "  service not running — start with: python -m cascadia.core.stripe_webhook"

echo ""
echo "--- Simulate checkout event (Stripe CLI) ---"
echo "  stripe trigger checkout.session.completed"
echo ""
echo "  or POST directly:"
echo "  curl -X POST http://localhost:6101/api/stripe/webhook \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -H 'Stripe-Signature: t=...,v1=...' \\"
echo "    -d '{\"type\":\"checkout.session.completed\",...}'"
