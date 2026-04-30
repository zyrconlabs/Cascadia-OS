# Review Requester

**ID:** review-requester | **Port:** 8104 | **Tier:** lite | **Category:** marketing

Send review request messages to customers after service completion. Campaigns are configurable per platform.

## Actions

| Action | Gate | Description |
|---|---|---|
| `create_campaign` | direct | Define a review campaign with platform, URL, and message template |
| `list_campaigns` | direct | List all campaigns |
| `queue_request` | direct | Add a customer to a campaign's pending queue |
| `list_pending` | direct | List pending review requests, optionally filtered by campaign |
| `send_requests` | approval | Send all pending requests for a campaign |
| `send_single` | approval | Send a single review request |

## NATS Subjects

- Subscribe: `cascadia.operators.review-requester.call`
- Respond: `cascadia.operators.review-requester.response`
- Approvals: `cascadia.approvals.request`

## Payload Examples

```json
{
  "action": "create_campaign",
  "name": "Google Reviews Q2",
  "platform": "google",
  "review_url": "https://g.page/r/my-business/review",
  "message_template": "Hi {customer_name}, we hope you enjoyed your experience! Please leave us a review: {review_url}"
}
```

```json
{
  "action": "queue_request",
  "campaign_id": "CAM-ABCDEF123456",
  "customer_email": "customer@example.com",
  "customer_name": "Sam Torres",
  "order_ref": "ORD-9912"
}
```

## Health

```
GET http://localhost:8104/health
→ {"status":"ok","operator":"review-requester","version":"1.0.0","port":8104}
```

## Install

```bash
bash install.sh
python3 operator.py
```
