import os
import importlib
import pytest

from cascadia.memory_governor.outbox import Outbox


@pytest.fixture
def outbox(tmp_path):
    db = str(tmp_path / "test_outbox.db")
    os.environ["OUTBOX_ENABLED"] = "true"
    import cascadia.memory_governor.flags as f
    importlib.reload(f)
    ob = Outbox(db_path=db)
    yield ob
    os.environ.pop("OUTBOX_ENABLED", None)


def test_enqueue_returns_id(outbox):
    """First enqueue returns a valid id."""
    oid = outbox.enqueue(
        run_id="r1",
        action_type="send_email",
        payload={"to": "a@b.com"},
        idempotency_key="key1",
    )
    assert oid is not None
    assert isinstance(oid, int)


def test_enqueue_duplicate_returns_none(outbox):
    """Duplicate idempotency key returns None."""
    outbox.enqueue("r1", "send_email", {"to": "a@b.com"}, "key1")
    oid2 = outbox.enqueue("r1", "send_email", {"to": "a@b.com"}, "key1")
    assert oid2 is None


def test_get_pending_returns_queued(outbox):
    """Pending actions appear in get_pending()."""
    outbox.enqueue("r1", "send_email", {"to": "a@b.com"}, "key1")
    pending = outbox.get_pending()
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"


def test_mark_completed_removes_from_pending(outbox):
    """Completed actions don't appear in get_pending()."""
    oid = outbox.enqueue("r1", "send_email", {"to": "a@b.com"}, "key1")
    outbox.mark_executing(oid)
    outbox.mark_completed(oid, "msg_abc123")
    pending = outbox.get_pending()
    assert len(pending) == 0


def test_is_completed_after_complete(outbox):
    """is_completed returns True after mark_completed."""
    oid = outbox.enqueue("r1", "send_email", {"to": "a@b.com"}, "key1")
    outbox.mark_completed(oid, "msg_123")
    assert outbox.is_completed("key1") is True


def test_is_completed_false_when_pending(outbox):
    """is_completed returns False for pending action."""
    outbox.enqueue("r1", "send_email", {"to": "a@b.com"}, "key1")
    assert outbox.is_completed("key1") is False


def test_mark_failed_status(outbox):
    """Failed action recorded with error message and not in get_pending()."""
    oid = outbox.enqueue("r1", "send_email", {"to": "a@b.com"}, "key1")
    outbox.mark_failed(oid, "SMTP timeout")
    pending = outbox.get_pending()
    assert len(pending) == 0


def test_idempotency_key_deterministic():
    """Same inputs always produce same key."""
    k1 = Outbox.make_idempotency_key("run1", "send_email", "user@example.com")
    k2 = Outbox.make_idempotency_key("run1", "send_email", "user@example.com")
    assert k1 == k2


def test_idempotency_key_different_for_different_target():
    """Different target produces different key."""
    k1 = Outbox.make_idempotency_key("run1", "send_email", "a@example.com")
    k2 = Outbox.make_idempotency_key("run1", "send_email", "b@example.com")
    assert k1 != k2


def test_outbox_disabled_returns_none():
    """When OUTBOX_ENABLED=false, all operations are no-ops."""
    os.environ.pop("OUTBOX_ENABLED", None)
    ob = Outbox(db_path=":memory:")
    assert ob.enqueue("r1", "email", {"to": "x"}, "k1") is None
    assert ob.get_pending() == []
    assert ob.is_completed("k1") is False


def test_crash_recovery_pending_survives(outbox, tmp_path):
    """Pending actions survive a restart (new Outbox instance)."""
    db = str(tmp_path / "crash_test.db")
    ob1 = Outbox(db_path=db)
    ob1.enqueue("r1", "send_email", {"to": "a@b.com"}, "key1")

    ob2 = Outbox(db_path=db)
    pending = ob2.get_pending()
    assert len(pending) == 1
    assert pending[0]["idempotency_key"] == "key1"
