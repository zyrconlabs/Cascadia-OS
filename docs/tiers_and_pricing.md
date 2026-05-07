# Tiers and Pricing Reference

Single source of truth for Cascadia OS product tiers, operator limits, DEPOT revenue model, and tier enforcement at runtime. Pricing figures not found in source files are marked "see COMMERCIAL.md."

---

## Product Tiers

Cascadia OS uses a four-tier license model enforced at runtime by the License Gate (port 6100). License keys follow the format:

```
ZYRCON-(LITE|PRO|BUSINESS|ENTERPRISE)-[16-character hex]
```

### Zyrcon Lite

- **Price:** see COMMERCIAL.md
- **Operator limit:** 2 concurrent operators
- **Target:** Individuals, solopreneurs, trial deployments
- **DEPOT access:** Operators and connectors with `tier_required: "lite"`

### Zyrcon Pro

- **Price:** see COMMERCIAL.md
- **Operator limit:** 6 concurrent operators
- **Target:** Small teams, growing businesses
- **DEPOT access:** Operators and connectors with `tier_required: "lite"` or `"pro"`

### Zyrcon Business

- **Price:** see COMMERCIAL.md
- **Operator limit:** 12 concurrent operators
- **Target:** Established businesses, multi-department deployments
- **DEPOT access:** All tiers up to `"business"`

### Zyrcon Enterprise

- **Price:** see COMMERCIAL.md
- **Operator limit:** 999 (effectively unlimited)
- **Target:** Enterprise deployments, OEM partners, resellers
- **DEPOT access:** All tiers including `"enterprise"`

Operator limits are enforced by `cascadia/licensing/license_gate.py`:

```python
OPERATOR_LIMITS: Dict[str, int] = {
    'lite':       2,
    'pro':        6,
    'business':   12,
    'enterprise': 999,
}
```

---

## Manifest tier_required Values

The `tier_required` field in `manifest.json` declares the minimum tier a user must have to install and run the operator or connector.

**Valid values:** `"lite"` | `"pro"` | `"business"` | `"enterprise"`

**Canonical field name is `tier_required`.** Do not use the alias `tier` in new manifests.

### Runtime enforcement

Tier ranks are ordered: `lite < pro < business < enterprise`

When a user attempts to install an operator:

1. The License Gate returns the user's current tier.
2. PRISM and the DEPOT installer compare the operator's `tier_required` against the user's tier rank.
3. If the user's rank is below the required rank, installation is blocked and the user is directed to `zyrcon.store`.

From `cascadia/dashboard/prism.py`:

```python
_TIER_RANKS = {'lite': 0, 'pro': 1, 'business': 2, 'enterprise': 3}
```

A `lite` user cannot install a `pro` operator. A `business` user can install any operator with `tier_required` of `lite`, `pro`, or `business`.

### Choosing tier_required for your operator

| If your operator requires... | Set tier_required to... |
|------------------------------|------------------------|
| No external credentials, local LLM only | `"lite"` |
| One simple API connector (messaging, webhook) | `"lite"` |
| A SaaS integration with auth (CRM, billing) | `"pro"` |
| Multiple connectors or enterprise SaaS | `"business"` |
| Legal, financial, healthcare, or regulated data | `"enterprise"` |
| High-risk operations (payment processing, HR records) | `"enterprise"` |

When in doubt, choose the lowest tier that makes sense. Operators with high `tier_required` have a smaller addressable market on DEPOT.

---

## DEPOT Revenue Model

Operators and connectors published to the Zyrcon DEPOT earn revenue according to this schedule:

| Lifetime revenue from this item | Developer share |
|---------------------------------|----------------|
| First $25,000 | **100%** to developer |
| Above $25,000 | **80%** developer / **20%** Zyrcon Labs |

Sources: `sdk/README.md` and `docs/connectors.md`.

Free operators and connectors (`"price": 0`) are always 100% to the developer — the revenue share applies to paid items only.

Revenue is calculated per operator/connector, not per developer account. Each item resets to $0 toward the $25,000 threshold independently.

---

## Business Pilot

see COMMERCIAL.md for current Business Pilot terms, setup fees, and monthly rates.

---

## Feature Access by Tier

Features confirmed in source code. Cells marked "see COMMERCIAL.md" indicate the feature exists but specific limits or availability were not found in source files.

| Feature | Lite | Pro | Business | Enterprise |
|---------|:----:|:---:|:--------:|:----------:|
| Concurrent operators | 2 | 6 | 12 | 999 |
| PRISM dashboard | ✓ | ✓ | ✓ | ✓ |
| Approval gate | ✓ | ✓ | ✓ | ✓ |
| DEPOT operator install | ✓ lite only | ✓ lite+pro | ✓ lite+pro+biz | ✓ all |
| VANTAGE gateway | ✓ | ✓ | ✓ | ✓ |
| VAULT + CURTAIN | ✓ | ✓ | ✓ | ✓ |
| Workflow Designer | see COMMERCIAL.md | see COMMERCIAL.md | see COMMERCIAL.md | see COMMERCIAL.md |
| IoT / CONDUIT | see COMMERCIAL.md | see COMMERCIAL.md | ✓ | ✓ |
| Actuator control | ✗ | ✗ | ✗ | ✓ |
| Fleet management | see COMMERCIAL.md | see COMMERCIAL.md | see COMMERCIAL.md | ✓ |
| OEM / white-label | ✗ | ✗ | ✗ | ✓ |
| Support level | Community | see COMMERCIAL.md | see COMMERCIAL.md | Dedicated |
| Audit log retention | 30 days | 90 days | 365 days | 365 days |

Audit log retention periods are sourced from `cascadia/dashboard/prism.py`:

```python
days = {'lite': 30, 'pro': 90, 'business': 365, 'enterprise': 365}.get(tier, 30)
```

---

## For Operator Developers

### Choosing tier_required

Use the decision table in the Manifest tier_required Values section above. The most common choices:

- **Lite** — messaging connectors, webhook connectors, simple lookup operators
- **Pro** — CRM integrations, email operators, calendar operators
- **Business** — multi-system orchestration, field service, legal/accounting connectors
- **Enterprise** — HR, payment processing, regulated industry operators

### Setting your price

Set `"price": 0` for free operators. Set a non-zero USD price for paid operators. The DEPOT review bot validates that `price` is a non-negative number.

Free connectors still qualify for the DEPOT program and count toward your developer account history.

### Upgrade prompt

When a user tries to install your operator and their tier is insufficient, PRISM automatically displays an upgrade prompt pointing to `zyrcon.store`. You do not need to handle this in your operator code.
