"""
C8: Social Post Scheduler — Cascadia OS Operator
Port: 8108  Subject prefix: cascadia.operators.social-scheduler
"""

import asyncio
import json
import os
import uuid
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import nats

NAME = "social-scheduler"
VERSION = "1.0.0"
PORT = 8108
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")

CALL_SUBJECT = f"cascadia.operators.{NAME}.call"
RESPONSE_SUBJECT = f"cascadia.operators.{NAME}.response"
APPROVALS_SUBJECT = "cascadia.approvals.request"

VALID_PLATFORMS = {"twitter", "linkedin", "facebook", "instagram"}

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

posts: dict = {}  # post_id → {platform, content, scheduled_at, status, tags}

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def create_post(platform: str, content: str, scheduled_at: str,
                tags: list = None) -> dict:
    platform = platform.lower()
    if platform not in VALID_PLATFORMS:
        return {
            "error": f"Invalid platform '{platform}'. "
                     f"Valid: {sorted(VALID_PLATFORMS)}"
        }
    if not content.strip():
        return {"error": "content cannot be empty"}

    post_id = str(uuid.uuid4())
    posts[post_id] = {
        "post_id": post_id,
        "platform": platform,
        "content": content,
        "scheduled_at": scheduled_at,
        "status": "scheduled",
        "tags": tags or [],
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    return {"post_id": post_id, **posts[post_id]}


def list_posts(platform: str = None, status: str = None) -> dict:
    result = []
    for p in posts.values():
        if platform and p["platform"] != platform.lower():
            continue
        if status and p["status"] != status.lower():
            continue
        result.append(dict(p))
    result.sort(key=lambda x: x.get("scheduled_at", ""))
    return {"posts": result, "count": len(result)}


def cancel_post(post_id: str) -> dict:
    if post_id not in posts:
        return {"error": f"Post not found: {post_id}"}
    if posts[post_id]["status"] in ("published", "cancelled"):
        return {
            "error": f"Cannot cancel post with status '{posts[post_id]['status']}'"
        }
    posts[post_id]["status"] = "cancelled"
    posts[post_id]["updated_at"] = datetime.utcnow().isoformat()
    return {"post_id": post_id, "status": "cancelled"}


def get_post(post_id: str) -> dict:
    if post_id not in posts:
        return {"error": f"Post not found: {post_id}"}
    return dict(posts[post_id])


def _mark_published(post_id: str) -> dict:
    """Internal helper — mark a post as published."""
    if post_id not in posts:
        return {"error": f"Post not found: {post_id}"}
    posts[post_id]["status"] = "published"
    posts[post_id]["published_at"] = datetime.utcnow().isoformat()
    posts[post_id]["updated_at"] = datetime.utcnow().isoformat()
    return dict(posts[post_id])


def _due_posts() -> list:
    """Return post_ids that are scheduled and due now or in the past."""
    now = datetime.now(timezone.utc).isoformat()
    return [
        pid for pid, p in posts.items()
        if p["status"] == "scheduled" and p["scheduled_at"] <= now
    ]


# ---------------------------------------------------------------------------
# Approval-gated actions
# ---------------------------------------------------------------------------

async def _request_approval(nc, action: str, payload: dict) -> dict:
    request_id = str(uuid.uuid4())
    envelope = {
        "request_id": request_id,
        "operator": NAME,
        "action": action,
        "payload": payload,
        "requested_at": datetime.utcnow().isoformat(),
    }
    await nc.publish(APPROVALS_SUBJECT, json.dumps(envelope).encode())
    return {"status": "pending_approval", "request_id": request_id, "action": action}


# ---------------------------------------------------------------------------
# Task dispatcher
# ---------------------------------------------------------------------------

def execute_task(payload: dict) -> dict:
    action = payload.get("action", "")
    params = payload.get("params", {})

    if action == "create_post":
        return create_post(
            platform=params.get("platform", ""),
            content=params.get("content", ""),
            scheduled_at=params.get("scheduled_at", datetime.utcnow().isoformat()),
            tags=params.get("tags", []),
        )
    elif action == "list_posts":
        return list_posts(
            platform=params.get("platform"),
            status=params.get("status"),
        )
    elif action == "cancel_post":
        return cancel_post(params.get("post_id", ""))

    elif action == "get_post":
        return get_post(params.get("post_id", ""))

    elif action in ("publish_post", "publish_scheduled"):
        return {"error": f"{action} requires async context; use handle_event"}

    else:
        return {"error": f"Unknown action: {action}"}


# ---------------------------------------------------------------------------
# NATS handler
# ---------------------------------------------------------------------------

async def handle_event(nc, subject: str, raw: bytes):
    try:
        payload = json.loads(raw.decode())
    except Exception as exc:
        result = {"error": f"Invalid JSON: {exc}"}
        await nc.publish(RESPONSE_SUBJECT, json.dumps(result).encode())
        return

    action = payload.get("action", "")
    params = payload.get("params", {})

    if action == "publish_post":
        post_id = payload.get("post_id") or params.get("post_id", "")
        post = get_post(post_id)
        if "error" in post:
            result = post
        else:
            result = await _request_approval(nc, action, {
                "post_id": post_id,
                "platform": post.get("platform"),
                "content_preview": post.get("content", "")[:120],
            })
    elif action == "publish_scheduled":
        due = _due_posts()
        result = await _request_approval(nc, action, {
            "due_count": len(due),
            "post_ids": due,
        })
    else:
        result = execute_task(payload)

    result["operator"] = NAME
    result["version"] = VERSION
    await nc.publish(RESPONSE_SUBJECT, json.dumps(result).encode())


# ---------------------------------------------------------------------------
# Health HTTP server
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            body = json.dumps({"status": "ok", "operator": NAME, "version": VERSION, "port": PORT}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _nats_loop():
    nc = await nats.connect(NATS_URL)
    print(f"[{NAME}] Connected to NATS at {NATS_URL}")

    async def _cb(msg):
        await handle_event(nc, msg.subject, msg.data)

    await nc.subscribe(CALL_SUBJECT, cb=_cb)
    print(f"[{NAME}] Subscribed to {CALL_SUBJECT}")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await nc.drain()


def main():
    health_server = HTTPServer(("0.0.0.0", PORT), _HealthHandler)
    t = threading.Thread(target=health_server.serve_forever, daemon=True)
    t.start()
    print(f"[{NAME}] Health endpoint running on port {PORT}")

    asyncio.run(_nats_loop())


if __name__ == "__main__":
    main()
