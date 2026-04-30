# Budget Tracker (C7)

**ID:** budget-tracker | **Port:** 8107 | **Tier:** lite

Track project and department budgets, log expenses, and generate spend reports. All state is in-memory; persistence is handled by the Cascadia OS state layer.

## Actions

| Action | Approval Required | Description |
|---|---|---|
| `create_budget` | No | Create a named budget with total and currency |
| `log_expense` | No | Record an expense against a budget |
| `get_budget` | No | Retrieve a budget with remaining + pct_used |
| `list_budgets` | No | List all budgets, optionally filtered by category |
| `generate_report` | No | Summary report with top vendors and totals |
| `export_report` | **Yes** | Write CSV report to disk |

## NATS Subjects

- Call: `cascadia.operators.budget-tracker.call`
- Response: `cascadia.operators.budget-tracker.response`
- Approvals: `cascadia.approvals.request`

## Example Payloads

```json
{ "action": "create_budget", "params": { "name": "Q2 Marketing", "total": 50000, "currency": "USD", "category": "marketing" } }
{ "action": "log_expense", "params": { "budget_id": "<id>", "amount": 1200, "description": "Ad spend", "vendor": "Google" } }
{ "action": "generate_report", "params": {} }
```

## Health

```
GET http://localhost:8107/health
→ {"status":"ok","operator":"budget-tracker","version":"1.0.0","port":8107}
```

## Running

```bash
bash install.sh
python3 operator.py
```
