# MATURITY: PRODUCTION — Persists decisions. Wakes blocked runs.
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cascadia.durability.run_store import RunStore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalStore:
    """Owns approval persistence and wake-up state changes. Does not own approval UI collection."""

    def __init__(self, run_store: RunStore) -> None:
        self.run_store = run_store

    def request_approval(self, run_id: str, step_index: int, action_key: str) -> int:
        """Owns creation of a pending approval row. Does not own policy decisions that triggered it."""
        latest = self.get_latest(run_id, action_key)
        if latest and latest['decision'] == 'pending':
            return int(latest['id'])
        approval_id = self.run_store.insert_approval({'run_id': run_id, 'step_index': step_index, 'action_key': action_key, 'decision': 'pending', 'actor': None, 'reason': '', 'created_at': utc_now(), 'decided_at': None})
        self.run_store.update_run(run_id, run_state='waiting_human')
        return approval_id

    def record_decision(self, approval_id: int, decision: str, actor: str, reason: str = '') -> None:
        """Owns recording user/system decisions. Does not own notification or UI concerns."""
        self.run_store.update_approval(approval_id, decision=decision, actor=actor, reason=reason, decided_at=utc_now())
        with self.run_store.connection() as conn:
            row = conn.execute('SELECT * FROM approvals WHERE id = ?', (approval_id,)).fetchone()
        if row is None:
            raise KeyError(approval_id)
        run_id = row['run_id']
        if decision == 'approved':
            self.wake_blocked_run(run_id)
        elif decision == 'denied':
            self.run_store.update_run(run_id, run_state='failed')

    def get_latest(self, run_id: str, action_key: str) -> Optional[Dict[str, Any]]:
        """Owns latest-approval lookup. Does not own cross-action aggregation."""
        return self.run_store.latest_approval(run_id, action_key)

    def pending_approvals(self, run_id: str) -> List[Dict[str, Any]]:
        """Owns pending-approval queries. Does not own escalation policy."""
        return self.run_store.pending_approvals(run_id)

    def edit_and_approve(self, approval_id: int, actor: str,
                          edited_content: str, edit_summary: str = '') -> None:
        """
        Approve an action with owner edits applied.
        Owns: recording the edit, marking approved, waking the run.
        Does not own: applying edits to operator output (operator responsibility).
        """
        self.run_store.update_approval(
            approval_id,
            decision='approved',
            actor=actor,
            edited_content=edited_content,
            edit_summary=edit_summary,
            decided_at=utc_now(),
        )
        with self.run_store.connection() as conn:
            row = conn.execute(
                'SELECT run_id FROM approvals WHERE id = ?', (approval_id,)
            ).fetchone()
        if row:
            self.wake_blocked_run(row['run_id'])

    def wake_blocked_run(self, run_id: str) -> None:
        """Owns wake transition after approval. Does not own resume execution itself."""
        self.run_store.clear_blocked(run_id)
        self.run_store.update_run(run_id, run_state='retrying')
