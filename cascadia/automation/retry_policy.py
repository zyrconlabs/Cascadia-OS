"""
retry_policy.py — Cascadia OS 2026.5
Retry policy for the escalation chain.
Owns: retry decision, backoff calculation, audit writing.
Does not own: execution, NATS publishing, or escalation routing.
"""
# MATURITY: PRODUCTION — Session E retry foundation.
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cascadia.automation.failure_event import FailureEvent, FailureType

# Failure types that are never retried — escalate immediately.
_NO_RETRY: frozenset = frozenset({
    "missing_connector",
    "permission_denied",
    "requires_decision",
})

# Failure types that always get at least one retry before escalation.
_ALWAYS_RETRY: frozenset = frozenset({
    "llm_timeout",
    "external_api_failure",
    "step_timeout",
    "operator_crash",
    "heartbeat_stale",
})


@dataclass
class RetryPolicy:
    """
    Configurable backoff policy for operator failure recovery.

    Attributes:
        max_attempts:        Maximum retry attempts before escalation.
        initial_delay_seconds: Delay before first retry.
        backoff_multiplier:  Multiply delay by this factor each attempt.
        max_delay_seconds:   Cap on delay between retries.
        jitter:              Add random fraction to prevent thundering herd.
    """
    max_attempts: int = 3
    initial_delay_seconds: float = 5.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 60.0
    jitter: bool = True

    def should_retry(self, failure_type: str, attempted: int) -> bool:
        """
        Return True if the failure should be retried.
        Never retry: missing_connector, permission_denied, requires_decision.
        Always cap at max_attempts.
        """
        if failure_type in _NO_RETRY:
            return False
        return attempted < self.max_attempts

    def delay_seconds(self, attempt: int) -> float:
        """
        Exponential backoff with optional jitter for attempt N (0-indexed).
        Capped at max_delay_seconds.
        """
        delay = min(
            self.initial_delay_seconds * (self.backoff_multiplier ** attempt),
            self.max_delay_seconds,
        )
        if self.jitter:
            delay *= (0.75 + random.random() * 0.5)
        return round(delay, 2)

    def escalate_reason(self, failure_type: str, attempted: int) -> str:
        """Human-readable reason why a run is being escalated."""
        if failure_type in _NO_RETRY:
            return f"'{failure_type}' requires immediate escalation — not retryable."
        return (
            f"'{failure_type}' failed {attempted} time(s); "
            f"max_attempts={self.max_attempts} reached."
        )

    def write_retry_audit(
        self,
        run_store: object,
        run_id: str,
        attempt: int,
        failure_type: str,
    ) -> None:
        """Record retry attempt in run_trace for auditability."""
        try:
            from datetime import datetime, timezone
            rs = run_store  # type: ignore[assignment]
            rs.trace_event(  # type: ignore[attr-defined]
                run_id=run_id,
                event_type="retry_attempt",
                step_index=None,
                payload={"failure_type": failure_type, "attempt": attempt},
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            rs.update_run(run_id, recovery_attempt=attempt)  # type: ignore[attr-defined]
        except Exception:
            pass


# Default policy — used by supervisor unless overridden per-operator.
DEFAULT_RETRY_POLICY = RetryPolicy()
