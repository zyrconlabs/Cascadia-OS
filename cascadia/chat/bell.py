"""
bell/bell.py - Cascadia OS v0.2
BELL: Inbound chat interface and human-in-the-loop handler.

Owns: message ingestion from humans, chat session management,
      approval response collection, human-triggered run starts.
Does not own: operator execution (BEACON/STITCH), encryption (CURTAIN),
              storage (VAULT), external channel routing (VANGUARD).

A bell is how you get someone's attention. BELL is how humans
get the attention of Cascadia OS — and how Cascadia gets theirs back.
"""
# MATURITY: STUB — Session management and approval collection work. Real-time websocket and push are v0.3.
from __future__ import annotations

import argparse
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime


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
        self.pending_approvals: List[str] = []   # approval_ids waiting on this human
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
    Owns session management and approval response collection.
    Does not own operator execution or external channel routing.
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

        self.runtime.register_route('POST', '/session/start', self.start_session)
        self.runtime.register_route('POST', '/message', self.receive_message)
        self.runtime.register_route('POST', '/approve', self.receive_approval)
        self.runtime.register_route('GET',  '/sessions', self.list_sessions)
        self.runtime.register_route('POST', '/session/history', self.get_history)

    def start_session(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Open a new BELL chat session for a human."""
        session_id = f'bell_{uuid.uuid4().hex[:10]}'
        tenant_id = payload.get('tenant_id', 'default')
        with self._lock:
            session = ChatSession(session_id, tenant_id)
            self._sessions[session_id] = session
        self.runtime.logger.info('BELL session started: %s', session_id)
        return 201, {'session_id': session_id, 'tenant_id': tenant_id}

    def receive_message(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Receive a message from a human and queue it for operator handling.
        Returns an acknowledgement. The operator response arrives asynchronously.
        """
        session_id = payload.get('session_id', '')
        content = payload.get('content', '')
        if not session_id or not content:
            return 400, {'error': 'session_id and content required'}

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return 404, {'error': 'session not found'}
            msg = session.add_message('user', content, payload.get('metadata'))

        self.runtime.logger.info('BELL received message in session %s', session_id)
        return 202, {
            'message_id': msg['id'],
            'session_id': session_id,
            'status': 'received',
            'queued_for_operator': True,
        }

    def receive_approval(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Accept a human approval or denial for a gated action.
        Forwards the decision to approval_store via the system layer.
        """
        session_id = payload.get('session_id', '')
        approval_id = payload.get('approval_id')
        decision = payload.get('decision', '')  # 'approved' | 'denied'
        reason = payload.get('reason', '')

        if decision not in ('approved', 'denied'):
            return 400, {'error': 'decision must be approved or denied'}

        with self._lock:
            session = self._sessions.get(session_id)
            if session and approval_id in session.pending_approvals:
                session.pending_approvals.remove(approval_id)

        self.runtime.logger.info(
            'BELL approval %s: %s by session %s', approval_id, decision, session_id
        )
        # In a full build: forward to approval_store service via HTTP
        # For v0.2: return the decision for the operator to collect
        return 200, {
            'approval_id': approval_id,
            'decision': decision,
            'reason': reason,
            'recorded': True,
        }

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


def main() -> None:
    p = argparse.ArgumentParser(description='BELL - Cascadia OS chat interface')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    BellService(a.config, a.name).start()


if __name__ == '__main__':
    main()
