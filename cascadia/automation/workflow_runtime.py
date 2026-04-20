"""
workflow_runtime.py - Cascadia OS v0.43
Owns one durable, executable workflow runtime for the built-in lead follow-up path.
Does not own HTTP transport, UI collection, or external provider auth.

This module closes the gap between workflow definition and durable execution:
- create a run in the durability layer
- execute deterministic workflow steps
- stop safely for approval-gated actions
- resume from the correct step after approval or restart
- commit side effects through the idempotency layer
"""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from cascadia.durability.idempotency import IdempotencyManager
from cascadia.durability.resume_manager import ResumeManager
from cascadia.durability.run_store import RunStore
from cascadia.durability.step_journal import StepJournal
from cascadia.policy.runtime_policy import RuntimePolicy
from cascadia.shared.ids import effect_key
from cascadia.shared.manifest_schema import Manifest, load_manifest
from cascadia.system.approval_store import ApprovalStore
from cascadia.system.dependency_manager import DependencyManager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_EMAIL_RE = re.compile(r'([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})')
_NAME_PATTERNS = (
    re.compile(r"\b(?:this is|i am|i'm|my name is|name[:\-]?)\s+([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+){0,2})(?=\s+(?:from|at)\b|[,.]|$)", re.IGNORECASE),
    re.compile(r"\b([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+){0,2})(?=\s+(?:from|at)\s+[A-Z])"),
)
_COMPANY_PATTERNS = (
    re.compile(r"\b(?:from|at|company[:\-]?)\s+([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,5})(?=[,.]?\s+(?:need|needs|looking|interested|email|phone|call|we\b|for\b)|[,.]|$)", re.IGNORECASE),
)


@dataclass(slots=True)
class ExecutionResult:
    run_id: str
    workflow_id: str
    run_state: str
    current_step: str
    state_snapshot: Dict[str, Any]
    preview: str = ''
    pending_approval_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'run_id': self.run_id,
            'workflow_id': self.workflow_id,
            'run_state': self.run_state,
            'current_step': self.current_step,
            'state_snapshot': self.state_snapshot,
            'draft_preview': self.preview,
            'pending_approval_id': self.pending_approval_id,
            'assistant_message': self.preview,
        }


