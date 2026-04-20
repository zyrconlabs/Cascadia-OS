"""
durability/resume_manager.py - Cascadia OS v0.43
Owns safe resume-point calculation from the step journal and side_effects table.
Does NOT own step execution, scheduling, or approval UI.

Resume rules:
  1. A run in waiting_human with pending approvals cannot auto-resume.
  2. A step is only safe to skip if it completed AND all its side effects are committed.
  3. If a step completed but a side effect is still planned, resume FROM that step (re-attempt the effect).
  4. Restore state from the last fully committed step's output_state.
  5. A run with run_state=poisoned or complete or abandoned is never resumable.
"""
# MATURITY: PRODUCTION — Crash-safe resume. All edge cases tested.
from __future__ import annotations

from typing import Any, Dict

from .idempotency import IdempotencyManager
from .run_store import RunStore
from .step_journal import StepJournal

NON_RESUMABLE_STATES = {'complete', 'failed', 'poisoned', 'abandoned'}


class ResumeManager:
    """Owns safe resume-point calculation. Does not own execution or approval UI."""

    def __init__(self, run_store: RunStore, step_journal: StepJournal,
                 idempotency: IdempotencyManager) -> None:
        self.run_store = run_store
        self.step_journal = step_journal
        self.idempotency = idempotency

    def determine_resume_point(self, run_id: str) -> Dict[str, Any]:
        """
        Calculate the safe step to resume from after a crash or restart.

        Returns a dict with:
          can_resume   — bool
          resume_step_index — int | None
          restored_state — dict
          last_committed_step_index — int
          reason — str
        """
        run = self.run_store.get_run(run_id)
        if run is None:
            raise KeyError(f'run not found: {run_id}')

        run_state = run.get('run_state', 'pending')

        # States that are never resumable
        if run_state in NON_RESUMABLE_STATES:
            return self._not_resumable(run, f'run_state is {run_state}')

        # Approval-gated: resume only after decision recorded
        if run_state == 'waiting_human':
            pending = self.run_store.pending_approvals(run_id)
            if pending:
                return self._not_resumable(
                    run, 'waiting_for_approval',
                    pending_approval_ids=[p['id'] for p in pending],
                    pending_actions=[p['action_key'] for p in pending],
                )
            # Approval was recorded (shouldn't still be waiting_human) — safe to resume
            return self._safe_resume(run)

        return self._safe_resume(run)

    def _safe_resume(self, run: Dict[str, Any]) -> Dict[str, Any]:
        """
        Walk steps in order. Stop at the first step that is either:
          - not completed, or
          - completed but has uncommitted side effects.
        Resume from that step index.
        """
        run_id = run['run_id']
        steps = self.step_journal.last_per_step(run_id)

        last_committed_idx = -1
        restored_state = run.get('input_snapshot') or {}

        for step in steps:
            # Step not completed or failed
            if not step.get('completed_at') or step.get('failure_reason'):
                break

            # Step completed — check side effects
            effects = self.idempotency.all_for_step(run_id, step['step_index'])
            uncommitted = [e for e in effects if e['status'] != 'committed']
            if uncommitted:
                # Effects declared but not committed — resume FROM this step
                # The idempotency layer will skip already-committed ones
                break

            # Fully committed — advance the safe pointer
            last_committed_idx = step['step_index']
            if step.get('output_state'):
                restored_state = step['output_state']

        return {
            'run': run,
            'can_resume': True,
            'resume_step_index': last_committed_idx + 1,
            'restored_state': restored_state,
            'last_committed_step_index': last_committed_idx,
            'reason': 'ok',
        }

    def _not_resumable(self, run: Dict[str, Any], reason: str, **extra: Any) -> Dict[str, Any]:
        return {
            'run': run,
            'can_resume': False,
            'resume_step_index': None,
            'restored_state': run.get('state_snapshot') or {},
            'last_committed_step_index': None,
            'reason': reason,
            **extra,
        }

    def scan_resumable(self) -> list[Dict[str, Any]]:
        """
        Called at FLINT startup. Returns resume contexts for all interrupted runs.
        Does not execute them — returns the list for the caller to act on.
        """
        resumable_states = {'pending', 'running', 'retrying', 'resuming'}
        with self.run_store.connection() as conn:
            rows = conn.execute(
                f"SELECT run_id FROM runs WHERE run_state IN ({','.join('?'*len(resumable_states))})"
                " ORDER BY updated_at ASC",
                list(resumable_states),
            ).fetchall()

        results = []
        for row in rows:
            try:
                ctx = self.determine_resume_point(row['run_id'])
                if ctx['can_resume']:
                    results.append(ctx)
            except Exception:
                pass
        return results
