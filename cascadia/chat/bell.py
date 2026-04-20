"""
bell/bell.py - Cascadia OS v0.43
BELL: Inbound chat interface and human-in-the-loop handler.

Owns: message ingestion from humans, chat session management,
      approval response collection, human-triggered run starts,
      workflow execution via WorkflowRuntime.
Does not own: operator execution scheduling (BEACON/STITCH),
              encryption (CURTAIN), storage (VAULT),
              external channel routing (VANGUARD).

A bell is how you get someone's attention. BELL is how humans
get the attention of Cascadia OS — and how Cascadia gets theirs back.
"""
# MATURITY: PRODUCTION — Session management, workflow execution, and approval
# collection fully wired to WorkflowRuntime and ApprovalStore.
from __future__ import annotations

import argparse
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cascadia.automation.stitch import StitchService
from cascadia.automation.workflow_runtime import WorkflowRuntime
from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime
from cascadia.system.approval_store import ApprovalStore
from cascadia.durability.run_store import RunStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatSession:
    """Owns state for one human conversation session. Does not own operator execution."""

    def __init__(self, session_id: str, tenant_id: str = 'default') -> None:
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.created_at = _now()
        self.last_active = time.time()
        self.messages: List[Dict[str, Any]] = []
        self.pending_approvals: List[int] = []   # approval_ids waiting on this human
        self.linked_run_ids: List[str] = []

    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        msg = {
            'id': uuid.uuid4().hex[:8],
            'session_id': self.session_id,
            'role': role,          # 'user' | 'assistant' | 'system'
            'content': content,
            'ts': _now(),
            'metadata': metadata or {},
        }
        self.messages.append(msg)
        self.last_active = time.time()
        return msg

    def to_dict(self) -> Dict[str, Any]:
        return {
            'session_id': self.session_id,
            'tenant_id': self.tenant_id,
            'created_at': self.created_at,
            'message_count': len(self.messages),
            'pending_approvals': self.pending_approvals,
            'linked_runs': self.linked_run_ids,
        }


