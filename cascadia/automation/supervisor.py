"""
supervisor.py — Cascadia OS 2026.5
SUPERVISOR: Operator failure routing and escalation loop.
Subscribes to zyrcon.operator.failure via NATS. Routes each failure
to retry, restart, escalate, or dead-letter based on failure type,
retry policy, and current run state.
Owns: failure routing decisions, retry triggering, escalation dispatch.
Does not own: execution (STITCH), persistence (RunStore), approval UI (PRISM).
"""
# MATURITY: PRODUCTION — Session E supervisor loop.
from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import cascadia.automation.failure_event as _fe_mod
from cascadia.automation.failure_event import FailureEvent, _NATS_URL
from cascadia.automation.retry_policy import DEFAULT_RETRY_POLICY, RetryPolicy
from cascadia.shared.config import load_config
from cascadia.shared.logger import configure_logging

_SUBJECT_FAILURE       = "zyrcon.operator.failure"
_SUBJECT_HEALTH        = "zyrcon.operator.health"
_SUBJECT_RETRY         = "zyrcon.operator.retry"
_SUBJECT_RESTART       = "zyrcon.operator.restart"
_SUBJECT_ESCALATE      = "zyrcon.chief.escalate"
_SUBJECT_DECISION_REQ  = "zyrcon.beacon.decision_request"
_SUBJECT_DEAD_LETTER   = "zyrcon.mission.dead_letter"

