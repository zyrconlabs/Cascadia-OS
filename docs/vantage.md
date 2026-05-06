# VANTAGE Capability Gateway

VANTAGE is the runtime enforcement gateway that sits between operators and connectors. Every connector call made by an operator passes through VANTAGE. It ensures operators can only use capabilities they have declared, and that high-risk capabilities are reviewed by SENTINEL before execution.

Port: **6208**

---

## What It Is

Capability-gated means that an operator must explicitly declare in its manifest every capability it intends to use. VANTAGE enforces that declaration at runtime — an operator cannot call a connector capability it did not declare, even if it knows the connector's port and path.

This matters for compliance and auditability. Every connector call is logged to AuditLog with the operator identity, capability, risk level, and verdict. High-risk capabilities cannot be called without SENTINEL approval.

### Where VANTAGE Fits in the Stack

```
Operator → POST /call → VANTAGE (6208)
                            │
                    Step 1: CREW (5100) — was this capability declared?
                            │
                    Step 2: entitlements.py — what is the risk level?
                            │
                    Step 3: SENTINEL (5102) — gate if high or critical
                            │
                    Step 4: Forward to connector at connector_port
```

VANTAGE depends on CREW and SENTINEL. Both must be healthy before VANTAGE starts.

---

## How Operators Declare Capabilities

Operators declare required capabilities in their `manifest.json` using the `capabilities` field, and required connectors using the `requires_connectors` field.

```json
{
  "id": "lead_intake",
  "name": "Lead Intake Operator",
  "type": "service",
  "capabilities": ["crm.write", "email.send", "message.send"],
  "requires_connectors": ["slack", "salesforce"],
  "autonomy_level": "semi_autonomous"
}
```

At runtime, when the operator calls VANTAGE:

```json
POST http://localhost:6208/call

{
  "operator_id": "lead_intake",
  "capability": "crm.write",
  "connector_port": 9400,
  "connector_path": "/api/call",
  "payload": {"action": "create_lead", "data": {"name": "Acme Corp"}},
  "autonomy_level": "semi_autonomous",
  "run_id": "run_abc123"
}
```

VANTAGE checks that `crm.write` is in `lead_intake`'s declared capabilities. If it is not, the call is blocked with HTTP 403. If it is, VANTAGE resolves the risk level (`medium` for `crm.write`) and forwards the call.

The `requires_connectors` field is a declaration of intent recorded in the CREW registry. It does not grant capability — `capabilities` does. Use `requires_connectors` to document connector dependencies so operators and PRISM can surface them.

---

## Configuration

In `config.example.json`, VANTAGE is declared as a component:

```json
{
  "name": "vantage",
  "module": "cascadia.gateway.vantage",
  "port": 6208,
  "tier": 2,
  "pulse_file": "./data/runtime/vantage.pulse",
  "depends_on": ["crew", "sentinel"]
}
```

`tier: 2` means VANTAGE starts after tier-1 components (VAULT, SENTINEL, CREW) and before tier-3 components (PRISM, ALMANAC).

---

## Health Check

```bash
curl http://localhost:6208/api/health
```

Expected response:

```json
{
  "service": "vantage",
  "status": "running",
  "port": 6208,
  "calls_total": 142,
  "calls_blocked": 3
}
```

`calls_blocked` counts calls rejected for any reason: undeclared capability, SENTINEL denial, or SENTINEL unavailability.

---

## API Routes

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/call` | Route a capability call through enforcement |
| `GET` | `/api/health` | Health and call statistics |
| `GET` | `/api/registry` | Current CREW operator registry (proxied) |
| `GET` | `/api/capabilities/{operator_id}` | Capabilities declared by a specific operator |
| `POST` | `/api/simulate` | Dry-run a capability call without forwarding to connector |

---

## Capability Risk Levels

Risk levels are defined in `cascadia/shared/entitlements.py`. VANTAGE uses these to decide whether SENTINEL review is required:

| Risk Level | SENTINEL Required | Examples |
|------------|:-----------------:|---------|
| `low` | No | `crm.read`, `email.read`, `file.read` |
| `medium` | No | `email.send`, `crm.write`, `file.write` |
| `high` | **Yes** | `billing.write`, `email.delete`, `crm.delete` |
| `critical` | **Yes** | `shell.exec`, `vault.write`, `system.destroy` |

For `high` and `critical` capabilities, VANTAGE calls SENTINEL before forwarding. If SENTINEL is unreachable, the call is blocked — VANTAGE does not fail open.

---

## Tier Access Rules

Tier-based access rules are not yet implemented. VANTAGE currently validates capability declaration (operator must have declared the capability) and risk level (high/critical routes through SENTINEL). It does not inspect the user's subscription tier.

Tier enforcement is planned in a future release.

---

## Example: Adding Capability Requirements

**Step 1 — Declare in manifest:**

```json
{
  "id": "invoice_sender",
  "capabilities": ["email.send", "invoice.create"],
  "requires_connectors": ["google-accounts"]
}
```

**Step 2 — Operator calls VANTAGE:**

```python
# In your operator — call VANTAGE, not the connector directly
import requests

resp = requests.post("http://localhost:6208/call", json={
    "operator_id": "invoice_sender",
    "capability": "invoice.create",
    "connector_port": 9020,
    "connector_path": "/api/invoice/create",
    "payload": {"customer_id": "cust_123", "amount": 1500.00},
    "autonomy_level": "semi_autonomous",
    "run_id": run_id,
})
```

**Step 3 — VANTAGE enforces:**

- CREW confirms `invoice.create` is in `invoice_sender`'s manifest → allowed
- `invoice.create` has risk level `high` → SENTINEL is called
- SENTINEL checks `autonomy_level` and configured thresholds → verdict returned
- If allowed, call is forwarded to connector on port 9020
- All steps logged to AuditLog

**Step 4 — VANTAGE response:**

```json
{
  "verdict": "allowed",
  "operator_id": "invoice_sender",
  "capability": "invoice.create",
  "risk_level": "high",
  "connector_port": 9020,
  "latency_ms": 84.3,
  "connector_response": { ... }
}
```

If blocked, the response includes `"verdict": "blocked"` and a `"reason"` string.

---

## SDK Helper

If you use `cascadia_sdk.py`, the `beacon_route()` function handles VANTAGE routing transparently. Direct VANTAGE calls are only needed for low-level operator implementations.

```python
from cascadia_sdk import beacon_route

result = beacon_route(
    task="invoice.create",
    payload={"customer_id": "cust_123", "amount": 1500.00}
)
```

---

*VANTAGE is part of Cascadia OS Core (Apache 2.0).*
*Source: `cascadia/gateway/vantage.py`*
