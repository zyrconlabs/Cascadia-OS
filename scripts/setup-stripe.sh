#!/bin/bash
# Setup Stripe integration for Cascadia OS

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Cascadia OS - Stripe Setup                ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# Generate license_secret in config.json if missing or placeholder
CONFIG="$ROOT/config.json"
if [ ! -f "$CONFIG" ]; then
  echo "ERROR: config.json not found. Run install.sh first."
  exit 1
fi

CURRENT_SECRET=$(python3 -c "import json; c=json.load(open('$CONFIG')); print(c.get('license_secret',''))" 2>/dev/null || echo "")
if [ -z "$CURRENT_SECRET" ] || echo "$CURRENT_SECRET" | grep -q "replace-"; then
  SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  python3 - "$CONFIG" "$SECRET" <<'PYEOF'
import json, sys
path, secret = sys.argv[1], sys.argv[2]
cfg = json.load(open(path))
cfg['license_secret'] = secret
open(path, 'w').write(json.dumps(cfg, indent=2) + '\n')
PYEOF
  echo "✓ Generated license_secret in config.json"
else
  echo "✓ license_secret already set"
fi

# stripe.config.json instructions
STRIPE_CFG="$ROOT/stripe.config.json"
echo ""
echo "stripe.config.json is at: $STRIPE_CFG"
if grep -q "REPLACE" "$STRIPE_CFG"; then
  echo "  ⚠  Still contains placeholder values — update before going live"
fi

echo ""
echo "Next steps:"
echo ""
echo "1. Go to https://dashboard.stripe.com/register"
echo "2. Get API keys from: Developers → API keys (test mode)"
echo ""
echo "3. Create a Product: Pro — \$49/month"
echo "   Note the Price ID (price_xxx...)"
echo ""
echo "4. Update stripe.config.json:"
echo "   \"price_ids\": { \"pro\": \"price_xxx...\" }"
echo "   \"checkout_links\": { \"pro\": \"https://buy.stripe.com/...\" }"
echo ""
echo "5. Create a webhook endpoint in Stripe dashboard:"
echo "   URL: https://your-domain.com/api/stripe/webhook  (or use stripe CLI for local)"
echo "   Events: checkout.session.completed, customer.subscription.deleted"
echo "   Copy the webhook signing secret into stripe.config.json:"
echo "   \"webhook_secret\": \"whsec_...\""
echo ""
echo "6. Local testing with Stripe CLI:"
echo "   brew install stripe/stripe-cli/stripe"
echo "   stripe login"
echo "   stripe listen --forward-to localhost:6101/api/stripe/webhook"
echo "   stripe trigger checkout.session.completed"
echo ""
echo "7. Start the webhook service:"
echo "   python -m cascadia.core.stripe_webhook"
echo ""