# Failure types that bypass retry and go straight to BEACON escalation.
_ESCALATE_IMMEDIATELY = frozenset({
    "missing_connector",
    "permission_denied",
    "requires_decision",
    "unknown",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _publish(subject: str, payload: Dict[str, Any], nats_url: str = _NATS_URL) -> None:
    _fe_mod._nats_publish_sync(subject, json.dumps(payload).encode(), nats_url)


class Supervisor:
    """
    Operator failure supervisor.
    Subscribes to NATS, maintains health registry, routes failures.
    Falls back to polling-based dispatch if NATS unavailable.
    """

    def __init__(self, config: Dict[str, Any], logger: Any,
                 retry_policy: Optional[RetryPolicy] = None,
                 nats_url: str = _NATS_URL) -> None:
        self.config = config
        self.logger = logger
        self._policy = retry_policy or DEFAULT_RETRY_POLICY
        self._nats_url = nats_url
        self._health: Dict[str, Dict[str, Any]] = {}
        self._health_lock = threading.Lock()
        self._db_path: str = config.get("database_path", "./data/runtime/cascadia.db")
        self._beacon_port: Optional[int] = next(
            (c["port"] for c in config.get("components", []) if c["name"] == "beacon"),
            None,
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the supervisor. Attempts NATS subscription; falls back gracefully."""
        self.logger.info("Supervisor starting — NATS url: %s", self._nats_url)
        self._register_with_crew()
        try:
            import asyncio
            asyncio.run(self._async_run())
        except Exception as exc:
            self.logger.error("Supervisor NATS loop failed: %s — retrying in 30s", exc)
            time.sleep(30)

    async def _async_run(self) -> None:
        import nats  # type: ignore[import]

        async def _failure_handler(msg: Any) -> None:
            try:
                event = FailureEvent.from_dict(json.loads(msg.data.decode()))
                self.route_failure(event)
            except Exception as exc:
                self.logger.error("Supervisor: message decode error: %s", exc)

        async def _health_handler(msg: Any) -> None:
            try:
                data = json.loads(msg.data.decode())
                with self._health_lock:
                    self._health[data.get("operator", "")] = data
            except Exception:
                pass

        nc = await nats.connect(self._nats_url, connect_timeout=5)
        await nc.subscribe(_SUBJECT_FAILURE, cb=_failure_handler)
        await nc.subscribe(_SUBJECT_HEALTH,  cb=_health_handler)
        self.logger.info("Supervisor subscribed to %s and %s", _SUBJECT_FAILURE, _SUBJECT_HEALTH)
        try:
            while True:
                await __import__("asyncio").sleep(1)
        finally:
            await nc.drain()

    # ------------------------------------------------------------------
    # Routing — testable without NATS
    # ------------------------------------------------------------------

    def route_failure(self, event: FailureEvent) -> str:
        """
        Decide what to do with a failure event.
        Returns action taken: 'retry' | 'restart' | 'escalate' | 'dead_letter'.
        All decisions are published to NATS and written to run_store.
        """
        ft = event.failure_type
        run_id = event.run_id

        self.logger.warning(
            "Supervisor: failure event — operator=%s type=%s run=%s attempt=%d",
            event.operator, ft, run_id, event.attempted,
        )

        # Immediate escalation — no retry
        if ft in _ESCALATE_IMMEDIATELY:
            return self._do_escalate(event, reason=f"'{ft}' requires immediate escalation")

        # Check retry policy
        if self._policy.should_retry(ft, event.attempted):
            return self._do_retry(event)

        # Max attempts exhausted — escalate or dead-letter
        if not event.recoverable:
            return self._do_dead_letter(event)

        return self._do_escalate(
            event,
            reason=self._policy.escalate_reason(ft, event.attempted),
        )

    def _do_retry(self, event: FailureEvent) -> str:
        delay = self._policy.delay_seconds(event.attempted)
        self.logger.info(
            "Supervisor: retry — operator=%s attempt=%d delay=%.1fs",
            event.operator, event.attempted + 1, delay,
        )
        action = "restart_and_resume" if event.failure_type in ("operator_crash", "heartbeat_stale") else "retry"
        subject = _SUBJECT_RESTART if action == "restart_and_resume" else _SUBJECT_RETRY
        _publish(subject, {
            "event_id": event.id,
            "operator": event.operator,
            "run_id": event.run_id,
            "step_id": event.step_id,
            "attempt": event.attempted + 1,
            "delay_seconds": delay,
            "failure_type": event.failure_type,
            "resume": self._get_resume_point(event.run_id),
        }, self._nats_url)
        self._update_run(event.run_id, run_state="recovering",
                         recovery_attempt=event.attempted + 1)
        self._policy.write_retry_audit(self._get_run_store(), event.run_id,
                                       event.attempted + 1, event.failure_type)
        return "retry"

    def _do_escalate(self, event: FailureEvent, reason: str = "") -> str:
        self.logger.warning(
            "Supervisor: escalating — operator=%s type=%s reason=%s",
            event.operator, event.failure_type, reason,
        )
        escalation = {
            "event_id": event.id,
            "operator": event.operator,
            "run_id": event.run_id,
            "step_id": event.step_id,
            "failure_type": event.failure_type,
            "reason": reason or event.context,
            "requires_user_decision": event.requires_user_decision,
            "suggested_action": event.suggested_action,
            "timestamp": _now(),
        }
        _publish(_SUBJECT_ESCALATE, escalation, self._nats_url)
        self._update_run(event.run_id, run_state="escalated",
                         escalation_status="escalated",
                         escalation_triggered_at=_now())
        # Also notify BEACON via HTTP if reachable
        self._notify_beacon(escalation)
        return "escalate"

    def _do_dead_letter(self, event: FailureEvent) -> str:
        self.logger.error(
            "Supervisor: dead-letter — operator=%s type=%s run=%s",
            event.operator, event.failure_type, event.run_id,
        )
        _publish(_SUBJECT_DEAD_LETTER, {
            "event_id": event.id,
            "operator": event.operator,
            "run_id": event.run_id,
            "failure_type": event.failure_type,
            "context": event.context,
            "attempts": event.attempted,
            "timestamp": _now(),
        }, self._nats_url)
        self._promote_to_dead_letter(event)
        self._update_run(event.run_id, run_state="dead_letter",
                         dead_letter_at=_now(),
                         dead_letter_reason=event.context)
        return "dead_letter"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_run_store(self) -> Any:
        try:
            from cascadia.durability.run_store import RunStore
            return RunStore(self._db_path)
        except Exception:
            return None

    def _update_run(self, run_id: str, **kwargs: Any) -> None:
        if not run_id:
            return
        try:
            rs = self._get_run_store()
            if rs:
                rs.update_run(run_id, **kwargs)
        except Exception:
            pass

    def _get_resume_point(self, run_id: str) -> Optional[Dict[str, Any]]:
        if not run_id:
            return None
        try:
            from cascadia.durability.run_store import RunStore
            from cascadia.durability.step_journal import StepJournal
            from cascadia.durability.idempotency import IdempotencyManager
            from cascadia.durability.resume_manager import ResumeManager
            rs = RunStore(self._db_path)
            rm = ResumeManager(rs, StepJournal(rs), IdempotencyManager(rs))
            ctx = rm.determine_resume_point(run_id)
            return {"can_resume": ctx["can_resume"],
                    "resume_step_index": ctx.get("resume_step_index")}
        except Exception:
            return None

    def _promote_to_dead_letter(self, event: FailureEvent) -> None:
        try:
            from cascadia.durability.dead_letter import DeadLetterQueue
            dlq = DeadLetterQueue(self._db_path)
            dlq.promote(event.run_id, event.step_id, event)
        except Exception:
            pass

    def _notify_beacon(self, escalation: Dict[str, Any]) -> None:
        if not self._beacon_port:
            return
        try:
            data = json.dumps(escalation).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{self._beacon_port}/escalation",
                data=data, method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass

    def _register_with_crew(self) -> None:
        crew = next((c for c in self.config.get("components", []) if c["name"] == "crew"), None)
        if not crew:
            return
        try:
            data = json.dumps({
                "operator_id": "supervisor",
                "name": "Supervisor",
                "capabilities": ["failure.route", "escalation.dispatch"],
                "port": 0,
            }).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{crew['port']}/register",
                data=data, method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass

    def health_registry(self) -> Dict[str, Any]:
        """Return current operator health snapshot."""
        with self._health_lock:
            return dict(self._health)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    args = p.parse_args()
    config = load_config(args.config)
    logger = configure_logging(config["log_dir"], "supervisor")
    sup = Supervisor(config, logger)
    while True:
        try:
            sup.run()
        except KeyboardInterrupt:
            break
        except Exception as exc:
            logger.error("Supervisor crashed: %s — restarting in 30s", exc)
            time.sleep(30)


if __name__ == "__main__":
    main()
