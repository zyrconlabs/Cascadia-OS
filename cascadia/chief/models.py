"""CHIEF data models — task request/response shapes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskRequest:
    task: str
    source_channel: str = "unknown"
    reply_channel: str = "unknown"
    sender: str = "unknown"
    tenant_id: str = "default"
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "TaskRequest":
        return cls(
            task=d.get("task", ""),
            source_channel=d.get("source_channel", "unknown"),
            reply_channel=d.get("reply_channel", "unknown"),
            sender=d.get("sender", "unknown"),
            tenant_id=d.get("tenant_id", "default"),
            metadata=d.get("metadata") or {},
        )

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "source_channel": self.source_channel,
            "reply_channel": self.reply_channel,
            "sender": self.sender,
            "tenant_id": self.tenant_id,
            "metadata": self.metadata,
        }


@dataclass
class TaskResponse:
    ok: bool
    task_id: str
    selected_type: str          # "operator" | "mission" | "status" | "none"
    reply_text: str
    mode: str = "sync"
    selected_target: str | None = None
    raw_result: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "task_id": self.task_id,
            "mode": self.mode,
            "selected_target": self.selected_target,
            "selected_type": self.selected_type,
            "reply_text": self.reply_text,
            "raw_result": self.raw_result,
        }
