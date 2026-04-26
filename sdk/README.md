# Cascadia OS SDK

Developer toolkit for building operators that run on Cascadia OS and publish to DEPOT.

## What operators are

An operator is an independent Python HTTP service that:
- Exposes a `/health` endpoint
- Exposes action endpoints (e.g. `/api/run`)
- Registers with CREW on startup
- Declares capabilities in `manifest.json`

The workflow engine (STITCH/BEACON) routes tasks to your operator. You return results. The runtime handles durability, approval gates, audit logging, and EU AI Act compliance.

## Copy the template

```bash
# Standard operator
cp -r sdk/operator_template my_operators/my_operator

# IoT sensor analysis operator
cp -r sdk/iot_template my_operators/my_iot_operator
```

## Edit manifest.json

Change `id`, `name`, `capabilities`, and `port`. Every field is documented in `sdk/operator_template/README.md`.

## Add your business logic to server.py

The template shows how to:
- Receive and validate input
- Check SENTINEL before consequential actions
- Store results in VAULT
- Return confidence scores for automatic escalation

## Run the validator

```bash
python sdk/validator/validate_manifest.py my_operators/my_operator/
```

Checks: JSON validity, required fields, Python syntax, hardcoded secrets, network declaration accuracy, requirements.txt presence.

## Integration points

### cascadia_sdk.py — stdlib only, no pip install needed

```python
from cascadia_sdk import vault_store, vault_get, sentinel_check, beacon_route, crew_register

# Store in VAULT
vault_store('my_operator:key', 'value')

# Retrieve from VAULT
value = vault_get('my_operator:key')

# Check SENTINEL (fail-closed — returns False on error)
if not sentinel_check('email.send', {'recipient': email}):
    return 403, {'error': 'action not permitted'}

# Route to another operator via BEACON
result = beacon_route('crm_operator', {'action': 'create_contact', 'data': data})

# Register with CREW on startup
crew_register(MANIFEST)
```

## Confidence and Self-Escalation

Operators can self-report confidence to trigger human review automatically.

Return these fields in your handler response:

```python
{
    'output': your_result,
    'confidence': 0.87,          # float 0.0–1.0
    'escalate_if_below': 0.80,   # escalate if confidence < this
    'escalation_reason': 'Complex case detected — please review'
}
```

When `confidence < escalate_if_below`, Cascadia OS inserts an approval gate automatically. The human sees your output, your confidence score, and your escalation reason. They can approve to continue or reject to halt the workflow.

This is the Autonomy Contract: declare what you're unsure about. The runtime handles the rest.

## Submit to DEPOT

1. Run the validator: all checks must pass
2. Test end-to-end on a live Cascadia OS instance
3. Set `depot_price_usd` and `depot_category` in manifest.json
4. Submit at: depot.zyrcon.ai

## Revenue

Your first $25,000 in DEPOT sales: yours entirely (0%).
After $25,000 lifetime: you keep 80%. Zyrcon keeps 20%.

This is better than every major app marketplace.
Start publishing at: depot.zyrcon.ai