class WorkflowRuntime:
    """Owns durable execution for one workflow run. Does not own HTTP request handling."""

    def __init__(
        self,
        database_path: str,
        *,
        installed_assets: Optional[Iterable[str]] = None,
        granted_permissions: Optional[Iterable[str]] = None,
        policy_rules: Optional[Dict[str, str]] = None,
        sentinel_port: Optional[int] = None,
        sentinel_fail_open: bool = False,
    ) -> None:
        self.store = RunStore(database_path)
        self.journal = StepJournal(self.store)
        self.idem = IdempotencyManager(self.store)
        self.approvals = ApprovalStore(self.store)
        self.resume = ResumeManager(self.store, self.journal, self.idem)
        self.dependency_manager = DependencyManager(self.store)
        self.policy = RuntimePolicy(
            policy_rules or {'email.send': 'approval_required', 'crm.write': 'allowed'},
            self.store,
            self.approvals,
        )
        self.installed_assets = list(installed_assets or self._discover_installed_assets())
        self.granted_permissions = list(granted_permissions or self._discover_permissions())
        self.sentinel_port: Optional[int] = sentinel_port  # SENTINEL risk check port
        # Fail-open override — False by default (production safe)
        # Set sentinel_fail_open: true in config.json for dev/demo environments only
        self._sentinel_fail_open: bool = sentinel_fail_open  # False = fail-closed (production default)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_run(self, workflow_id: str, definition: Any, payload: Dict[str, Any]) -> str:
        run_id = payload.get('run_id') or f'run_{uuid.uuid4().hex[:10]}'
        state = {
            'workflow_id': workflow_id,
            'session_id': payload.get('session_id'),
            'sender': payload.get('sender', 'human'),
            'content': payload.get('content', ''),
            **payload.get('input_snapshot', {}),
        }
        self.store.create_run({
            'run_id': run_id,
            'operator_id': definition.steps[0].operator if definition.steps else workflow_id,
            'tenant_id': payload.get('tenant_id', 'default'),
            'goal': payload.get('goal', definition.name),
            'current_step': definition.steps[0].name if definition.steps else 'complete',
            'input_snapshot': state,
            'state_snapshot': state,
            'retry_count': 0,
            'last_checkpoint': None,
            'process_state': 'ready',
            'run_state': 'pending',
            'created_at': _now(),
            'updated_at': _now(),
        })
        self.store.trace_event(run_id, 'workflow.created', None, {
            'workflow_id': workflow_id,
            'goal': payload.get('goal', definition.name),
        }, _now())
        return run_id

    def execute(self, workflow_id: str, definition: Any, payload: Dict[str, Any]) -> ExecutionResult:
        run_id = payload.get('run_id')
        if run_id:
            existing = self.store.get_run(run_id)
            if existing is None:
                raise KeyError(f'run not found: {run_id}')
            run_id = existing['run_id']
        else:
            run_id = self.create_run(workflow_id, definition, payload)

        ctx = self.resume.determine_resume_point(run_id)
        if not ctx['can_resume']:
            return ExecutionResult(
                run_id=run_id,
                workflow_id=workflow_id,
                run_state=ctx['run'].get('run_state', 'blocked'),
                current_step=ctx['run'].get('current_step', 'unknown'),
                state_snapshot=ctx.get('restored_state', {}),
                preview=self._waiting_message(ctx),
                pending_approval_id=(ctx.get('pending_approval_ids') or [None])[0],
            )

        state = dict(ctx['restored_state'])
        start_index = int(ctx['resume_step_index'] or 0)
        self.store.update_run(run_id, run_state='running', process_state='running', updated_at=_now())
        self.store.trace_event(run_id, 'workflow.executing', start_index, {
            'workflow_id': workflow_id,
            'resume_step_index': start_index,
        }, _now())

        for idx in range(start_index, len(definition.steps)):
            step = definition.steps[idx]
            manifest = self._load_manifest(step.operator)
            dependency_issue = self.dependency_manager.check(
                run_id, manifest, self.installed_assets, self.granted_permissions,
            )
            if dependency_issue:
                self.store.trace_event(run_id, 'workflow.blocked', idx, dependency_issue, _now())
                run = self.store.get_run(run_id) or {}
                return ExecutionResult(
                    run_id=run_id,
                    workflow_id=workflow_id,
                    run_state=run.get('run_state', 'blocked'),
                    current_step=step.name,
                    state_snapshot=run.get('state_snapshot', state),
                    preview=dependency_issue.get('human_message', 'Run blocked on dependency.'),
                )

            self.store.update_run(
                run_id,
                current_step=step.name,
                operator_id=step.operator,
                state_snapshot=state,
                updated_at=_now(),
            )
            self.journal.append_step(
                run_id=run_id,
                step_name=step.name,
                step_index=idx,
                started_at=_now(),
                input_state=state,
            )
            outcome = self._execute_step(run_id, idx, step.name, step.action, state)
            if outcome['status'] == 'waiting_human':
                self.store.update_run(run_id, run_state='waiting_human', process_state='ready', updated_at=_now())
                self.store.trace_event(run_id, 'approval.waiting', idx, {
                    'action': step.action,
                    'approval_id': outcome['approval_id'],
                }, _now())
                run = self.store.get_run(run_id) or {}
                return ExecutionResult(
                    run_id=run_id,
                    workflow_id=workflow_id,
                    run_state=run.get('run_state', 'waiting_human'),
                    current_step=step.name,
                    state_snapshot=run.get('state_snapshot', state),
                    preview=outcome['preview'],
                    pending_approval_id=outcome['approval_id'],
                )
            if outcome['status'] == 'failed':
                self.journal.append_step(
                    run_id=run_id,
                    step_name=step.name,
                    step_index=idx,
                    started_at=_now(),
                    input_state=state,
                    output_state=outcome.get('state', state),
                    failure_reason=outcome['reason'],
                )
                self.store.update_run(run_id, run_state='failed', process_state='ready', updated_at=_now())
                return ExecutionResult(
                    run_id=run_id,
                    workflow_id=workflow_id,
                    run_state='failed',
                    current_step=step.name,
                    state_snapshot=outcome.get('state', state),
                    preview=outcome['reason'],
                )

            state = outcome['state']
            self.journal.append_step(
                run_id=run_id,
                step_name=step.name,
                step_index=idx,
                started_at=_now(),
                completed_at=_now(),
                input_state=outcome.get('input_state', state),
                output_state=state,
            )
            self.store.update_run(
                run_id,
                state_snapshot=state,
                last_checkpoint=step.name,
                updated_at=_now(),
            )
            self.store.trace_event(run_id, 'step.completed', idx, {
                'step_name': step.name,
                'action': step.action,
            }, _now())

        self.store.update_run(
            run_id,
            current_step='complete',
            run_state='complete',
            process_state='ready',
            state_snapshot=state,
            updated_at=_now(),
        )
        self.store.trace_event(run_id, 'workflow.completed', len(definition.steps) - 1, {
            'workflow_id': workflow_id,
        }, _now())
        return ExecutionResult(
            run_id=run_id,
            workflow_id=workflow_id,
            run_state='complete',
            current_step='complete',
            state_snapshot=state,
            preview=state.get('draft_body', 'Workflow complete.'),
        )

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------


    def _check_sentinel(self, run_id: str, action: str, operator_id: str) -> Optional[Dict[str, Any]]:
        """
        Check action against SENTINEL before execution.
        Returns None if allowed, or a failure dict if blocked/unreachable.

        Safety default: FAIL CLOSED.
        If SENTINEL is configured but unreachable, side-effect steps are
        blocked — not allowed. This is the correct default for a system
        selling trusted autonomy. To override for dev/demo environments,
        set sentinel_fail_open: true in config.json.

        Owns: SENTINEL integration. Does not own risk policy definition.
        """
        if not self.sentinel_port:
            return None  # SENTINEL not configured — allow (no safety layer installed)

        # Check whether fail-open is explicitly enabled in config (dev/demo only)
        fail_open = self._sentinel_fail_open

        try:
            body = json.dumps({
                'action': action,
                'operator_id': operator_id,
                'autonomy_level': 'semi_autonomous',
            }).encode()
            req = urllib.request.Request(
                f'http://127.0.0.1:{self.sentinel_port}/check',
                data=body, method='POST',
                headers={'Content-Type': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=2) as r:
                result = json.loads(r.read().decode())
                verdict = result.get('verdict', 'allowed')
                if verdict == 'blocked':
                    return {
                        'status': 'failed',
                        'reason': result.get('reason', f'SENTINEL blocked: {action}'),
                        'state': {},
                    }
                return None  # allowed or requires_approval — let policy handle approval
        except Exception as exc:
            if fail_open:
                # Dev/demo override — log and continue
                return None
            # Production default: FAIL CLOSED — block side effects when safety is unavailable
            return {
                'status': 'failed',
                'reason': f'SENTINEL unreachable — side effect blocked for safety ({action}). '
                          f'Set sentinel_fail_open: true in config to override for dev/demo. '
                          f'Error: {exc}',
                'state': {},
            }

    def _execute_step(
        self,
        run_id: str,
        step_index: int,
        step_name: str,
        action: str,
        state: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Non-side-effect steps skip SENTINEL check
        if step_name == 'parse_lead':
            return {'status': 'ok', 'state': self._parse_lead_state(state), 'input_state': dict(state)}
        if step_name == 'enrich_company':
            return {'status': 'ok', 'state': self._enrich_company_state(state), 'input_state': dict(state)}
        if step_name == 'draft_email':
            return {'status': 'ok', 'state': self._draft_email_state(state), 'input_state': dict(state)}

        # Side-effect steps — check SENTINEL before executing
        operator_id = state.get('sender', 'workflow_runtime')
        sentinel_block = self._check_sentinel(run_id, action, operator_id)
        if sentinel_block:
            return sentinel_block

        if action == 'email.send':
            return self._send_email(run_id, step_index, dict(state))
        if action == 'crm.write':
            return self._log_crm(run_id, step_index, dict(state))
        return {'status': 'failed', 'reason': f'Unsupported step: {step_name}', 'state': state}

    def _parse_lead_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        content = (state.get('content') or '').strip()
        email = state.get('lead_email') or self._extract_email(content)
        company = state.get('company') or self._extract_company(content, email)
        name = state.get('lead_name') or self._extract_name(content, email)
        summary = state.get('request_summary') or content[:220]
        return {
            **state,
            'lead_email': email,
            'lead_name': name,
            'company': company,
            'request_summary': summary,
        }

    def _enrich_company_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        content = (state.get('content') or '').lower()
        intent = 'high' if any(w in content for w in ('quote', 'pricing', 'proposal', 'demo', 'meeting')) else 'medium'
        urgency = 'high' if any(w in content for w in ('urgent', 'asap', 'today', 'tomorrow')) else 'normal'
        service = 'warehouse automation' if any(w in content for w in ('warehouse', 'automation', 'conveyor', 'mezzanine')) else 'general services'
        score = 90 if intent == 'high' and urgency == 'high' else 75 if intent == 'high' else 55
        return {
            **state,
            'lead_intent': intent,
            'lead_urgency': urgency,
            'service_interest': service,
            'lead_score': score,
            'company_domain': (state.get('lead_email', '').split('@', 1)[-1] if state.get('lead_email') else ''),
        }

    def _draft_email_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        name = state.get('lead_name') or 'there'
        company = state.get('company') or 'your team'
        service = state.get('service_interest') or 'your request'
        urgency_line = 'I can prioritize this quickly.' if state.get('lead_urgency') == 'high' else 'I can put together a clear next-step plan.'
        subject = f"Follow-up for {company} — {service.title()}"
        body = (
            f"Hi {name},\n\n"
            f"Thanks for reaching out about {service}. I reviewed your request and {urgency_line} "
            f"Based on what you shared, the fastest next step is a short call to confirm scope, timing, and budget.\n\n"
            f"If helpful, reply with a couple of times that work for you and I’ll prepare a focused outline before we speak.\n\n"
            f"Best,\nZyrcon Labs"
        )
        preview = f"Draft ready for {state.get('lead_email') or company}. Approval required before send."
        return {
            **state,
            'draft_subject': subject,
            'draft_body': body,
            'draft_preview': preview,
        }

    def _send_email(self, run_id: str, step_index: int, state: Dict[str, Any]) -> Dict[str, Any]:
        decision = self.policy.check(run_id=run_id, step_index=step_index, action='email.send')
        if decision.decision == 'approval_required':
            preview = state.get('draft_preview') or 'Draft ready. Waiting for approval before send.'
            return {
                'status': 'waiting_human',
                'approval_id': decision.approval_id,
                'preview': preview,
                'state': state,
            }
        if decision.decision == 'denied':
            return {'status': 'failed', 'reason': decision.reason, 'state': state}

        recipient = state.get('lead_email') or 'unknown@example.com'
        key = effect_key(run_id, step_index, 'email.send', recipient)
        payload = {
            'subject': state.get('draft_subject', ''),
            'body': state.get('draft_body', ''),
        }
        registered = self.idem.register_planned(
            run_id=run_id,
            step_index=step_index,
            effect_type='email.send',
            effect_key=key,
            target=recipient,
            payload=payload,
            created_at=_now(),
        )
        if registered:
            self.store.trace_event(run_id, 'outbound.dispatched', step_index, {
                'channel': 'email',
                'mode': 'simulated',
                'recipient': recipient,
                'subject': payload['subject'],
            }, _now())
            self.idem.commit(key, _now())
            delivery = 'simulated_sent'
        else:
            delivery = 'already_committed'
        return {
            'status': 'ok',
            'state': {
                **state,
                'delivery_status': delivery,
                'sent_to': recipient,
                'sent_at': _now(),
            },
            'input_state': dict(state),
        }

    def _log_crm(self, run_id: str, step_index: int, state: Dict[str, Any]) -> Dict[str, Any]:
        target = state.get('company') or state.get('lead_email') or 'unknown-lead'
        key = effect_key(run_id, step_index, 'crm.write', target)
        payload = {
            'lead_email': state.get('lead_email', ''),
            'company': state.get('company', ''),
            'score': state.get('lead_score'),
            'status': state.get('delivery_status'),
        }
        registered = self.idem.register_planned(
            run_id=run_id,
            step_index=step_index,
            effect_type='crm.write',
            effect_key=key,
            target=target,
            payload=payload,
            created_at=_now(),
        )
        if registered:
            self.store.trace_event(run_id, 'crm.logged', step_index, payload, _now())
            self.idem.commit(key, _now())
        return {
            'status': 'ok',
            'state': {
                **state,
                'crm_logged': True,
                'crm_target': target,
            },
            'input_state': dict(state),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _discover_installed_assets(self) -> List[str]:
        operators_dir = Path(__file__).resolve().parents[1] / 'operators'
        assets: List[str] = []
        for path in operators_dir.glob('*.json'):
            try:
                assets.append(load_manifest(path).id)
            except Exception:
                continue
        return assets

    def _discover_permissions(self) -> List[str]:
        operators_dir = Path(__file__).resolve().parents[1] / 'operators'
        permissions: List[str] = []
        for path in operators_dir.glob('*.json'):
            try:
                manifest = load_manifest(path)
                permissions.extend(manifest.requested_permissions)
            except Exception:
                continue
        return sorted(set(permissions))

    def _load_manifest(self, operator_id: str) -> Manifest:
        path = Path(__file__).resolve().parents[1] / 'operators' / f'{operator_id}.json'
        return load_manifest(path)

    def _extract_email(self, content: str) -> str:
        m = _EMAIL_RE.search(content or '')
        return m.group(1).lower() if m else 'lead@example.com'

    def _clean_extracted_fragment(self, value: str) -> str:
        cleaned = re.sub(r'\s+', ' ', (value or '').strip(' \t\r\n,.;:-'))
        cleaned = re.split(r'[.,;:]\s+', cleaned, maxsplit=1)[0]
        cleaned = re.sub(r'\b(?:email|phone|call|quote|pricing|proposal|demo|meeting)\b.*$', '', cleaned, flags=re.IGNORECASE).strip(' ,.;:-')
        return cleaned

    def _extract_company(self, content: str, email: str) -> str:
        content = content or ''
        for pattern in _COMPANY_PATTERNS:
            explicit = pattern.search(content)
            if explicit:
                company = self._clean_extracted_fragment(explicit.group(1))
                if company:
                    return company
        if email and '@' in email:
            domain = email.split('@', 1)[1].split('.', 1)[0]
            return domain.replace('-', ' ').title()
        return 'Prospective Client'

    def _extract_name(self, content: str, email: str) -> str:
        content = content or ''
        for pattern in _NAME_PATTERNS:
            explicit = pattern.search(content)
            if explicit:
                name = self._clean_extracted_fragment(explicit.group(1))
                if name:
                    return name
        if email and '@' in email:
            local = email.split('@', 1)[0].replace('.', ' ').replace('_', ' ').split('+', 1)[0]
            return ' '.join(p.capitalize() for p in local.split()[:2]) or 'Prospect'
        return 'Prospect'

    def _waiting_message(self, ctx: Dict[str, Any]) -> str:
        if ctx.get('reason') == 'waiting_for_approval':
            actions = ', '.join(ctx.get('pending_actions', []))
            return f'Waiting for approval before continuing: {actions}.'
        return f"Workflow paused: {ctx.get('reason', 'unknown')}"
