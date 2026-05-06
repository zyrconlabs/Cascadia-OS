# setup_fields Reference

`setup_fields` is the array in an operator or connector manifest that defines configuration fields shown in the Onboarding Wizard and Settings UI. Each entry maps to a single input the user fills in when installing or configuring the operator.

---

## When to Use setup_fields

**Use setup_fields when your operator or connector requires credentials, identifiers, or user-configurable behavior.**

**Do NOT use setup_fields for:**
- Fields the operator resolves itself at runtime (e.g., internal ports from config.json)
- Connector credentials when your operator is connector-dependent — list the connector in `requires_connectors` instead; the connector manages its own credentials
- Feature flags that belong in the operator's own logic

```json
// LOCAL_LLM_ONLY operator — no external credentials needed
"setup_fields": []

// CONNECTOR_DEPENDENT operator — declare the connector, not its credentials
"requires_connectors": ["slack", "salesforce"],
"setup_fields": []

// DIRECT_INTEGRATION — operator calls an external API itself
"setup_fields": [ ... ]
```

---

## Field Schema

Every entry in `setup_fields` is a JSON object. The validator (`cascadia.shared.manifest_schema`) enforces these rules:

| Key | Type | Required | Description |
|-----|------|:--------:|-------------|
| `name` | string | **Yes** | Internal key — used to retrieve the value in operator code |
| `label` | string | **Yes** | Display label shown in the UI |
| `type` | string | **Yes** | One of the valid types listed below |
| `required` | boolean | No | Default `false`. If `true`, setup cannot proceed without this field |
| `default` | any | No | Pre-filled value; `null` means no default |
| `help_text` | string | No | One-line hint shown below the field in the UI |
| `placeholder` | string | No | Input placeholder text |
| `options` | array | No | Required for `select` type — list of allowed values |
| `min` | integer | No | Minimum value for `number` fields |
| `max` | integer | No | Maximum value for `number` fields |
| `pattern` | string | No | Regex validation pattern for `string` fields |
| `secret` | boolean | No | Default `false`. If `true`, value is masked in the UI and must have `vault_key` |
| `vault_key` | string | No | `"connector_id:field_name"` — stores value in VAULT instead of plain config |
| `simple_mode` | boolean | No | Default `true`. Field is shown in the basic setup flow |
| `advanced_mode` | boolean | No | Default `false`. Field is shown only in advanced setup |
| `developer_mode` | boolean | No | Default `false`. Field is shown only in developer mode |
| `affects_permissions` | array | No | Capability strings whose approval status this field affects |
| `requires_approval_if_enabled` | array | No | If this toggle is `true`, adds approval requirements for these capabilities |

### Valid Types

| Type | Description |
|------|-------------|
| `string` | Single-line text input |
| `secret` | Masked text input — value stored in VAULT, never shown after entry |
| `boolean` | Toggle switch |
| `select` | Dropdown — requires `options` array |
| `number` | Numeric input — supports `min` / `max` |
| `slider` | Range slider — requires `min` and `max` |
| `tags` | Multi-value tag input |

---

## Pattern 1 — LOCAL_LLM_ONLY

An operator that works entirely against the local LLM and runtime, with no external API calls.

```json
{
  "id": "document_summarizer",
  "name": "Document Summarizer",
  "type": "skill",
  "requires_connectors": [],
  "setup_fields": []
}
```

No setup required. The user installs and it works immediately.

---

## Pattern 2 — DIRECT_INTEGRATION with Credentials

An operator that calls an external API directly and stores the credentials in VAULT.

```json
{
  "id": "shopify_monitor",
  "name": "Shopify Monitor",
  "type": "service",
  "requires_connectors": [],
  "setup_fields": [
    {
      "name": "api_key",
      "label": "Shopify API Key",
      "type": "secret",
      "required": true,
      "secret": true,
      "vault_key": "shopify_monitor:api_key",
      "simple_mode": true,
      "help_text": "From your Shopify Partners dashboard under API credentials"
    },
    {
      "name": "shop_domain",
      "label": "Shop Domain",
      "type": "string",
      "required": true,
      "simple_mode": true,
      "placeholder": "yourstore.myshopify.com",
      "help_text": "Your Shopify store subdomain"
    },
    {
      "name": "check_interval_minutes",
      "label": "Check interval (minutes)",
      "type": "number",
      "required": false,
      "default": 15,
      "min": 5,
      "max": 60,
      "simple_mode": false,
      "advanced_mode": true,
      "help_text": "How often to poll for new orders"
    }
  ]
}
```

