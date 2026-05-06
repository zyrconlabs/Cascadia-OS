# DEPOT Manifest Schema

Every operator and connector published to the Zyrcon DEPOT must include a `manifest.json`
file in its package root. This file describes the item to the marketplace, installer, and
license gate.

---

## Required Fields

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique slug — lowercase, alphanumeric, hyphens or underscores only |
| `name` | string | Human-readable display name |
| `type` | string | `"operator"`, `"connector"`, or `"orchestrator"` |
| `version` | string | Semantic version — `"1.0.0"` format |
| `description` | string | One sentence shown in the catalog (≤280 chars) |
| `author` | string | Developer or organization name |
| `price` | number | USD price (0 for free) |
| `tier_required` | string | `"lite"`, `"pro"`, `"business"`, or `"enterprise"` |
| `port` | integer | Port this item listens on (8100–9999) |
| `entry_point` | string | File to execute — e.g. `"operator.py"` |
| `dependencies` | array | Python packages required — e.g. `["nats-py", "httpx"]` |
| `install_hook` | string | Shell script run on install — e.g. `"install.sh"` |
| `uninstall_hook` | string | Shell script run on uninstall — e.g. `"uninstall.sh"` |
| `category` | string | See valid categories below |
| `industries` | array | Target industries — e.g. `["construction", "general"]` |
| `installed_by_default` | boolean | Must be `false` for all DEPOT items |
| `safe_to_uninstall` | boolean | Whether uninstall can proceed without manual steps |

---

## Optional Fields

| Field | Type | Description |
|---|---|---|
| `icon` | string | Filename of icon in package — e.g. `"icon.png"` |
| `approval_required` | boolean | Operator requires human approval before actions |
| `approval_required_for_writes` | boolean | Connector requires approval before any write |
| `quality_level` | string | Operator quality tier — see valid values below. Default: `"apprentice"` |
| `nats_subjects` | array | NATS subjects this item subscribes to |
| `auth_type` | string | Connector auth method — see valid values below |
| `screenshots` | array | Filenames of screenshots in package |
| `readme` | string | Filename of README — defaults to `"README.md"` |
| `changelog` | string | Filename of changelog |
| `homepage_url` | string | Developer homepage |
| `support_email` | string | Support contact email |

---

## Valid Values

**type:** `operator`, `connector`, `orchestrator`

**tier_required:** `lite`, `pro`, `business`, `enterprise`

> **Canonical field name is `tier_required`.** The alias `tier` is accepted for backwards
> compatibility (present in some older first-party connectors) but `tier_required` is the
> standard for all new manifests. The manifest validator and CREW registry both prefer
> `tier_required`.

**quality_level:** `apprentice`, `professional`, `advanced`

Describes the operator's maturity and capability level. Displayed as a badge in the PRISM
operator catalog. Validated by `manifest_schema` at registration time — invalid values are
rejected.

| Value | Meaning |
|-------|---------|
| `apprentice` | Basic automation, handles common cases, may need human guidance |
| `professional` | Production-ready, handles edge cases, minimal supervision |
| `advanced` | Expert-level, handles complex multi-step scenarios autonomously |

Default: `"apprentice"` (applied automatically if the field is absent).

**category:**
`sales`, `marketing`, `support`, `finance`, `operations`, `devops`,
`ecommerce`, `data`, `hr`, `industry`, `communication`, `productivity`,
`iot`, `legal`, `integration`, `analytics`, `identity`, `runtime`

**auth_type** (connectors only):
`oauth2`, `api_key`, `bearer`, `basic`, `hmac`, `iam`, `service_account`, `signed_token`, `none`

---

## Example — Operator

```json
{
  "id": "lead-intake",
  "name": "Lead Intake Operator",
  "type": "operator",
  "version": "1.0.0",
  "description": "Normalizes leads from web forms, email, and CSV imports. Deduplicates before writing to CRM.",
  "author": "Zyrcon Labs",
  "price": 0,
  "tier_required": "enterprise",
  "port": 8101,
  "entry_point": "operator.py",
  "dependencies": ["nats-py"],
  "install_hook": "install.sh",
  "uninstall_hook": "uninstall.sh",
  "category": "sales",
  "industries": ["general"],
  "installed_by_default": false,
  "safe_to_uninstall": true,
  "approval_required": true,
  "nats_subjects": ["cascadia.operators.lead-intake.>"]
}
```

## Example — Connector

```json
{
  "id": "salesforce",
  "name": "Salesforce Connector",
  "type": "connector",
  "version": "1.0.0",
  "description": "Connects Cascadia OS to Salesforce CRM via OAuth2. Supports lead, contact, opportunity, and task CRUD.",
  "author": "Zyrcon Labs",
  "price": 0,
  "tier_required": "pro",
  "port": 9400,
  "entry_point": "connector.py",
  "dependencies": ["nats-py"],
  "install_hook": "install.sh",
  "uninstall_hook": "uninstall.sh",
  "category": "sales",
  "industries": ["general"],
  "installed_by_default": false,
  "safe_to_uninstall": true,
  "auth_type": "oauth2",
  "approval_required_for_writes": true,
  "nats_subjects": ["cascadia.connectors.salesforce.>"]
}
```

---

## Validation

Run the validator against any manifest:

```bash
python -m cascadia.depot.manifest_validator path/to/manifest.json
```

Or from Python:

```python
from cascadia.depot.manifest_validator import validate_depot_manifest_file

result = validate_depot_manifest_file("manifest.json")
if not result.valid:
    for err in result.errors:
        print(f"ERROR: {err}")
```

---

## Port Allocation

| Range | Category |
|---|---|
| 8101–8200 | Sales & CRM operators |
| 8201–8300 | Marketing & Content operators |
| 8301–8400 | Support & Customer Success operators |
| 8401–8500 | Finance & Accounting operators |
| 8501–8600 | Operations & Admin operators |
| 8601–8700 | IT DevOps & Security operators |
| 8701–8800 | E-commerce & Inventory operators |
| 8801–8900 | Data Analytics & AI operators |
| 8901–9000 | HR & Recruiting operators |
| 9100–9300 | Industry & Local Business operators |
| 9400–9499 | Sales CRM Support connectors |
| 9500–9599 | Communication connectors |
| 9600–9699 | Productivity & Knowledge connectors |
| 9700–9799 | Commerce, Payments & Finance connectors |
| 9800–9899 | Cloud, Data & Dev connectors |
| 9900–9949 | Analytics & Ads connectors |
| 9950–9979 | Identity, HR & Security connectors |
| 9980–9999 | Core Runtime Bridge connectors |
