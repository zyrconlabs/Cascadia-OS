"""CHIEF client — thin stdlib wrapper for calling CHIEF from other components."""
from __future__ import annotations

import json
import urllib.request
import urllib.error


class ChiefClient:
    def __init__(self, base_url: str = "http://127.0.0.1:6210") -> None:
        self.base_url = base_url.rstrip("/")

    def send_task(
        self,
        task: str,
        source_channel: str = "unknown",
        reply_channel: str = "unknown",
        sender: str = "unknown",
        tenant_id: str = "default",
        metadata: dict | None = None,
        timeout: int = 60,
    ) -> dict:
        """POST /task → return response dict."""
        payload = json.dumps({
            "task": task,
            "source_channel": source_channel,
            "reply_channel": reply_channel,
            "sender": sender,
            "tenant_id": tenant_id,
            "metadata": metadata or {},
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/task",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def health(self) -> bool:
        """GET /health → True if ok."""
        try:
            with urllib.request.urlopen(
                f"{self.base_url}/health", timeout=3
            ) as r:
                data = json.loads(r.read().decode())
                return bool(data.get("ok"))
        except Exception:
            return False
