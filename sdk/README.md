[![PyPI version](https://img.shields.io/pypi/v/cascadia-os)](https://pypi.org/project/cascadia-os/)

# Cascadia OS SDK

Two ways to use the SDK depending on your setup. Both expose identical function signatures.

---

## Option A — pip install (recommended)

```bash
pip install cascadia-os
```

```python
from cascadia.sdk import vault_get, vault_store, sentinel_check, beacon_route, crew_register
```

**When to use:** You are building an operator in a proper Python project with a `requirements.txt` or `pyproject.toml`. This is the standard path for DEPOT submissions.

---

## Option B — Standalone template (no pip required)

Copy the file into your operator directory:

```bash
cp sdk/cascadia_sdk.py your_operator/
```

```python
from cascadia_sdk import vault_get, vault_store, sentinel_check, beacon_route, crew_register
```

**When to use:** You received a Cascadia OS hardware unit, you are customizing an existing operator, or you want zero external dependencies. No pip install required — stdlib only.

---

## Requirements

Python 3.11+. No third-party dependencies. All functions use stdlib only (`json`, `os`, `urllib`).

---

## Function Reference

All functions are **safe to call when Cascadia OS is not running** — they catch all exceptions and return safe defaults. No function raises an exception.

---

### `vault_store(key, value)`

Store a secret or persistent value in VAULT.

**Signature:**
```python
def vault_store(key: str, value: str) -> bool
```

| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| `key` | `str` | yes | Storage key. Convention: `"operator_id:field_name"` |
| `value` | `str` | yes | String value to store. Serialize non-strings before passing. |

**Returns:** `bool` — `True` on successful write, `False` on failure or if VAULT is not running.

**Example:**
```python
from cascadia.sdk import vault_store

ok = vault_store('my_operator:api_key', api_key_from_setup)
if not ok:
    logger.warning('VAULT unavailable — key not stored')
```

**Common errors:**
- Returns `False` silently if VAULT (port 5101) is not running. Check that Cascadia OS is started.
- Key must be a string. Value must be a string — use `json.dumps()` for dicts or lists.

---

### `vault_get(key)`

Retrieve a value previously stored in VAULT.

**Signature:**
```python
def vault_get(key: str) -> Optional[str]
```

| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| `key` | `str` | yes | Storage key used when calling `vault_store` |

**Returns:** `Optional[str]` — The stored string, or `None` if the key does not exist or VAULT is not running.

**Example:**
```python
from cascadia.sdk import vault_get

api_key = vault_get('my_operator:api_key')
if api_key is None:
    return {'error': 'operator not configured — run setup'}
```

**Common errors:**
- Returns `None` if the key was never stored or if VAULT is not running. Always check for `None` before using the value.
- Keys are namespaced by convention, not by enforcement. Use `"operator_id:field"` to avoid collisions between operators.

---

### `sentinel_check(action, context)`

Check whether an action is permitted by SENTINEL before executing it.

**Signature:**
```python
def sentinel_check(action: str, context: Optional[Dict[str, Any]] = None) -> bool
```

| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| `action` | `str` | yes | Capability string to check — e.g. `"email.send"`, `"crm.write"` |
| `context` | `dict` | no | Additional context passed to SENTINEL for policy evaluation |

**Returns:** `bool` — `True` if the action is permitted, `False` if denied or if SENTINEL is unreachable.

**Fail-closed:** If SENTINEL is not running or the request times out, this function returns `False`. It never permits an action on error.

**Example:**
```python
from cascadia.sdk import sentinel_check

if not sentinel_check('email.send', {'recipient': email, 'operator_id': 'my_operator'}):
    return {'error': 'action not permitted by SENTINEL'}

# Safe to proceed
send_email(email, body)
```

**Common errors:**
- `action` must match a capability string registered in `cascadia/shared/entitlements.py`. Unrecognised strings are denied by default.
- Always call `sentinel_check` before any consequential action (write, send, delete). Do not skip it for "low-risk" operations — SENTINEL decides what is low-risk.

---

### `beacon_route(target, payload)`

Route a message to another operator via BEACON.

**Signature:**
```python
def beacon_route(target: str, payload: Dict[str, Any]) -> Dict[str, Any]
```

| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| `target` | `str` | yes | ID of the destination operator (matches its manifest `id`) |
| `payload` | `dict` | yes | Message payload forwarded to the target operator |

**Returns:** `Dict[str, Any]` — Response dict from the target operator, or `{}` (empty dict) on failure.

**Example:**
```python
from cascadia.sdk import beacon_route

result = beacon_route('crm_operator', {
    'action': 'create_contact',
    'data': {'name': 'Acme Corp', 'email': 'hello@acme.com'},
})
if not result:
    logger.warning('CRM operator did not respond')
```

**Common errors:**
- Returns `{}` if BEACON (port 6200) is not running or the target operator is not registered with CREW.
- The target must be running and registered. Check PRISM → Operators to verify.

---

### `crew_register(manifest)`

Register this operator with CREW on startup.

**Signature:**
```python
def crew_register(manifest: Dict[str, Any]) -> bool
```

| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| `manifest` | `dict` | yes | Parsed contents of the operator's `manifest.json` |

**Returns:** `bool` — `True` on successful registration, `False` on failure.

**Non-fatal:** Registration failure is not fatal. The operator continues running unregistered, but will not appear in PRISM and cannot be routed to by BEACON.

**Example:**
```python
import json
from pathlib import Path
from cascadia.sdk import crew_register

MANIFEST = json.loads(Path('manifest.json').read_text())

def startup():
    ok = crew_register(MANIFEST)
    if not ok:
        print('CREW registration failed — running standalone')
```

**Fields read from manifest:**
`id`, `type`, `autonomy_level`, `capabilities`, `health_hook`, `version`, `name`

**Common errors:**
- Call `crew_register` once at startup, not on every request.
- CREW runs on port 5100. If it is not running, registration returns `False` silently.

---

## Confidence and Self-Escalation

Operators can self-report confidence to trigger automatic human review:

```python
return {
    'output': your_result,
    'confidence': 0.87,          # float 0.0–1.0
    'escalate_if_below': 0.80,   # insert approval gate if confidence < this
    'escalation_reason': 'Complex case detected — please review',
}
```

When `confidence < escalate_if_below`, Cascadia OS inserts an approval gate. The human sees the output, confidence score, and reason. They can approve to continue or reject to halt.

---

## Copy the operator template

```bash
# Standard operator
cp -r sdk/operator_template my_operators/my_operator

# IoT sensor operator
cp -r sdk/iot_template my_operators/my_iot_operator
```

## Validate your manifest

```bash
python sdk/validator/validate_manifest.py my_operators/my_operator/
```

## Submit to DEPOT

1. Pass the manifest validator — all checks must pass
2. Test end-to-end on a live Cascadia OS instance
3. Set `depot_price_usd` and `depot_category` in `manifest.json`
4. Submit at: depot.zyrcon.ai

First $25,000 in DEPOT sales: 100% yours. Above $25,000 lifetime: 80% yours / 20% Zyrcon.

---

*Source: `cascadia/sdk/client.py` (pip) and `sdk/cascadia_sdk.py` (standalone template)*