class BellService:
    """
    BELL - Inbound chat and human-in-the-loop interface.
    Owns session management, workflow execution, and approval response collection.
    Does not own external channel routing or operator scheduling.
    """

    def __init__(self, config_path: str, name: str) -> None:
        self.config = load_config(config_path)
        component = next(c for c in self.config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=component['port'],
            heartbeat_file=component['heartbeat_file'],
            log_dir=self.config['log_dir'],
        )
        self._lock = threading.Lock()
        self._sessions: Dict[str, ChatSession] = {}

        # WorkflowRuntime — owns durable execution
        db_path = self.config.get('database_path', './data/runtime/cascadia.db')
        self._wf_runtime = WorkflowRuntime(db_path)

        # Build workflow definition map from STITCH builtins
        self._stitch = _StitchShim()

        self.runtime.register_route('POST', '/session/start',   self.start_session)
        self.runtime.register_route('POST', '/message',         self.receive_message)
        self.runtime.register_route('POST', '/approve',         self.receive_approval)
        self.runtime.register_route('GET',  '/sessions',        self.list_sessions)
        self.runtime.register_route('POST', '/session/history', self.get_history)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def start_session(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Open a new BELL chat session for a human."""
        session_id = f'bell_{uuid.uuid4().hex[:10]}'
        tenant_id = payload.get('tenant_id', 'default')
        with self._lock:
            session = ChatSession(session_id, tenant_id)
            self._sessions[session_id] = session
        self.runtime.logger.info('BELL session started: %s', session_id)
        return 201, {'session_id': session_id, 'tenant_id': tenant_id}

    # ------------------------------------------------------------------
    # Message ingestion → WorkflowRuntime execution
    # ------------------------------------------------------------------

    def receive_message(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Receive a message from a human, start a workflow run via WorkflowRuntime,
        and return the run state immediately.

        If workflow_id is provided in the payload, runs that specific workflow.
        Defaults to 'lead_follow_up'.
        """
        session_id = payload.get('session_id', '')
        content = payload.get('content', '')
        if not session_id or not content:
            return 400, {'error': 'session_id and content required'}

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return 404, {'error': 'session not found'}
            session.add_message('user', content, payload.get('metadata'))

        workflow_id = payload.get('workflow_id', 'lead_follow_up')
        definition = self._stitch.get_definition(workflow_id)
        if definition is None:
            return 400, {'error': f'unknown workflow: {workflow_id}'}

        try:
            result = self._wf_runtime.execute(workflow_id, definition, {
                'session_id': session_id,
                'content': content,
                'tenant_id': payload.get('tenant_id', session.tenant_id),
                'goal': payload.get('goal', f'Lead follow-up from BELL session {session_id}'),
                'sender': 'bell',
            })
        except Exception as exc:
            self.runtime.logger.error('BELL workflow execution failed: %s', exc)
            return 500, {'error': str(exc)}

        result_dict = result.to_dict()
        run_id = result_dict['run_id']

        with self._lock:
            if run_id not in session.linked_run_ids:
                session.linked_run_ids.append(run_id)
            # If waiting for approval, track the approval_id in session
            approval_id = result_dict.get('pending_approval_id')
            if approval_id is not None and approval_id not in session.pending_approvals:
                session.pending_approvals.append(approval_id)
            # Add assistant message from the workflow result
            assistant_msg = result_dict.get('assistant_message') or result_dict.get('draft_preview', '')
            if assistant_msg:
                session.add_message('assistant', assistant_msg)

        self.runtime.logger.info(
            'BELL run %s started — state: %s step: %s',
            run_id, result_dict['run_state'], result_dict['current_step'],
        )
        return 202, result_dict

    # ------------------------------------------------------------------
    # Approval collection → ApprovalStore → WorkflowRuntime resume
    # ------------------------------------------------------------------

    def receive_approval(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Accept a human approval or denial.
        1. Records decision in ApprovalStore (wakes the run to 'retrying' state)
        2. Re-executes WorkflowRuntime with the run_id so the workflow resumes
        3. Returns the final run state
        """
        session_id  = payload.get('session_id', '')
        approval_id = payload.get('approval_id')
        decision    = payload.get('decision', '')   # 'approved' | 'denied'
        reason      = payload.get('reason', '')
        actor       = payload.get('actor', 'operator')
        run_id      = payload.get('run_id', '')

        if decision not in ('approved', 'denied'):
            return 400, {'error': 'decision must be approved or denied'}
        if approval_id is None:
            return 400, {'error': 'approval_id required'}

        # 1. Record decision — wakes run to 'retrying' if approved
        try:
            self._wf_runtime.approvals.record_decision(
                int(approval_id), decision, actor, reason
            )
        except Exception as exc:
            self.runtime.logger.error('BELL approval record failed: %s', exc)
            return 500, {'error': f'failed to record decision: {exc}'}

        # Remove from session pending list
        with self._lock:
            session = self._sessions.get(session_id)
            if session and approval_id in session.pending_approvals:
                session.pending_approvals.remove(approval_id)

        # 2. If approved, find run_id and resume execution
        # Prefer explicit run_id; fall back to most recent linked run in session
        resume_result: Optional[Dict[str, Any]] = None
        if decision == 'approved':
            effective_run_id = run_id
            if not effective_run_id:
                with self._lock:
                    if session and session.linked_run_ids:
                        effective_run_id = session.linked_run_ids[-1]
            if effective_run_id:
                workflow_id = 'lead_follow_up'
                definition = self._stitch.get_definition(workflow_id)
                if definition:
                    try:
                        result = self._wf_runtime.execute(workflow_id, definition, {
                            'run_id': effective_run_id,
                        })
                        resume_result = result.to_dict()
                        # Add final assistant message to session
                        with self._lock:
                            if session:
                                msg = resume_result.get('assistant_message') or resume_result.get('draft_preview', '')
                                if msg:
                                    session.add_message('assistant', msg)
                        self.runtime.logger.info(
                            'BELL run %s resumed — state: %s', effective_run_id, resume_result['run_state']
                        )
                    except Exception as exc:
                        self.runtime.logger.error('BELL resume failed: %s', exc)

        return 200, {
            'approval_id': approval_id,
            'decision': decision,
            'reason': reason,
            'recorded': True,
            'resume_result': resume_result,
        }

    # ------------------------------------------------------------------
    # Session queries
    # ------------------------------------------------------------------

    def list_sessions(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        with self._lock:
            sessions = [s.to_dict() for s in self._sessions.values()]
        return 200, {'sessions': sessions, 'count': len(sessions)}

    def get_history(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        session_id = payload.get('session_id', '')
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            return 404, {'error': 'session not found'}
        return 200, {'session_id': session_id, 'messages': session.messages}

    def start(self) -> None:
        self.runtime.start()


# ---------------------------------------------------------------------------
# Lightweight shim — gives BELL access to STITCH workflow definitions
# without requiring a running STITCH service
# ---------------------------------------------------------------------------

class _StitchShim:
    """
    Provides workflow definitions to BELL without requiring HTTP to STITCH.
    Mirrors the built-in workflows registered in StitchService._register_builtins().
    Owns: definition lookup. Does not own workflow state or execution.
    """

    def __init__(self) -> None:
        from cascadia.automation.stitch import WorkflowDefinition, WorkflowStep
        self._definitions: Dict[str, Any] = {}

        lead = WorkflowDefinition(
            workflow_id='lead_follow_up',
            name='Lead Follow-Up',
            description='Parse lead, enrich, draft email, approval gate, send, log CRM.',
            steps=[
                WorkflowStep('parse_lead',     'main_operator',  'parse_lead'),
                WorkflowStep('enrich_company', 'main_operator',  'enrich_company'),
                WorkflowStep('draft_email',    'main_operator',  'draft_email'),
                WorkflowStep('send_email',     'gmail_operator', 'email.send',  on_failure='stop'),
                WorkflowStep('log_crm',        'main_operator',  'crm.write'),
            ],
        )
        self._definitions['lead_follow_up'] = lead

    def get_definition(self, workflow_id: str) -> Optional[Any]:
        return self._definitions.get(workflow_id)


def main() -> None:
    p = argparse.ArgumentParser(description='BELL - Cascadia OS chat interface')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    BellService(a.config, a.name).start()


if __name__ == '__main__':
    main()
