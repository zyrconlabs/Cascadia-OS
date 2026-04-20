# MATURITY: PRODUCTION — allow/deny/approval_required per action.
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from cascadia.durability.run_store import RunStore
from cascadia.system.approval_store import ApprovalStore


@dataclass(slots=True)
class PolicyDecision:
    """Owns a single policy decision result. Does not own execution side effects."""
    decision: str
    reason: str
    approval_id: int | None = None


class RuntimePolicy:
    """Owns lightweight allow/deny/approval gating. Does not own UI or dependency detection."""

    def __init__(self, rules: Dict[str, str], run_store: RunStore, approval_store: ApprovalStore) -> None:
        self.rules = rules
        self.run_store = run_store
        self.approval_store = approval_store

    def check(self, *, run_id: str, step_index: int, action: str) -> PolicyDecision:
        """Owns decision lookup for one action. Does not own later scheduling or retries."""
        decision = self.rules.get(action, 'allowed')
        if decision == 'denied':
            self.run_store.update_run(run_id, run_state='failed')
            return PolicyDecision('denied', f'Action {action} is denied by runtime policy')
        if decision == 'approval_required':
            latest = self.approval_store.get_latest(run_id, action)
            if latest and latest['decision'] == 'approved':
                return PolicyDecision('allowed', f'Action {action} previously approved')
            approval_id = self.approval_store.request_approval(run_id, step_index, action)
            return PolicyDecision('approval_required', f'Action {action} requires approval', approval_id)
        return PolicyDecision('allowed', f'Action {action} allowed by runtime policy')
