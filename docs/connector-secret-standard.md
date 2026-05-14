# Connector Secret Standard

How connector tokens are configured, stored, and loaded across the Cascadia OS stack.

---

## 1. How PRISM writes a connector secret to VAULT

Every connector exposes a configuration screen in PRISM. The screen is driven by `setup_fields` in the connector's `manifest.json`. Secret fields are identified by `"secret": true` and carry a `vault_key` that names the storage slot.

```json
// cascadia/connectors/telegram/manifest.json (excerpt)
{
  "setup_fields": [
    {
      "name": "bot_token",
      "label": "Telegram Bot Token",
      "type": "secret",
      "required": true,
      "secret": true,
      "vault_key": "telegram:bot_token"
    }
  ]
}
```

When the user saves the configuration, PRISM calls `settings/engine.py → save_patch()`, which writes the value to VAULT:

```python
# cascadia/settings/engine.py
self._vault.write(f.vault_key, value, created_by=source, namespace="secrets")
```

The value is encrypted at rest in `data/runtime/cascadia_vault.db`.

---

## 2. How a connector reads its secret at startup

Each connector implements a `_load_*()` function that checks three sources in order:

```
1. VAULT  — vault_get(manifest_vault_key, namespace="secrets")
2. env    — os.environ.get("CONNECTOR_TOKEN_ENV_VAR")
3. file   — <connector>.config.json (gitignored, local dev only)
```

The resolved token is stored in a module-level constant (`_BOT_TOKEN`, `_ACCESS_TOKEN`). It is loaded once at process startup. If all three sources are empty, the connector logs a WARNING and starts anyway — the token must be configured before any message is dispatched.

```python
# Example — cascadia/connectors/telegram/connector.py
def _load_bot_token() -> str:
    try:
        from cascadia_sdk import vault_get
        val = vault_get("telegram:bot_token", namespace="secrets")
        if val:
            return val
    except ImportError:
        pass
    val = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if val:
        return val
    cfg_path = Path(__file__).parent / "telegram.config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("bot_token", "")
    except Exception:
        return ""

_BOT_TOKEN: str = _load_bot_token()
```

`execute_call()` always reads from the module constant — never from the incoming NATS payload.

---

## 3. Adding a new connector

**Step 1** — Define the secret field in `manifest.json`:

```json
{
  "setup_fields": [
    {
      "name": "api_key",
      "label": "My Service API Key",
      "type": "secret",
      "required": true,
      "secret": true,
      "vault_key": "myservice:api_key",
      "simple_mode": true,
      "help_text": "From your MyService developer dashboard"
    }
  ]
}
```

Choose `vault_key` in the format `<service>:<field>`. This key is the contract between PRISM and the connector — both sides must use the same string.

**Step 2** — Implement `_load_token()` in `connector.py`:

```python
def _load_token() -> str:
    try:
        from cascadia_sdk import vault_get
        val = vault_get("myservice:api_key", namespace="secrets")
        if val:
            return val
    except ImportError:
        pass
    val = os.environ.get("MYSERVICE_API_KEY", "")
    if val:
        return val
    cfg_path = Path(__file__).parent / "myservice.config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("api_key", "")
    except Exception:
        return ""

_TOKEN: str = _load_token()
if not _TOKEN:
    log.warning("MYSERVICE_API_KEY not set — configure via PRISM, MYSERVICE_API_KEY env var, or myservice.config.json")
```

**Step 3** — Use `_TOKEN` in `execute_call()`, never the payload:

```python
def execute_call(payload: dict) -> dict:
    token = _TOKEN  # connector owns the token
    ...
```

**Step 4** — Add the env var name to `sdk/cascadia_sdk.py` `.env.example`:

```
MYSERVICE_API_KEY=
```

**Step 5** — Add a `myservice.config.json` entry to `.gitignore`.

---

## 4. What VANGUARD and CHIEF must never do

- **Never carry connector tokens in message payloads.** VANGUARD routes inbound channel events to CHIEF; CHIEF dispatches tasks via BEACON `/route`. Neither layer knows which connector will ultimately execute the action, and neither layer should hold credentials.

- **Never inject tokens into NATS messages or HTTP request bodies.** The connector is the only process that holds its own token. Any token appearing in a NATS subject payload or an HTTP request body is a misrouted secret.

If a connector needs a token that was not loaded at startup, it must fail the action and log an error — not accept the token from the caller.

---

## 5. Namespace reference

| Namespace | Used for |
|---|---|
| `default` | General config, non-secret operator data |
| `secrets` | Connector tokens, API keys, OAuth credentials |

The `namespace` parameter is passed explicitly to `vault_get()`. Existing callers that omit `namespace` continue to use `"default"` — no behavior change.

```python
# Reading a non-secret config value (default namespace):
vault_get("business_name")

# Reading a connector secret (secrets namespace):
vault_get("telegram:bot_token", namespace="secrets")
```

---

## Connector vault_key registry

| Connector | Env var fallback | vault_key | config.json key |
|---|---|---|---|
| Telegram | `TELEGRAM_BOT_TOKEN` | `telegram:bot_token` | `bot_token` |
| Slack | `SLACK_BOT_TOKEN` | `slack:bot_token` | `token` |
| Discord | `DISCORD_BOT_TOKEN` | `discord:bot_token` | `token` |
| WhatsApp | `WHATSAPP_ACCESS_TOKEN` | `whatsapp:api_key` | `access_token` |
