# Workflow Templates

Pre-built workflow definitions for Business tier customers. Each template defines an operator chain with approval gates, configuration, and ROI context.

## What is a template?

A template is a JSON file that describes a complete automated workflow — which operators run, in what order, what each one does, and which steps require your approval before proceeding.

Templates are starting points. Load one during onboarding and customize it to match your exact process.

## Available templates

| Template | Industry | Tier Required | Time Saved |
|---|---|---|---|
| `construction.json` | Construction | business_starter | ~15 hrs/mo |
| `hvac.json` | HVAC | business_starter | ~12 hrs/mo |
| `medical.json` | Medical practice | business_starter | ~20 hrs/mo |
| `legal.json` | Law firm | business_starter | ~10 hrs/mo |

## How to load a template in PRISM

1. Open PRISM dashboard at `http://localhost:6300`
2. Go to **Workflows → Templates**
3. Select a template and click **Load**
4. Review the operator chain and adjust any configuration
5. Click **Activate** to enable the workflow

## How to customize a template

Each template is a plain JSON file. You can edit:

- `operators[].config` — change fields, add criteria, adjust thresholds
- `operators[].approval_required` — add or remove approval gates per step
- `operators[].approval_message` — customize the message you see when approving

After editing, reload the template in PRISM or restart the workflow.

## Template format

```json
{
  "id": "unique_workflow_id",
  "name": "Human-readable name",
  "description": "What this workflow does",
  "industry": "construction | hvac | medical | legal | ...",
  "tier_required": "business_starter",
  "operators": [
    {
      "step": 1,
      "operator": "SCOUT",
      "action": "action_name",
      "trigger": "webhook",
      "approval_required": false,
      "config": {}
    }
  ],
  "estimated_time_saved_hours_per_month": 15,
  "roi_basis": "Context for calculating ROI."
}
```

## How to create a new template

1. Copy an existing template as a starting point
2. Set a unique `id` (snake_case)
3. Define your operator chain in `operators[]`
4. Set `approval_required: true` for any step where you want to review before proceeding
5. Save the file to this `templates/` folder
6. Load it in PRISM

## Operators available in templates

| Operator | Purpose |
|---|---|
| SCOUT | Lead qualification, intake screening |
| RECON | Research — company, person, case background |
| QUOTE | Proposal and estimate drafting |
| EMAIL | Outbound email with approval gate |
| CHIEF | Review and decision coordination |
| AURELIA | Scheduling and calendar management |
| SENTINEL | Approval gates and compliance checks |
