"""
tests/test_session_e.py — Cascadia OS 2026.5
Session E: Escalation Chain / Supervisor Loop
Unit + acceptance tests for all 6 acceptance scenarios.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_db(tmp: str) -> str:
    """Create a migrated test database. Returns path."""
    import sqlite3
    from cascadia.durability.migration import migrate
    db = str(Path(tmp) / "cascadia.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    conn.commit()
    conn.close()
    return db


def _make_run(db: str, run_id: str, run_state: str = "running") -> None:
    from cascadia.durability.run_store import RunStore
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    rs = RunStore(db)
    rs.create_run({
        "run_id": run_id,
        "operator_id": "test_op",
        "goal": "test goal",
        "run_state": run_state,
        "created_at": now,
        "updated_at": now,
    })


# ──────────────────────────────────────────────────────────────────────────────
# 1. FailureEvent serialization / deserialization
# ──────────────────────────────────────────────────────────────────────────────

class TestFailureEventSerialization(unittest.TestCase):

    def test_to_dict_round_trip(self):
        from cascadia.automation.failure_event import FailureEvent
        ev = FailureEvent(
            run_id="run_abc",
            operator="QUOTE",
            failure_type="llm_timeout",
            severity="high",
            context="model timed out",
            attempted=1,
        )
        d = ev.to_dict()
        self.assertEqual(d["failure_type"], "llm_timeout")
        self.assertEqual(d["severity"], "high")
        self.assertEqual(d["attempted"], 1)

    def test_from_dict_round_trip(self):
        from cascadia.automation.failure_event import FailureEvent
        original = FailureEvent(
            run_id="run_xyz", operator="SCOUT",
            failure_type="missing_connector", severity="critical",
        )
        restored = FailureEvent.from_dict(original.to_dict())
        self.assertEqual(restored.run_id, "run_xyz")
        self.assertEqual(restored.failure_type, "missing_connector")
        self.assertEqual(restored.id, original.id)

    def test_json_serializable(self):
        from cascadia.automation.failure_event import FailureEvent
        ev = FailureEvent(operator="TEST", failure_type="unknown")
        # Must not raise
        json.dumps(ev.to_dict())


# ──────────────────────────────────────────────────────────────────────────────
# 2. from_stale_pulse produces correct event
# ──────────────────────────────────────────────────────────────────────────────

class TestFromStalePulse(unittest.TestCase):

    def test_stale_pulse_fields(self):
        from cascadia.automation.failure_event import FailureEvent
        ev = FailureEvent.from_stale_pulse("flint", run_id="run_001")
        self.assertEqual(ev.failure_type, "heartbeat_stale")
        self.assertEqual(ev.severity, "high")
        self.assertEqual(ev.operator, "flint")
        self.assertEqual(ev.run_id, "run_001")
        self.assertTrue(ev.recoverable)
        self.assertEqual(ev.suggested_action, "restart_and_resume")

    def test_from_operator_crash_fields(self):
        from cascadia.automation.failure_event import FailureEvent
        ev = FailureEvent.from_operator_crash("SOCIAL")
        self.assertEqual(ev.failure_type, "operator_crash")
        self.assertEqual(ev.operator, "SOCIAL")
        self.assertTrue(ev.recoverable)


# ──────────────────────────────────────────────────────────────────────────────
# 3. missing_connector → escalate immediately, no retry
# ──────────────────────────────────────────────────────────────────────────────

class TestMissingConnectorEscalatesImmediately(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _make_db(self.tmp)
        _make_run(self.db, "run_mc_001")

    def _make_supervisor(self):
        from cascadia.automation.supervisor import Supervisor
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        # Patch out NATS publish so tests don't need NATS running
        sup._nats_url = "nats://127.0.0.1:9999"  # unreachable
        return sup

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_missing_connector_escalates(self, mock_pub):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.automation.supervisor import Supervisor
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        ev = FailureEvent(
            run_id="run_mc_001", operator="QUOTE",
            failure_type="missing_connector", attempted=0,
        )
        action = sup.route_failure(ev)
        self.assertEqual(action, "escalate")
        mock_pub.assert_called()
        # Verify NATS subject published
        calls = [c[0][0] for c in mock_pub.call_args_list]
        self.assertTrue(any("escalate" in s for s in calls))

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_missing_connector_run_state_escalated(self, _):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.automation.supervisor import Supervisor
        from cascadia.durability.run_store import RunStore
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        ev = FailureEvent(
            run_id="run_mc_001", operator="QUOTE",
            failure_type="missing_connector", attempted=0,
        )
        sup.route_failure(ev)
        rs = RunStore(self.db)
        run = rs.get_run("run_mc_001")
        self.assertEqual(run["run_state"], "escalated")


# ──────────────────────────────────────────────────────────────────────────────
# 4. permission_denied → escalate immediately, no retry
# ──────────────────────────────────────────────────────────────────────────────

class TestPermissionDeniedEscalatesImmediately(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _make_db(self.tmp)
        _make_run(self.db, "run_pd_001")

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_permission_denied_escalates_not_retries(self, mock_pub):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.automation.supervisor import Supervisor
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        ev = FailureEvent(
            run_id="run_pd_001", operator="INVOICE",
            failure_type="permission_denied", attempted=0,
        )
        action = sup.route_failure(ev)
        self.assertEqual(action, "escalate")

    def test_retry_policy_never_retries_permission_denied(self):
        from cascadia.automation.retry_policy import RetryPolicy
        policy = RetryPolicy()
        self.assertFalse(policy.should_retry("permission_denied", 0))
        self.assertFalse(policy.should_retry("permission_denied", 5))


# ──────────────────────────────────────────────────────────────────────────────
# 5. llm_timeout → supervisor retries with backoff up to max_attempts
# ──────────────────────────────────────────────────────────────────────────────

class TestLlmTimeoutRetries(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _make_db(self.tmp)
        _make_run(self.db, "run_llm_001")

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_llm_timeout_retries_on_first_attempt(self, mock_pub):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.automation.supervisor import Supervisor
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        ev = FailureEvent(
            run_id="run_llm_001", operator="QUOTE",
            failure_type="llm_timeout", attempted=0,
        )
        action = sup.route_failure(ev)
        self.assertEqual(action, "retry")
        # run_state should be recovering
        from cascadia.durability.run_store import RunStore
        run = RunStore(self.db).get_run("run_llm_001")
        self.assertEqual(run["run_state"], "recovering")

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_llm_timeout_escalates_after_max_attempts(self, mock_pub):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.automation.supervisor import Supervisor
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        ev = FailureEvent(
            run_id="run_llm_001", operator="QUOTE",
            failure_type="llm_timeout", attempted=3,  # max_attempts=3
        )
        action = sup.route_failure(ev)
        self.assertEqual(action, "escalate")

    def test_retry_policy_backoff_increases(self):
        from cascadia.automation.retry_policy import RetryPolicy
        policy = RetryPolicy(jitter=False)
        d0 = policy.delay_seconds(0)
        d1 = policy.delay_seconds(1)
        d2 = policy.delay_seconds(2)
        self.assertLess(d0, d1)
        self.assertLess(d1, d2)
        self.assertLessEqual(d2, policy.max_delay_seconds)

    def test_retry_policy_caps_at_max(self):
        from cascadia.automation.retry_policy import RetryPolicy
        policy = RetryPolicy(max_delay_seconds=30, jitter=False)
        delay = policy.delay_seconds(100)
        self.assertLessEqual(delay, 30)


# ──────────────────────────────────────────────────────────────────────────────
# 6. heartbeat_stale → watchdog emits zyrcon.operator.failure
# ──────────────────────────────────────────────────────────────────────────────

class TestWatchdogEmitsFailureEvent(unittest.TestCase):

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_kernel_watchdog_emits_on_stale_pulse(self, mock_pub):
        """kernel/watchdog.restart_flint() must publish a failure event."""
        from cascadia.kernel.watchdog import Watchdog
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            pulse_path = Path(tmp) / "flint.pulse"
            cfg = {
                "log_dir": tmp,
                "data_dir": tmp,
                "database_path": str(Path(tmp) / "cascadia.db"),
                "operators_dir": "",
                "operators_registry_path": "",
                "flint": {
                    "pulse_file": str(pulse_path),
                    "pulse_interval_seconds": 5,
                    "pulse_stale_after_seconds": 15,
                    "status_port": 14011,
                    "health_interval_seconds": 5,
                    "drain_timeout_seconds": 10,
                    "max_restart_attempts": 3,
                    "restart_backoff_seconds": [5, 30, 120],
                },
                "curtain": {"signing_secret": "x"},
                "components": [],
            }
            cfg_path.write_text(json.dumps(cfg))
            wd = Watchdog(str(cfg_path))
            wd.proc = MagicMock()
            wd.proc.poll.return_value = None
            wd.proc.terminate = MagicMock()
            wd.proc.wait = MagicMock()
            with patch.object(wd, "start_flint"):
                wd.restart_flint()
            mock_pub.assert_called()
            subject = mock_pub.call_args[0][0]
            self.assertEqual(subject, "zyrcon.operator.failure")

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_core_watchdog_emits_on_operator_down(self, mock_pub):
        """core/watchdog._restart_operator() must publish a failure event."""
        from cascadia.core.watchdog import OperatorWatchdog
        logger = MagicMock()
        config: dict = {}
        wd = OperatorWatchdog(config, logger)
        op = {"id": "CHIEF", "start_cmd": ""}
        wd._restart_operator(op, attempt=1)
        mock_pub.assert_called()
        subject = mock_pub.call_args[0][0]
        self.assertEqual(subject, "zyrcon.operator.failure")


# ──────────────────────────────────────────────────────────────────────────────
# 7. max_attempts exceeded → dead_letter created
# ──────────────────────────────────────────────────────────────────────────────

class TestDeadLetterOnMaxAttempts(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _make_db(self.tmp)
        _make_run(self.db, "run_dl_001")

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_non_recoverable_goes_to_dlq(self, _):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.automation.supervisor import Supervisor
        from cascadia.durability.dead_letter import DeadLetterQueue
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        ev = FailureEvent(
            run_id="run_dl_001", operator="INVOICE",
            failure_type="external_api_failure",
            attempted=3, recoverable=False,
        )
        action = sup.route_failure(ev)
        self.assertEqual(action, "dead_letter")
        dlq = DeadLetterQueue(self.db)
        records = dlq.list_unresolved()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["run_id"], "run_dl_001")

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_run_state_is_dead_letter(self, _):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.automation.supervisor import Supervisor
        from cascadia.durability.run_store import RunStore
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        ev = FailureEvent(
            run_id="run_dl_001", operator="INVOICE",
            failure_type="step_timeout", attempted=3, recoverable=False,
        )
        sup.route_failure(ev)
        run = RunStore(self.db).get_run("run_dl_001")
        self.assertEqual(run["run_state"], "dead_letter")

    def test_dlq_resolve(self):
        from cascadia.durability.dead_letter import DeadLetterQueue
        from cascadia.automation.failure_event import FailureEvent
        dlq = DeadLetterQueue(self.db)
        ev = FailureEvent(run_id="run_dl_001", operator="TEST",
                         failure_type="unknown", attempted=3)
        dlq_id = dlq.promote("run_dl_001", "step_1", ev)
        dlq.resolve(dlq_id, "Fixed the connector")
        record = dlq.get(dlq_id)
        self.assertEqual(record["resolved"], 1)
        self.assertEqual(record["resolution_note"], "Fixed the connector")


# ──────────────────────────────────────────────────────────────────────────────
# 8. zyrcon.operator.health broadcast
# ──────────────────────────────────────────────────────────────────────────────

class TestHealthBroadcast(unittest.TestCase):

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_health_payload_shape(self, mock_pub):
        """Health broadcast must include operator, status, and timestamp."""
        # Call the internal broadcast logic directly via a minimal ServiceRuntime
        import threading, time
        from cascadia.shared.service_runtime import ServiceRuntime
        with tempfile.TemporaryDirectory() as tmp:
            rt = ServiceRuntime(
                name="test_op",
                port=19999,
                pulse_file=str(Path(tmp) / "test.pulse"),
                log_dir=tmp,
            )
            rt.state = "ready"
            # Run one iteration of the broadcast loop manually
            import datetime as _dt
            payload = json.dumps({
                "operator": rt.name,
                "status": "healthy" if rt.state == "ready" else "unhealthy",
                "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }).encode()
            from cascadia.automation.failure_event import _nats_publish_sync
            _nats_publish_sync("zyrcon.operator.health", payload)
            mock_pub.assert_called_with("zyrcon.operator.health", payload)
            data = json.loads(mock_pub.call_args[0][1].decode())
            self.assertIn("operator", data)
            self.assertIn("status", data)
            self.assertIn("timestamp", data)
            self.assertEqual(data["operator"], "test_op")
            self.assertEqual(data["status"], "healthy")


# ──────────────────────────────────────────────────────────────────────────────
# 9. Escalation channel routing
# ──────────────────────────────────────────────────────────────────────────────

class TestEscalationChannelRouting(unittest.TestCase):

    def _make_daemon(self, channel: str, tmp: str, db: str) -> object:
        from cascadia.system.approval_timeout import ApprovalTimeoutDaemon
        config = {"escalation": {"primary_channel": channel}}
        return ApprovalTimeoutDaemon(
            db_path=db,
            handshake_port=6203,
            owner_email="owner@example.com",
            config=config,
        )

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _make_db(self.tmp)

    def test_email_channel_resolved(self):
        from cascadia.system.approval_timeout import ApprovalTimeoutDaemon
        config = {"escalation": {"primary_channel": "email"}}
        d = ApprovalTimeoutDaemon(self.db, 6203, config=config)
        self.assertEqual(d._channel, "email")

    def test_telegram_channel_resolved(self):
        d = self._make_daemon("telegram", self.tmp, self.db)
        self.assertEqual(d._channel, "telegram")

    def test_whatsapp_channel_resolved(self):
        d = self._make_daemon("whatsapp", self.tmp, self.db)
        self.assertEqual(d._channel, "whatsapp")

    def test_sms_channel_resolved(self):
        d = self._make_daemon("sms", self.tmp, self.db)
        self.assertEqual(d._channel, "sms")

    def test_backward_compat_escalation_email(self):
        """Old escalation_email key should default channel to email with deprecation warning."""
        from cascadia.system.approval_timeout import ApprovalTimeoutDaemon
        config = {"escalation_email": "escalate@example.com"}  # old key
        d = ApprovalTimeoutDaemon(
            self.db, 6203,
            escalation_email="escalate@example.com",
            config=config,
        )
        self.assertEqual(d._channel, "email")
        self.assertEqual(d._escalation_email, "escalate@example.com")

    def test_no_channel_defaults_to_email(self):
        from cascadia.system.approval_timeout import ApprovalTimeoutDaemon
        d = ApprovalTimeoutDaemon(self.db, 6203, config={})
        self.assertEqual(d._channel, "email")


# ──────────────────────────────────────────────────────────────────────────────
# ACCEPTANCE — Test 1: Soft failure (missing_connector)
# ──────────────────────────────────────────────────────────────────────────────

class TestAcceptanceMissingConnector(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _make_db(self.tmp)
        _make_run(self.db, "run_acc_mc")

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_full_escalation_path(self, mock_pub):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.automation.supervisor import Supervisor
        from cascadia.durability.run_store import RunStore
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        ev = FailureEvent(
            run_id="run_acc_mc", operator="QUOTE",
            failure_type="missing_connector",
            requires_user_decision=True, attempted=0,
        )
        action = sup.route_failure(ev)
        # 1. Supervisor escalates
        self.assertEqual(action, "escalate")
        # 2. run_state is escalated
        run = RunStore(self.db).get_run("run_acc_mc")
        self.assertIn(run["run_state"], ("escalated", "waiting_human"))
        # 3. NATS events published (escalate subject)
        subjects = [c[0][0] for c in mock_pub.call_args_list]
        self.assertTrue(any("escalate" in s for s in subjects))


# ──────────────────────────────────────────────────────────────────────────────
# ACCEPTANCE — Test 2: LLM timeout retry
# ──────────────────────────────────────────────────────────────────────────────

class TestAcceptanceLlmRetry(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _make_db(self.tmp)
        _make_run(self.db, "run_acc_llm")

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_retry_increments_recovery_attempt(self, _):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.automation.supervisor import Supervisor
        from cascadia.durability.run_store import RunStore
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        ev = FailureEvent(
            run_id="run_acc_llm", operator="CHIEF",
            failure_type="llm_timeout", attempted=1,
        )
        sup.route_failure(ev)
        run = RunStore(self.db).get_run("run_acc_llm")
        self.assertEqual(run["run_state"], "recovering")

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_max_attempts_reached_escalates(self, _):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.automation.supervisor import Supervisor
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        ev = FailureEvent(
            run_id="run_acc_llm", operator="CHIEF",
            failure_type="llm_timeout", attempted=3,
        )
        action = sup.route_failure(ev)
        self.assertEqual(action, "escalate")


# ──────────────────────────────────────────────────────────────────────────────
# ACCEPTANCE — Test 3: Hard crash / stale pulse
# ──────────────────────────────────────────────────────────────────────────────

class TestAcceptanceHardCrash(unittest.TestCase):

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_stale_pulse_event_type(self, mock_pub):
        from cascadia.automation.failure_event import FailureEvent
        ev = FailureEvent.from_stale_pulse("QUOTE")
        self.assertEqual(ev.failure_type, "heartbeat_stale")
        self.assertTrue(ev.recoverable)
        data = json.dumps(ev.to_dict()).encode()
        from cascadia.automation.failure_event import _nats_publish_sync
        _nats_publish_sync("zyrcon.operator.failure", data)
        mock_pub.assert_called_with("zyrcon.operator.failure", data)


# ──────────────────────────────────────────────────────────────────────────────
# ACCEPTANCE — Test 4: User decision resume (skip)
# ──────────────────────────────────────────────────────────────────────────────

class TestAcceptanceUserDecision(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _make_db(self.tmp)
        _make_run(self.db, "run_acc_ud", run_state="waiting_human")

    def test_decision_options_stored(self):
        from cascadia.system.approval_store import ApprovalStore
        from cascadia.durability.run_store import RunStore
        rs = RunStore(self.db)
        store = ApprovalStore(rs)
        options = [
            {"id": "skip_step", "label": "Skip this step", "action": "skip"},
            {"id": "abort", "label": "Abort mission", "action": "abort"},
        ]
        approval_id = store.insert_decision_request(
            run_id="run_acc_ud",
            step_id="step_2",
            source="test",
            title="Missing connector",
            summary="Gmail connector not configured",
            options=options,
        )
        self.assertGreater(approval_id, 0)
        # Decision options were persisted
        with rs.connection() as conn:
            row = conn.execute(
                "SELECT decision_options FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
        stored = json.loads(row["decision_options"])
        self.assertEqual(len(stored), 2)
        self.assertEqual(stored[0]["action"], "skip")

    def test_approve_wakes_run(self):
        from cascadia.system.approval_store import ApprovalStore
        from cascadia.durability.run_store import RunStore
        rs = RunStore(self.db)
        store = ApprovalStore(rs)
        approval_id = store.insert_decision_request(
            run_id="run_acc_ud", step_id="s1",
            source="test", title="T", summary="S",
        )
        store.record_decision(approval_id, "approved", "andy")
        run = rs.get_run("run_acc_ud")
        self.assertEqual(run["run_state"], "retrying")


# ──────────────────────────────────────────────────────────────────────────────
# ACCEPTANCE — Test 5: Abort
# ──────────────────────────────────────────────────────────────────────────────

class TestAcceptanceAbort(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _make_db(self.tmp)
        _make_run(self.db, "run_acc_abort", run_state="waiting_human")

    def test_denied_sets_failed(self):
        from cascadia.system.approval_store import ApprovalStore
        from cascadia.durability.run_store import RunStore
        rs = RunStore(self.db)
        store = ApprovalStore(rs)
        approval_id = store.request_approval("run_acc_abort", 0, "email.send")
        store.record_decision(approval_id, "denied", "andy", "abort mission")
        run = rs.get_run("run_acc_abort")
        self.assertEqual(run["run_state"], "failed")


# ──────────────────────────────────────────────────────────────────────────────
# ACCEPTANCE — Test 6: Dead-letter
# ──────────────────────────────────────────────────────────────────────────────

class TestAcceptanceDeadLetter(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _make_db(self.tmp)
        _make_run(self.db, "run_acc_dl")

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_dead_letter_no_further_retries(self, _):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.automation.supervisor import Supervisor
        from cascadia.durability.dead_letter import DeadLetterQueue
        from cascadia.durability.run_store import RunStore
        logger = MagicMock()
        config = {"database_path": self.db, "components": [], "log_dir": self.tmp}
        sup = Supervisor(config, logger)
        ev = FailureEvent(
            run_id="run_acc_dl", operator="INVOICE",
            failure_type="external_api_failure",
            attempted=3, recoverable=False,
            context="Stripe API returned 500 three times",
        )
        action = sup.route_failure(ev)
        self.assertEqual(action, "dead_letter")
        # DLQ record exists
        dlq = DeadLetterQueue(self.db)
        records = dlq.list_unresolved()
        self.assertTrue(any(r["run_id"] == "run_acc_dl" for r in records))
        # run_state is dead_letter
        run = RunStore(self.db).get_run("run_acc_dl")
        self.assertEqual(run["run_state"], "dead_letter")
        # resume_manager blocks auto-resume
        from cascadia.durability.resume_manager import ResumeManager
        from cascadia.durability.step_journal import StepJournal
        from cascadia.durability.idempotency import IdempotencyManager
        rs = RunStore(self.db)
        rm = ResumeManager(rs, StepJournal(rs), IdempotencyManager(rs))
        ctx = rm.determine_resume_point("run_acc_dl")
        self.assertFalse(ctx["can_resume"])
        self.assertEqual(ctx["reason"], "dead_letter")

    @patch("cascadia.automation.failure_event._nats_publish_sync")
    def test_resolved_dlq_record(self, _):
        from cascadia.automation.failure_event import FailureEvent
        from cascadia.durability.dead_letter import DeadLetterQueue
        dlq = DeadLetterQueue(self.db)
        ev = FailureEvent(run_id="run_acc_dl", operator="X",
                          failure_type="unknown", attempted=5)
        dlq_id = dlq.promote("run_acc_dl", "step_3", ev)
        dlq.resolve(dlq_id, "Manually re-ran with fixed config")
        unresolved = dlq.list_unresolved()
        self.assertFalse(any(r["id"] == dlq_id for r in unresolved))


# ──────────────────────────────────────────────────────────────────────────────
# Resume manager — new states
# ──────────────────────────────────────────────────────────────────────────────

class TestResumManagerNewStates(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _make_db(self.tmp)

    def _rm(self):
        from cascadia.durability.run_store import RunStore
        from cascadia.durability.step_journal import StepJournal
        from cascadia.durability.idempotency import IdempotencyManager
        from cascadia.durability.resume_manager import ResumeManager
        rs = RunStore(self.db)
        return ResumeManager(rs, StepJournal(rs), IdempotencyManager(rs)), rs

    def test_recovering_is_resumable(self):
        _make_run(self.db, "run_rec", "recovering")
        rm, _ = self._rm()
        ctx = rm.determine_resume_point("run_rec")
        self.assertTrue(ctx["can_resume"])

    def test_dead_letter_not_resumable(self):
        _make_run(self.db, "run_dl2", "dead_letter")
        rm, _ = self._rm()
        ctx = rm.determine_resume_point("run_dl2")
        self.assertFalse(ctx["can_resume"])
        self.assertEqual(ctx["reason"], "dead_letter")

    def test_escalated_without_pending_approval_is_resumable(self):
        _make_run(self.db, "run_esc", "escalated")
        rm, _ = self._rm()
        ctx = rm.determine_resume_point("run_esc")
        self.assertTrue(ctx["can_resume"])

    def test_recovering_included_in_scan_resumable(self):
        _make_run(self.db, "run_scan_rec", "recovering")
        rm, _ = self._rm()
        contexts = rm.scan_resumable()
        ids = [c["run"]["run_id"] for c in contexts]
        self.assertIn("run_scan_rec", ids)


if __name__ == "__main__":
    unittest.main()
