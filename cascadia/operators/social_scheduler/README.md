# Social Post Scheduler (C8)

**ID:** social-scheduler | **Port:** 8108 | **Tier:** lite

Queue and schedule social media posts across Twitter, LinkedIn, Facebook, and Instagram. Publishing actions are approval-gated.

## Actions

| Action | Approval Required | Description |
|---|---|---|
| `create_post` | No | Queue a post for a platform |
| `list_posts` | No | List posts, filterable by platform/status |
| `cancel_post` | No | Cancel a scheduled post |
| `get_post` | No | Retrieve a single post |
| `publish_post` | **Yes** | Immediately publish a specific post |
| `publish_scheduled` | **Yes** | Publish all posts that are due |

## Supported Platforms

`twitter`, `linkedin`, `facebook`, `instagram`

## NATS Subjects

- Call: `cascadia.operators.social-scheduler.call`
- Response: `cascadia.operators.social-scheduler.response`
- Approvals: `cascadia.approvals.request`

## Example Payloads

```json
{ "action": "create_post", "params": { "platform": "linkedin", "content": "Hello world!", "scheduled_at": "2026-05-01T09:00:00Z", "tags": ["launch"] } }
{ "action": "list_posts", "params": { "platform": "linkedin", "status": "scheduled" } }
{ "action": "publish_post", "params": { "post_id": "<id>" } }
```

## Health

```
GET http://localhost:8108/health
→ {"status":"ok","operator":"social-scheduler","version":"1.0.0","port":8108}
```

## Running

```bash
bash install.sh
python3 operator.py
```
