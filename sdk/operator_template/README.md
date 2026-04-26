# Operator Template — Cascadia OS SDK

This template gives you a working Cascadia OS operator in minutes.

## What operators are

An operator is an independent Python HTTP service that:
- Exposes a `/health` endpoint
- Exposes action endpoints (e.g. `/api/run`)
- Registers itself with CREW on startup
- Declares its capabilities in `manifest.json`

Cascadia OS routes workflow steps to your operator by `id`. The workflow engine calls your action endpoint, waits for a response, and continues execution.

## How to use this template

```bash
# Copy the template
cp -r sdk/operator_template my_operators/my_new_operator

# Edit manifest.json — change id, name, capabilities, port
# Edit server.py — add your business logic

# Run the validator
python sdk/validator/validate_manifest.py my_operators/my_new_operator/

# Start your operator
python my_operators/my_new_operator/server.py
```

## Manifest fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier. Lowercase, underscores only. |
| `name` | Yes | Human-readable display name. |
| `version` | Yes | Semantic version string (e.g. "1.0.0"). |
| `type` | Yes | `skill`, `service`, `system`, or `composite`. |
| `description` | Yes | What this operator does. |
| `autonomy_level` | Yes | `manual_only`, `assistive`, `semi_autonomous`, or `autonomous`. |
| `capabilities` | Yes | List of capability strings (e.g. `["email.send"]`). |
| `required_dependencies` | Yes | Operator IDs this depends on. Usually `[]`. |
| `requested_permissions` | Yes | Permissions this operator needs (e.g. `["gmail.send"]`). |
| `health_hook` | Yes | Health endpoint path. Must be `"/health"`. |
| `port` | Yes | Port this operator listens on. Must not conflict with other operators. |
| `start_cmd` | Yes | Filename of the entry point script (e.g. `"server.py"`). |
| `autostart` | No | If `true`, FLINT starts this operator automatically. Default: `true`. |
| `author` | No | Your name or organization. |
| `license` | No | License identifier (e.g. `"MIT"`, `"proprietary"`). |
| `support_url` | No | URL for operator support. |
| `depot_category` | No | DEPOT marketplace category. |
| `depot_price_usd` | No | Price in USD. 0 for free operators. |
| `min_cascadia_version` | No | Minimum Cascadia OS version required. |
| `network_access` | No | `true` if operator makes outbound network requests. |
| `vault_namespaces` | No | VAULT key prefixes this operator reads from. |
| `input_schema` | No | JSON Schema for operator input. |
| `output_schema` | No | JSON Schema for operator output. |

## Integration points

### VAULT — persistent memory

```python
from cascadia_sdk import vault_store, vault_get

vault_store('my_operator:key', 'value')      # Store
value = vault_get('my_operator:key')          # Retrieve
```

### SENTINEL — risk classification

```python
from cascadia_sdk import sentinel_check

if not sentinel_check('email.send', {'recipient': email}):
    return 403, {'error': 'action not permitted'}
```

### BEACON — route to other operators

```python
from cascadia_sdk import beacon_route

result = beacon_route('crm_operator', {'action': 'create_contact', 'data': contact_data})
```

### CREW — register on startup

```python
from cascadia_sdk import crew_register

crew_register(MANIFEST)  # Call once on startup
```

## Confidence and Self-Escalation

Return confidence scores to trigger automatic human review when your operator is unsure:

```python
return {
    'output': result,
    'confidence': 0.85,
    'escalate_if_below': 0.80,
    'escalation_reason': 'Unusual input — recommend human review'
}
```

When `confidence < escalate_if_below`, Cascadia OS inserts an approval gate automatically.

## Run the validator

```bash
python sdk/validator/validate_manifest.py sdk/operator_template/
```

The validator checks:
- manifest.json exists and is valid JSON
- All required fields are present
- start_cmd script exists
- Python syntax is valid
- No hardcoded secrets
- network_access declaration matches imports

## Submit to DEPOT

1. Run the validator and fix all issues
2. Test your operator end-to-end
3. Submit at: depot.zyrcon.ai

## Revenue

Your first $25,000 in DEPOT sales: yours entirely (0%).
After $25,000 lifetime: you keep 80%. Zyrcon keeps 20%.

This is better than every major app marketplace.
Start publishing at: depot.zyrcon.ai
