"""
failure_event.py — Cascadia OS 2026.5
Structured failure schema for the escalation chain.
Owns: failure event definition, serialization, factory helpers.
Does not own: routing, retry policy, or escalation decisions.

Two failure paths:
  Soft: operator alive but blocked — emits its own FailureEvent.
  Hard: operator dead or pulse stale — watchdog emits on operator's behalf.
"""
# MATURITY: PRODUCTION — Session E escalation foundation.
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional
from uuid import uuid4

FailureType = Literal[
    "missing_connector",
    "insufficient_data",
    "llm_timeout",
    "permission_denied",
    "requires_decision",
    "operator_crash",
    "heartbeat_stale",
    "step_timeout",
    "external_api_failure",
    "unknown",
]

FailureSeverity = Literal[
    "low",
    "medium",
    "high",
    "critical",
]

_NATS_URL = "nats://localhost:4222"
_SUBJECT_FAILURE = "zyrcon.operator.failure"


@dataclass
class FailureEvent:
    """Structured failure signal for the supervisor loop."""
    id: str = field(default_factory=lambda: str(uuid4()))
    mission_id: str = ""
    run_id: str = ""
    step_id: str = ""
    operator: str = ""
    failure_type: FailureType = "unknown"
    severity: FailureSeverity = "medium"
    context: str = ""
    attempted: int = 0
    max_attempts: int = 3
    recoverable: bool = True
    requires_user_decision: bool = False
    suggested_action: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "operator": self.operator,
            "failure_type": self.failure_type,
            "severity": self.severity,
            "context": self.context,
            "attempted": self.attempted,
            "max_attempts": self.max_attempts,
            "recoverable": self.recoverable,
            "requires_user_decision": self.requires_user_decision,
            "suggested_action": self.suggested_action,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FailureEvent":
        return cls(
            id=data.get("id", str(uuid4())),
            mission_id=data.get("mission_id", ""),
            run_id=data.get("run_id", ""),
            step_id=data.get("step_id", ""),
            operator=data.get("operator", ""),
            failure_type=data.get("failure_type", "unknown"),
            severity=data.get("severity", "medium"),
            context=data.get("context", ""),
            attempted=data.get("attempted", 0),
            max_attempts=data.get("max_attempts", 3),
            recoverable=data.get("recoverable", True),
            requires_user_decision=data.get("requires_user_decision", False),
            suggested_action=data.get("suggested_action"),
            payload=data.get("payload", {}),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_exception(
        cls,
        operator: str,
        run_id: str,
        step_id: str,
        exc: Exception,
        failure_type: FailureType = "unknown",
        severity: FailureSeverity = "medium",
    ) -> "FailureEvent":
        return cls(
            operator=operator,
            run_id=run_id,
            step_id=step_id,
            failure_type=failure_type,
            severity=severity,
            context=f"{type(exc).__name__}: {exc}",
            recoverable=True,
        )

    @classmethod
    def from_stale_pulse(cls, operator: str, run_id: str = "") -> "FailureEvent":
        """Hard failure: watchdog detected stale pulse — operator cannot self-report."""
        return cls(
            operator=operator,
            run_id=run_id,
            failure_type="heartbeat_stale",
            severity="high",
            context=f"Pulse file stale for operator '{operator}' — likely crashed.",
            recoverable=True,
            suggested_action="restart_and_resume",
        )

    @classmethod
    def from_operator_crash(cls, operator: str, run_id: str = "") -> "FailureEvent":
        """Hard failure: watchdog detected operator is offline."""
        return cls(
            operator=operator,
            run_id=run_id,
            failure_type="operator_crash",
            severity="high",
            context=f"Operator '{operator}' failed health check — restarting.",
            recoverable=True,
            suggested_action="restart_and_resume",
        )


# ------------------------------------------------------------------
# NATS publish helper (sync, fire-and-forget)
# ------------------------------------------------------------------

def publish_failure_event(event: FailureEvent, nats_url: str = _NATS_URL) -> None:
    """
    Publish a FailureEvent to zyrcon.operator.failure.
    Fire-and-forget from synchronous code. Silent on failure (NATS optional).
    """
    data = json.dumps(event.to_dict()).encode()
    _nats_publish_sync(_SUBJECT_FAILURE, data, nats_url)


def _nats_publish_sync(subject: str, data: bytes, url: str = _NATS_URL) -> None:
    """Publish to NATS from synchronous code via a daemon thread. Silent on failure."""
    def _run() -> None:
        try:
            import asyncio
            import nats as _nats  # type: ignore[import]

            async def _pub() -> None:
                nc = await _nats.connect(url, connect_timeout=2)
                await nc.publish(subject, data)
                await nc.drain()

            asyncio.run(_pub())
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
