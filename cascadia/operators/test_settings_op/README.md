# test-settings-op

**For testing only. Not for production. Not listed in DEPOT.**

A fake operator that exercises every field type in the settings engine:
`string`, `boolean`, `select`, `number`, `secret`, `developer_mode`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/health` | Standard health check |
| `GET`  | `/api/settings/echo` | Returns current settings from settings engine |
| `POST` | `/api/run` | Echoes received body + current settings |

## Usage

```bash
# Start the test operator
SETTINGS_DB_PATH=data/settings.db python cascadia/operators/test_settings_op/operator.py

# Health check
curl http://localhost:8999/api/health

# Echo current settings
curl http://localhost:8999/api/settings/echo
```

## Field coverage

| Field | Type | Mode |
|-------|------|------|
| `business_name` | string (required) | simple |
| `lead_source` | select | simple |
| `ask_before_sending` | boolean (gates email.send) | simple |
| `api_key` | secret → VAULT | all |
| `retry_limit` | number (min/max) | advanced |
| `raw_prompt_override` | string | developer |