**Key points:**
- `api_key` has `"secret": true` and a `vault_key` — value is stored in VAULT and never returned to the UI after initial entry
- `shop_domain` is a plain string — no VAULT storage needed
- `check_interval_minutes` is `advanced_mode: true` so it only appears when the user expands advanced settings

In operator code, retrieve the vault value using:

```python
from cascadia_sdk import vault_get

api_key = vault_get('shopify_monitor:api_key')
```

---

## Pattern 3 — Advanced (approval-binding)

A connector that stores OAuth credentials in VAULT and binds certain field values to approval requirements. Based on the live Clio connector.

```json
{
  "id": "clio",
  "name": "Clio",
  "type": "connector",
  "tier_required": "pro",
  "auth_type": "oauth2",
  "setup_fields": [
    {
      "name": "client_id",
      "label": "Clio Client ID",
      "type": "secret",
      "required": true,
      "secret": true,
      "vault_key": "clio:client_id",
      "simple_mode": true,
      "help_text": "From Clio Developer Portal"
    },
    {
      "name": "client_secret",
      "label": "Clio Client Secret",
      "type": "secret",
      "required": true,
      "secret": true,
      "vault_key": "clio:client_secret",
      "simple_mode": true,
      "help_text": "From Clio Developer Portal"
    },
    {
      "name": "region",
      "label": "Clio Region",
      "type": "select",
      "required": true,
      "default": "US",
      "simple_mode": true,
      "options": ["US", "CA", "EU", "AU"],
      "help_text": "Your Clio account region"
    },
    {
      "name": "approval_all_writes",
      "label": "Require approval for all legal record changes",
      "type": "boolean",
      "required": false,
      "default": true,
      "simple_mode": true,
      "help_text": "Legal data always requires owner approval before changes",
      "requires_approval_if_enabled": [
        "matter.create", "contact.create", "task.create", "time_entry.create"
      ]
    }
  ]
}
```

**Key points:**
- `vault_key` format is `"connector_id:field_name"` — the connector ID prefix namespaces secrets in VAULT
- `select` type requires the `options` array — the validator rejects select fields without options
- `requires_approval_if_enabled` on a `boolean` field means: if the user sets this toggle to `true`, SENTINEL approval gates are added for the listed capabilities. The Clio connector defaults this to `true` so legal records always require approval out of the box.

---

## Field Naming: tier_required vs. tier

The canonical field name is **`tier_required`**. This is what the manifest validator, CREW registry, and DEPOT schema expect.

The alias `tier` is present in some older connectors (clio, mindbody) for backwards compatibility but is not the standard. Use `tier_required` in all new manifests.

```json
// Correct
"tier_required": "pro"

// Accepted but non-standard — do not use in new manifests
"tier": "pro"
```

---

## Common Mistakes

**Adding setup_fields when the operator is connector-dependent.**
If your operator uses Slack through the Slack connector, list `"requires_connectors": ["slack"]` — do not re-declare `SLACK_BOT_TOKEN` in setup_fields. The user already set that up when installing the connector.

**Using `tier` instead of `tier_required`.**
Write `tier_required`. The alias works today but may be removed in a future release.

**Omitting `vault_key` on a secret field.**
If `"secret": true`, you must provide `"vault_key"`. Without it, the value has no storage destination and the field will not survive restarts. Format: `"connector_id:field_name"` or `"operator_id:field_name"`.

**Using a type not in the valid set.**
Valid types are: `string`, `secret`, `boolean`, `select`, `number`, `slider`, `tags`. The validator rejects manifests with any other type value. Do not use `text`, `password`, `email`, or `url` — use `string` or `secret` instead.

**Forgetting `options` on a `select` field.**
The validator will reject a `select` field with no `options` array.

---

## Running the Validator

```bash
python -m cascadia.depot.manifest_validator path/to/manifest.json
```

The validator will surface setup_fields errors with the field name and the specific problem.
