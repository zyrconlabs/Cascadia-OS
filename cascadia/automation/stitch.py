"""
stitch/stitch.py - Cascadia OS v0.44
STITCH: Workflow automation engine.

Owns: workflow definition loading, step sequencing, operator assignment,
      workflow run lifecycle (start/pause/resume/complete).
Does not own: step execution (operators do that), approval decisions (SENTINEL/approval_store),
              storage (VAULT), communication (BELL/VANGUARD).

STITCH connects steps, operators, triggers, and outcomes into
durable sequences. The name implies connecting things together.
"""
# MATURITY: FUNCTIONAL — Workflow definitions and run tracking work. Actual step dispatch to operators is v0.35.
from __future__ import annotations

import argparse
import json
import logging
import re as _re
import sqlite3
import threading
import time as _time
import urllib.request as _urllib_request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Sales Funnel workflow seed + execution helpers
# ---------------------------------------------------------------------------

_SALES_FUNNEL_DEF = {
    "id": "wf_sales_funnel",
    "name": "Sales Funnel — Lead to Proposal",
    "description": "Qualifies a lead, researches the company, synthesizes intelligence, generates a proposal, gets approval, and sends it.",
    "trigger": {"type": "manual", "label": "New Lead Received"},
    "input_schema": {
        "company_name":    "string (required)",
        "contact_name":    "string (optional)",
        "contact_email":   "string (required for final send)",
        "website":         "string (optional)",
        "service_interest":"string (optional)",
    },
    "steps": [
        {
            "id": "step_scout",
            "name": "Qualify Lead",
            "operator": "scout",
            "port": 7002,
            "endpoint": "POST /api/leads/inbound",
            "input_map": {
                "source": "workflow",
                "data": {
                    "company_name":    "{trigger.company_name}",
                    "contact_name":    "{trigger.contact_name}",
                    "contact_email":   "{trigger.contact_email}",
                    "service_interest":"{trigger.service_interest}",
                }
            },
            "output_key": "scout_result",
            "requires_approval": False,
            "timeout_seconds": 30,
        },
        {
            "id": "step_recon",
            "name": "Research Company",
            "operator": "recon",
            "port": 8002,
            "endpoint": "POST /api/research/company",
            "input_map": {
                "company_name": "{trigger.company_name}",
                "website":      "{trigger.website}",
                "context":      "Sales lead — {trigger.service_interest}",
                "depth":        3,
            },
            "poll_endpoint":        "GET /api/research/run/{run_id}",
            "poll_field":           "run_id",
            "poll_status_field":    "status",
            "poll_complete_value":  "complete",
            "poll_interval_seconds": 5,
            "poll_timeout_seconds":  180,
            "output_key": "recon_result",
            "requires_approval": False,
            "timeout_seconds": 200,
        },
        {
            "id": "step_chief",
            "name": "Synthesize Intelligence",
            "operator": "chief",
            "port": 8006,
            "endpoint": "POST /api/synthesize",
            "input_map": {
                "objective": "Generate executive summary for sales proposal",
                "context": [
                    {
                        "source":  "scout",
                        "label":   "Lead Information",
                        "content": "{scout_result.qualification}",
                    },
                    {
                        "source":  "recon",
                        "label":   "Company Research",
                        "content": "{recon_result.result.summary}",
                    },
                ],
            },
            "output_key": "chief_result",
            "requires_approval": False,
            "timeout_seconds": 60,
        },
        {
            "id": "step_quote",
            "name": "Generate Proposal",
            "operator": "quote",
            "port": 8007,
            "endpoint": "POST /api/task",
            "input_map": {
                "task": "Generate a professional sales proposal for {trigger.company_name}",
                "context": {
                    "company_name":  "{trigger.company_name}",
                    "contact_name":  "{trigger.contact_name}",
                    "contact_email": "{trigger.contact_email}",
                    "synthesis_id":  "{chief_result.synthesis_id}",
                    "summary":       "{chief_result.summary}",
                    "opportunity":   "{chief_result.opportunity}",
                    "approach":      "{chief_result.approach}",
                },
            },
            "output_key": "quote_result",
            "requires_approval": True,
            "approval_label": "Approve proposal for {trigger.company_name} ({trigger.contact_email})?",
            "approval_risk": "medium",
            "timeout_seconds": 3600,
        },
        {
            "id": "step_email",
            "name": "Send Proposal",
            "operator": "email",
            "port": 8010,
            "endpoint": "POST /send",
            "input_map": {
                "to":        "{trigger.contact_email}",
                "subject":   "Proposal for {trigger.company_name} — Zyrcon AI Labs",
                "body":      "Dear {trigger.contact_name},\n\nThank you for your interest. Please find our proposal below.\n\n{quote_result.result.sections.executive_summary}\n\nWe look forward to working with you.\n\nBest regards,\nZyrcon AI Labs",
                "from_name": "Zyrcon AI Labs",
            },
            "output_key": "email_result",
            "requires_approval": False,
            "timeout_seconds": 30,
        },
    ],
    "created_at": "",
    "updated_at": "",
}


def _seed_sales_funnel(db_path: str) -> None:
    """Create the Sales Funnel workflow on startup if it doesn't already exist."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT id FROM workflow_definitions WHERE id=? AND deleted_at IS NULL',
                ('wf_sales_funnel',)
            ).fetchone()
        if row:
            return
        now = datetime.now(timezone.utc).isoformat()
        wf = _SALES_FUNNEL_DEF.copy()
        wf['created_at'] = now
        wf['updated_at'] = now
        with sqlite3.connect(db_path) as conn:
            conn.execute('''
                INSERT INTO workflow_definitions
                (id, name, description, nodes, edges, viewport, created_by, is_template, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
            ''', (
                'wf_sales_funnel',
                wf['name'],
                wf['description'],
                json.dumps(wf['steps']),
                json.dumps([]),
                json.dumps({'definition': wf}),
                'system',
                0,
                now,
                now,
            ))
    except Exception as exc:
        logger.warning('STITCH: could not seed sales funnel: %s', exc)


# ---------------------------------------------------------------------------
# Workflow run execution engine
# ---------------------------------------------------------------------------

def _resolve_template(template: Any, context: dict) -> Any:
    """Resolve {path.to.value} placeholders in template strings."""
    if not isinstance(template, str):
        return template

    def replacer(match: '_re.Match') -> str:
        path = match.group(1)
        parts = path.split('.')
        val: Any = context
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p, '')
            else:
                return ''
        return str(val) if val is not None else ''

    return _re.sub(r'\{([^}]+)\}', replacer, template)


def _resolve_input_map(input_map: dict, context: dict) -> dict:
    """Resolve all template strings in an input_map dict."""
    resolved = {}
    for k, v in input_map.items():
        if isinstance(v, str):
            resolved[k] = _resolve_template(v, context)
        else:
            resolved[k] = v
    return resolved


def _call_operator(port: int, endpoint: str, payload: dict) -> dict:
    """Make HTTP call to operator. Returns response dict or error dict."""
    method, path = endpoint.split(' ', 1)
    url = f"http://127.0.0.1:{port}{path}"
    if method == 'POST':
        body = json.dumps(payload).encode()
        req = _urllib_request.Request(url, data=body, method='POST',
                                      headers={'Content-Type': 'application/json'})
    else:
        req = _urllib_request.Request(url, method='GET',
                                      headers={'Content-Type': 'application/json'})
    try:
        with _urllib_request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as exc:
        return {'error': str(exc), 'status': 'operator_error'}


def _get_nested(obj: Any, path: str) -> Any:
    """Get a nested value from a dict using dot-notation path."""
    parts = path.split('.')
    val = obj
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return None
    return val


_RUNS_FILE_LOCK = threading.Lock()


def _runs_file_path(db_path: str) -> Path:
    """Return path to the workflow_runs.json file, co-located with the DB."""
    return Path(db_path).parent / 'workflow_runs.json'


def _load_runs(db_path: str) -> dict:
    fp = _runs_file_path(db_path)
    try:
        return json.loads(fp.read_text())
    except Exception:
        return {}


def _save_runs(db_path: str, runs: dict) -> None:
    fp = _runs_file_path(db_path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(runs, indent=2))


def _update_run(db_path: str, run_id: str, updates: dict) -> None:
    with _RUNS_FILE_LOCK:
        runs = _load_runs(db_path)
        if run_id in runs:
            runs[run_id].update(updates)
            runs[run_id]['updated_at'] = _now()
            _save_runs(db_path, runs)


class WorkflowRunEngine:
    """
    Executes a sales-funnel-style workflow definition sequentially.
    Supports: polling steps, approval gates, conditional steps, timeout handling.
    State is persisted in workflow_runs.json.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def start(self, workflow_def: dict, input_data: dict) -> str:
        """Create a run record and launch execution in a background thread."""
        run_id = f'run_{uuid.uuid4().hex[:12]}'
        now = _now()
        run_record = {
            'run_id': run_id,
            'workflow_id': workflow_def.get('id', 'unknown'),
            'workflow_name': workflow_def.get('name', ''),
            'status': 'running',
            'input': input_data,
            'steps': [],
            'outputs': {},
            'created_at': now,
            'updated_at': now,
            'error': None,
            'awaiting_approval': False,
            'approval_step': None,
        }
        with _RUNS_FILE_LOCK:
            runs = _load_runs(self._db_path)
            runs[run_id] = run_record
            _save_runs(self._db_path, runs)

        thread = threading.Thread(
            target=self._execute,
            args=(run_id, workflow_def, input_data),
            daemon=True,
            name=f'wf-run-{run_id}',
        )
        thread.start()
        return run_id

    def _execute(self, run_id: str, workflow_def: dict, input_data: dict) -> None:
        steps = workflow_def.get('steps', [])
        context = {'trigger': input_data}
        step_records: List[dict] = []

        try:
            for i, step in enumerate(steps):
                step_id = step.get('id', f'step_{i}')
                step_name = step.get('name', step_id)
                port = step.get('port', 0)
                endpoint = step.get('endpoint', 'POST /')
                input_map = step.get('input_map', {})
                output_key = step.get('output_key', step_id)
                requires_approval = step.get('requires_approval', False)
                condition = step.get('condition')
                timeout_s = step.get('timeout_seconds', 30)

                step_rec = {
                    'step_id': step_id,
                    'name': step_name,
                    'status': 'running',
                    'started_at': _now(),
                    'completed_at': None,
                    'output': None,
                    'error': None,
                    'skipped': False,
                }
                step_records.append(step_rec)
                _update_run(self._db_path, run_id, {'steps': step_records, 'current_step': step_id})

                # Evaluate condition — skip step if condition is falsy
                if condition:
                    cond_val = _get_nested(context, condition)
                    if not cond_val:
                        step_rec.update({'status': 'skipped', 'skipped': True, 'completed_at': _now()})
                        _update_run(self._db_path, run_id, {'steps': step_records})
                        logger.info('STITCH run %s: step %s skipped (condition=%s falsy)', run_id, step_id, condition)
                        continue

                # Approval gate — pause run and wait
                if requires_approval:
                    approval_label = _resolve_template(
                        step.get('approval_label', f'Approve step: {step_name}'), context
                    )
                    step_rec.update({'status': 'awaiting_approval'})
                    _update_run(self._db_path, run_id, {
                        'steps': step_records,
                        'status': 'awaiting_approval',
                        'awaiting_approval': True,
                        'approval_step': step_id,
                        'approval_label': approval_label,
                    })
                    logger.info('STITCH run %s: paused at approval gate %s', run_id, step_id)

                    # Wait up to timeout for approval
                    deadline = _time.monotonic() + timeout_s
                    approved = False
                    while _time.monotonic() < deadline:
                        _time.sleep(3)
                        with _RUNS_FILE_LOCK:
                            runs = _load_runs(self._db_path)
                        run_state = runs.get(run_id, {})
                        decision = run_state.get('approval_decision')
                        if decision == 'approved':
                            approved = True
                            break
                        if decision == 'denied':
                            approved = False
                            break

                    if not approved:
                        step_rec.update({
                            'status': 'approval_denied',
                            'completed_at': _now(),
                            'error': 'approval denied or timed out',
                        })
                        _update_run(self._db_path, run_id, {
                            'steps': step_records,
                            'status': 'failed',
                            'error': f'Approval denied or timed out at step: {step_name}',
                            'awaiting_approval': False,
                        })
                        return

                    # Approval granted — clear gate state, continue
                    step_rec.update({'status': 'approved'})
                    _update_run(self._db_path, run_id, {
                        'steps': step_records,
                        'status': 'running',
                        'awaiting_approval': False,
                        'approval_decision': None,
                    })

                # Resolve inputs
                resolved_inputs = _resolve_input_map(input_map, context)

                # Execute step
                poll_endpoint = step.get('poll_endpoint')
                if poll_endpoint:
                    # Polling step — first call starts the job, then poll for completion
                    result = _call_operator(port, endpoint, resolved_inputs)
                    if 'error' in result and result.get('status') == 'operator_error':
                        step_rec.update({
                            'status': 'error',
                            'completed_at': _now(),
                            'error': result['error'],
                        })
                        _update_run(self._db_path, run_id, {
                            'steps': step_records,
                            'status': 'failed',
                            'error': f'Step {step_name} failed: {result["error"]}',
                        })
                        return

                    # Extract poll field value (e.g. run_id from response)
                    poll_field = step.get('poll_field', 'run_id')
                    poll_id = result.get(poll_field, '')
                    poll_status_field = step.get('poll_status_field', 'status')
                    poll_complete_value = step.get('poll_complete_value', 'complete')
                    poll_interval = step.get('poll_interval_seconds', 5)
                    poll_timeout = step.get('poll_timeout_seconds', 120)

                    # Resolve poll endpoint URL with run_id substituted
                    poll_path = _resolve_template(
                        poll_endpoint.split(' ', 1)[1] if ' ' in poll_endpoint else poll_endpoint,
                        {**context, 'run_id': poll_id}
                    )
                    deadline = _time.monotonic() + poll_timeout
                    while _time.monotonic() < deadline:
                        _time.sleep(poll_interval)
                        poll_result = _call_operator(port, f'GET {poll_path}', {})
                        if poll_result.get(poll_status_field) == poll_complete_value:
                            result = poll_result
                            break
                    else:
                        step_rec.update({
                            'status': 'timeout',
                            'completed_at': _now(),
                            'error': f'polling timed out after {poll_timeout}s',
                        })
                        _update_run(self._db_path, run_id, {
                            'steps': step_records,
                            'status': 'failed',
                            'error': f'Step {step_name} timed out',
                        })
                        return
                else:
                    # Simple synchronous step
                    result = _call_operator(port, endpoint, resolved_inputs)
                    if 'error' in result and result.get('status') == 'operator_error':
                        step_rec.update({
                            'status': 'error',
                            'completed_at': _now(),
                            'error': result['error'],
                        })
                        _update_run(self._db_path, run_id, {
                            'steps': step_records,
                            'status': 'failed',
                            'error': f'Step {step_name} failed: {result["error"]}',
                        })
                        return

                # Store output
                context[output_key] = result
                step_rec.update({
                    'status': 'complete',
                    'completed_at': _now(),
                    'output': result,
                })
                with _RUNS_FILE_LOCK:
                    runs = _load_runs(self._db_path)
                    if run_id in runs:
                        runs[run_id]['steps'] = step_records
                        runs[run_id]['outputs'][output_key] = result
                        runs[run_id]['updated_at'] = _now()
                        _save_runs(self._db_path, runs)
                logger.info('STITCH run %s: step %s complete', run_id, step_id)

            # All steps complete
            _update_run(self._db_path, run_id, {
                'status': 'complete',
                'steps': step_records,
                'completed_at': _now(),
            })
            logger.info('STITCH run %s: workflow complete', run_id)

        except Exception as exc:
            logger.error('STITCH run %s: unhandled error: %s', run_id, exc)
            _update_run(self._db_path, run_id, {
                'status': 'failed',
                'error': str(exc),
            })


# ---------------------------------------------------------------------------
# Workflow definition model
# ---------------------------------------------------------------------------

class WorkflowStep:
    """One step in a STITCH workflow. Owns step metadata. Does not own execution."""

    def __init__(self, name: str, operator: str, action: str,
                 inputs: Optional[Dict] = None, on_failure: str = 'stop') -> None:
        self.name = name
        self.operator = operator    # Which operator runs this step
        self.action = action        # What action the operator performs
        self.inputs = inputs or {}
        self.on_failure = on_failure  # 'stop' | 'skip' | 'retry'


class WorkflowDefinition:
    """
    A named, reusable workflow template.
    Owns: step sequence, operator assignments, trigger conditions.
    Does not own: run state or execution.
    """

    def __init__(self, workflow_id: str, name: str, steps: List[WorkflowStep],
                 description: str = '') -> None:
        self.workflow_id = workflow_id
        self.name = name
        self.steps = steps
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        return {
            'workflow_id': self.workflow_id,
            'name': self.name,
            'description': self.description,
            'step_count': len(self.steps),
            'steps': [
                {'name': s.name, 'operator': s.operator,
                 'action': s.action, 'on_failure': s.on_failure}
                for s in self.steps
            ],
        }


class WorkflowRun:
    """
    One active execution of a workflow definition.
    Owns: run state and progress tracking.
    Does not own: actual step execution (operators do that via BEACON).
    """

    def __init__(self, run_id: str, workflow_id: str, tenant_id: str,
                 goal: str, total_steps: int) -> None:
        self.run_id = run_id
        self.workflow_id = workflow_id
        self.tenant_id = tenant_id
        self.goal = goal
        self.total_steps = total_steps
        self.current_step = 0
        self.state = 'pending'      # pending/running/paused/complete/failed
        self.created_at = _now()
        self.updated_at = _now()
        self.error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'run_id': self.run_id,
            'workflow_id': self.workflow_id,
            'tenant_id': self.tenant_id,
            'goal': self.goal,
            'state': self.state,
            'run_state': self.state,
            'current_step': self.current_step,
            'total_steps': self.total_steps,
            'progress_pct': int(self.current_step / max(self.total_steps, 1) * 100),
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'error': self.error,
        }


# ---------------------------------------------------------------------------
# STITCH service
# ---------------------------------------------------------------------------

class WorkflowStore:
    """Owns workflow definition persistence. Does not own execution."""

    def __init__(self, db_path: str) -> None:
        self._db = db_path

    def save(self, workflow_id: str, name: str, nodes: list,
             edges: list, viewport: dict = None,
             description: str = '', created_by: str = 'user') -> dict:
        import json
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            'id': workflow_id, 'name': name,
            'description': description,
            'nodes': json.dumps(nodes),
            'edges': json.dumps(edges),
            'viewport': json.dumps(viewport or {}),
            'created_by': created_by,
            'is_template': 0, 'deleted_at': None,
            'created_at': now, 'updated_at': now
        }
        with sqlite3.connect(self._db) as conn:
            conn.execute('''
                INSERT INTO workflow_definitions
                (id, name, description, nodes, edges, viewport, created_by, is_template, created_at, updated_at)
                VALUES (:id, :name, :description, :nodes, :edges, :viewport, :created_by, :is_template, :created_at, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name, description=excluded.description,
                  nodes=excluded.nodes, edges=excluded.edges,
                  viewport=excluded.viewport, updated_at=excluded.updated_at
            ''', payload)
        return self.get(workflow_id)

    def get(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        import json
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT * FROM workflow_definitions WHERE id=? AND deleted_at IS NULL',
                (workflow_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        for f in ('nodes', 'edges', 'viewport'):
            try: d[f] = json.loads(d[f])
            except Exception: d[f] = [] if f != 'viewport' else {}
        return d

    def list_all(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                'SELECT id, name, description, created_by, is_template, created_at, updated_at FROM workflow_definitions WHERE deleted_at IS NULL ORDER BY updated_at DESC'
            ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, workflow_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db) as conn:
            cursor = conn.execute(
                'UPDATE workflow_definitions SET deleted_at=? WHERE id=? AND deleted_at IS NULL',
                (now, workflow_id)
            )
        return cursor.rowcount > 0

    def list_templates(self) -> List[Dict[str, Any]]:
        import json
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                'SELECT * FROM workflow_definitions WHERE is_template=1 AND deleted_at IS NULL ORDER BY name'
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            for f in ('nodes', 'edges', 'viewport'):
                try: d[f] = json.loads(d[f])
                except Exception: d[f] = [] if f != 'viewport' else {}
            result.append(d)
        return result


class StitchService:
    """
    STITCH - Workflow automation service.
    Owns workflow definitions and run tracking.
    Does not own step execution, approval decisions, or storage.
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
        self._workflows: Dict[str, WorkflowDefinition] = {}
        self._runs: Dict[str, WorkflowRun] = {}
        self._db_path = self.config.get('database_path', './data/runtime/cascadia.db')

        # Scheduler for recurring workflow triggers
        from cascadia.automation.scheduler import Scheduler
        self._scheduler = Scheduler()
        self._register_scheduled_jobs()

        # Register built-in workflows
        self._register_builtins()

        # WorkflowStore for persistent designer-created workflows
        self._wf_store = WorkflowStore(self._db_path)
        self.runtime.register_route('GET',    '/api/stitch/workflows',        self._wf_list)
        self.runtime.register_route('POST',   '/api/stitch/workflows',        self._wf_save)
        self.runtime.register_route('GET',    '/api/stitch/workflows/{id}',   self._wf_get)
        self.runtime.register_route('DELETE', '/api/stitch/workflows/{id}',   self._wf_delete)
        self.runtime.register_route('GET',    '/api/stitch/templates',        self._wf_templates)
        self.runtime.register_route('POST',   '/api/stitch/resume',           self._resume_interrupted_runs)

        self.runtime.register_route('POST', '/workflow/register', self.register_workflow)
        self.runtime.register_route('GET',  '/workflow/list', self.list_workflows)
        self.runtime.register_route('POST', '/run/start', self.start_run)
        self.runtime.register_route('POST', '/run/advance', self.advance_run)
        self.runtime.register_route('POST', '/run/pause', self.pause_run)
        self.runtime.register_route('POST', '/run/status', self.run_status)
        self.runtime.register_route('GET',  '/run/active',   self.active_runs)
        self.runtime.register_route('POST', '/run/execute',  self.execute_run)
        self.runtime.register_route('POST', '/run/resume',   self.resume_run)
        self.runtime.register_route('GET',  '/scheduler/jobs', self.scheduler_list)
        self.runtime.register_route('POST', '/scheduler/enable', self.scheduler_enable)
        # REST API (iOS + designer)
        self.runtime.register_route('GET',  '/api/workflows',             self.api_list_workflows)
        self.runtime.register_route('GET',  '/api/workflows/{id}',        self.api_get_workflow)
        self.runtime.register_route('POST', '/api/workflows/{id}/run',    self.api_run_workflow)
        self.runtime.register_route('GET',  '/api/workflows/{id}/runs',   self.api_list_runs)
        self.runtime.register_route('GET',  '/designer',                  self.serve_designer)
        # Sales Funnel execution engine routes
        self.runtime.register_route('GET',  '/api/workflows/runs/{run_id}',         self.api_get_run)
        self.runtime.register_route('POST', '/api/workflows/runs/{run_id}/approve', self.api_approve_run)

        # Seed the Sales Funnel workflow
        _seed_sales_funnel(self._db_path)

    def _register_builtins(self) -> None:
        """Register built-in workflow templates."""
        lead_follow_up = WorkflowDefinition(
            workflow_id='lead_follow_up',
            name='Lead Follow-Up',
            description='Parse a lead, enrich company data, draft and send an outreach email, log to CRM.',
            steps=[
                WorkflowStep('parse_lead',      'main_operator',   'parse_lead'),
                WorkflowStep('enrich_company',  'main_operator',   'enrich_company'),
                WorkflowStep('draft_email',     'main_operator',   'draft_email'),
                WorkflowStep('send_email',      'gmail_operator',  'email.send',      on_failure='stop'),
                WorkflowStep('log_crm',         'main_operator',   'crm.write'),
            ],
        )
        self._workflows['lead_follow_up'] = lead_follow_up

        calendar_check = WorkflowDefinition(
            workflow_id='calendar_check',
            name='Calendar Check',
            description='Read upcoming events and produce a daily briefing.',
            steps=[
                WorkflowStep('read_events',    'calendar_operator', 'calendar.read'),
                WorkflowStep('draft_briefing', 'main_operator',     'draft_briefing'),
            ],
        )
        self._workflows['calendar_check'] = calendar_check

    def register_workflow(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        wf_id = payload.get('workflow_id', f'wf_{uuid.uuid4().hex[:8]}')
        steps = [WorkflowStep(**s) for s in payload.get('steps', [])]
        wf = WorkflowDefinition(
            workflow_id=wf_id,
            name=payload.get('name', wf_id),
            steps=steps,
            description=payload.get('description', ''),
        )
        with self._lock:
            self._workflows[wf_id] = wf
        return 201, {'workflow_id': wf_id, 'step_count': len(steps)}

    def list_workflows(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        with self._lock:
            workflows = [wf.to_dict() for wf in self._workflows.values()]
        return 200, {'workflows': workflows, 'count': len(workflows)}

    def start_run(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        workflow_id = payload.get('workflow_id', '')
        with self._lock:
            wf = self._workflows.get(workflow_id)
        if wf is None:
            return 404, {'error': f'workflow not found: {workflow_id}'}

        run_id = f'stitch_{uuid.uuid4().hex[:10]}'
        run = WorkflowRun(
            run_id=run_id,
            workflow_id=workflow_id,
            tenant_id=payload.get('tenant_id', 'default'),
            goal=payload.get('goal', wf.name),
            total_steps=len(wf.steps),
        )
        run.state = 'running'
        run.updated_at = _now()

        with self._lock:
            self._runs[run_id] = run

        self.runtime.logger.info('STITCH run started: %s (%s)', run_id, workflow_id)
        return 202, run.to_dict()

    def advance_run(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Mark the current step complete and advance to the next."""
        run_id = payload.get('run_id', '')
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            return 404, {'error': 'run not found'}

        run.current_step += 1
        run.updated_at = _now()
        if run.current_step >= run.total_steps:
            run.state = 'complete'
            self.runtime.logger.info('STITCH run complete: %s', run_id)
        return 200, run.to_dict()

    def pause_run(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        run_id = payload.get('run_id', '')
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            return 404, {'error': 'run not found'}
        run.state = 'paused'
        run.updated_at = _now()
        return 200, run.to_dict()

    def run_status(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        run_id = payload.get('run_id', '')
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            return 404, {'error': 'run not found'}
        return 200, run.to_dict()

    def active_runs(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        with self._lock:
            active = [r.to_dict() for r in self._runs.values() if r.state == 'running']
        return 200, {'active_runs': active, 'count': len(active)}


    def execute_run(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Execute a workflow run via WorkflowRuntime.
        Delegates durable execution to WorkflowRuntime — STITCH owns the
        workflow definition, WorkflowRuntime owns durable step execution.
        """
        from cascadia.automation.workflow_runtime import WorkflowRuntime
        workflow_id = payload.get('workflow_id', 'lead_follow_up')
        with self._lock:
            definition = self._workflows.get(workflow_id)
        if definition is None:
            return 404, {'error': f'workflow not found: {workflow_id}'}
        db_path = self.config.get('database_path', './data/runtime/cascadia.db')
        try:
            runtime = WorkflowRuntime(db_path)
            result = runtime.execute(workflow_id, definition, payload)
            return 200, result.to_dict()
        except Exception as exc:
            return 500, {'error': str(exc)}

    def resume_run(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """
        Resume a workflow run after approval or restart.
        Looks up the workflow_id from the run record, then re-executes.
        """
        from cascadia.automation.workflow_runtime import WorkflowRuntime
        from cascadia.durability.run_store import RunStore
        run_id = payload.get('run_id', '')
        if not run_id:
            return 400, {'error': 'run_id required'}
        db_path = self.config.get('database_path', './data/runtime/cascadia.db')
        try:
            store = RunStore(db_path)
            run = store.get_run(run_id)
            if run is None:
                return 404, {'error': f'run not found: {run_id}'}
            workflow_id = run.get('goal', '').split(':')[0].strip() or 'lead_follow_up'
            with self._lock:
                definition = self._workflows.get(workflow_id) or self._workflows.get('lead_follow_up')
            runtime = WorkflowRuntime(db_path)
            result = runtime.execute(workflow_id or 'lead_follow_up', definition, {'run_id': run_id})
            return 200, result.to_dict()
        except Exception as exc:
            return 500, {'error': str(exc)}

    # ------------------------------------------------------------------
    # REST API handlers (iOS app + designer)
    # ------------------------------------------------------------------

    def api_list_workflows(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/workflows — list workflows in mobile-friendly format."""
        with self._lock:
            workflows = [wf.to_dict() for wf in self._workflows.values()]
        return 200, {'workflows': workflows, 'count': len(workflows)}

    def api_get_workflow(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/workflows/{id}"""
        wf_id = payload.get('id', '')
        with self._lock:
            wf = self._workflows.get(wf_id)
        if wf is None:
            return 404, {'error': f'workflow not found: {wf_id}'}
        return 200, wf.to_dict()

    def api_list_runs(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/workflows/{id}/runs — list runs for a specific workflow."""
        wf_id = payload.get('id', '')
        with self._lock:
            runs = [r.to_dict() for r in self._runs.values() if r.workflow_id == wf_id]
        runs.sort(key=lambda r: r.get('created_at', ''), reverse=True)
        return 200, {'runs': runs, 'count': len(runs), 'workflow_id': wf_id}

    def api_run_workflow(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/workflows/{id}/run — start a run for a specific workflow.
        For the Sales Funnel (wf_sales_funnel), uses WorkflowRunEngine for real execution.
        Falls back to the lightweight in-memory run for other workflows.
        """
        wf_id = payload.get('id', '')
        input_data = payload.get('input', {})

        # Sales Funnel — use the execution engine
        if wf_id == 'wf_sales_funnel':
            engine = WorkflowRunEngine(self._db_path)
            run_id = engine.start(_SALES_FUNNEL_DEF, input_data)
            return 202, {
                'run_id': run_id,
                'workflow_id': wf_id,
                'status': 'running',
                'message': 'Sales Funnel workflow started',
            }

        # Fallback — in-memory lightweight run
        with self._lock:
            wf = self._workflows.get(wf_id)
        if wf is None:
            return 404, {'error': f'workflow not found: {wf_id}'}
        run_id = f'stitch_{uuid.uuid4().hex[:10]}'
        run = WorkflowRun(
            run_id=run_id,
            workflow_id=wf_id,
            tenant_id=payload.get('tenant_id', 'default'),
            goal=payload.get('goal', wf.name),
            total_steps=len(wf.steps),
        )
        run.state = 'running'
        run.updated_at = _now()
        with self._lock:
            self._runs[run_id] = run
        self.runtime.logger.info('STITCH api run: %s (%s)', run_id, wf_id)
        return 202, run.to_dict()

    def api_get_run(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/workflows/runs/{run_id} — return full run state from workflow_runs.json."""
        run_id = payload.get('run_id', '')
        if not run_id:
            return 400, {'error': 'run_id required'}
        with _RUNS_FILE_LOCK:
            runs = _load_runs(self._db_path)
        run = runs.get(run_id)
        if run is None:
            return 404, {'error': f'run not found: {run_id}'}
        return 200, run

    def api_approve_run(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/workflows/runs/{run_id}/approve — resume a paused run waiting for approval."""
        run_id = payload.get('run_id', '')
        approved = payload.get('approved', False)
        note = payload.get('note', '')
        if not run_id:
            return 400, {'error': 'run_id required'}
        with _RUNS_FILE_LOCK:
            runs = _load_runs(self._db_path)
        run = runs.get(run_id)
        if run is None:
            return 404, {'error': f'run not found: {run_id}'}
        if run.get('status') != 'awaiting_approval':
            return 400, {'error': f'run is not awaiting approval (status: {run.get("status")})'}
        decision = 'approved' if approved else 'denied'
        with _RUNS_FILE_LOCK:
            runs = _load_runs(self._db_path)
            if run_id in runs:
                runs[run_id]['approval_decision'] = decision
                runs[run_id]['approval_note'] = note
                runs[run_id]['approval_decided_at'] = _now()
                runs[run_id]['updated_at'] = _now()
                _save_runs(self._db_path, runs)
        self.runtime.logger.info('STITCH run %s: approval decision=%s note=%s', run_id, decision, note)
        return 200, {'run_id': run_id, 'decision': decision, 'recorded_at': _now()}

    def serve_designer(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /designer — serve the workflow designer HTML."""
        from pathlib import Path as _Path
        html_path = _Path(__file__).parent / 'templates' / 'workflow_designer.html'
        try:
            return 200, {'__html__': html_path.read_bytes()}
        except FileNotFoundError:
            return 404, {'error': 'designer template not found'}

    def _register_scheduled_jobs(self) -> None:
        """Register default recurring jobs. Config overrides can be added externally."""
        morning_brief_time = self.config.get('scheduler', {}).get('morning_brief_time', '07:00')
        self._scheduler.add_job(
            name='morning_brief',
            schedule=morning_brief_time,
            trigger_fn=lambda: self._trigger_workflow_by_id('calendar_check', {'goal': 'morning_brief'}),
        )
        weekly_time = self.config.get('scheduler', {}).get('weekly_summary_time', 'FRI 17:00')
        self._scheduler.add_job(
            name='weekly_summary',
            schedule=weekly_time,
            trigger_fn=self._trigger_weekly_summary,
        )

    def _trigger_workflow_by_id(self, workflow_id: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Trigger a registered workflow by ID. Used by the scheduler."""
        with self._lock:
            wf = self._workflows.get(workflow_id)
        if wf is None:
            self.runtime.logger.warning('Scheduler: workflow not found: %s', workflow_id)
            return
        run_id = f'sched_{uuid.uuid4().hex[:10]}'
        run = WorkflowRun(
            run_id=run_id,
            workflow_id=workflow_id,
            tenant_id=(payload or {}).get('tenant_id', 'default'),
            goal=(payload or {}).get('goal', wf.name),
            total_steps=len(wf.steps),
        )
        run.state = 'running'
        run.updated_at = _now()
        with self._lock:
            self._runs[run_id] = run
        self.runtime.logger.info('Scheduler fired: %s → run %s', workflow_id, run_id)

    def _trigger_weekly_summary(self) -> None:
        """Trigger the weekly summary report via WeeklySummaryReport."""
        try:
            from cascadia.reports.weekly_summary import WeeklySummaryReport
            db_path = self.config.get('database_path', './data/runtime/cascadia.db')
            reports_dir = self.config.get('reports_dir', './data/reports')
            email = self.config.get('weekly_summary_email', '')
            rpt = WeeklySummaryReport(
                database_path=db_path,
                reports_dir=reports_dir,
                delivery_email=email,
            )
            dest = rpt.deliver()
            self.runtime.logger.info('Weekly summary delivered to: %s', dest)
        except Exception as exc:
            self.runtime.logger.error('Weekly summary error: %s', exc)

    # ------------------------------------------------------------------
    # WorkflowStore handlers
    # ------------------------------------------------------------------

    def _wf_list(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        try:
            workflows = self._wf_store.list_all()
            return 200, {'workflows': workflows, 'count': len(workflows)}
        except Exception as e:
            return 500, {'error': str(e)}

    def _wf_save(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        wf_id = payload.get('id', f'wf_{uuid.uuid4().hex[:10]}')
        name  = payload.get('name', 'Untitled Workflow')
        try:
            result = self._wf_store.save(
                workflow_id=wf_id,
                name=name,
                nodes=payload.get('nodes', []),
                edges=payload.get('edges', []),
                viewport=payload.get('viewport', {}),
                description=payload.get('description', ''),
                created_by=payload.get('created_by', 'user'),
            )
            return 200, result or {'id': wf_id, 'name': name}
        except Exception as e:
            return 500, {'error': str(e)}

    def _wf_get(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        wf_id = payload.get('id', '')
        try:
            result = self._wf_store.get(wf_id)
            if result is None:
                return 404, {'error': f'workflow not found: {wf_id}'}
            return 200, result
        except Exception as e:
            return 500, {'error': str(e)}

    def _wf_delete(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        wf_id = payload.get('id', '')
        try:
            deleted = self._wf_store.delete(wf_id)
            if not deleted:
                return 404, {'error': f'workflow not found: {wf_id}'}
            return 200, {'deleted': True, 'id': wf_id}
        except Exception as e:
            return 500, {'error': str(e)}

    def _wf_templates(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        try:
            templates = self._wf_store.list_templates()
            return 200, {'templates': templates, 'count': len(templates)}
        except Exception as e:
            return 500, {'error': str(e)}

    def _resume_interrupted_runs(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        from cascadia.durability.run_store import RunStore
        try:
            store = RunStore(self._db_path)
            interrupted = store.get_runs_by_status(['running', 'waiting_human'])
        except Exception:
            interrupted = []
        resumed = []
        failed = []
        for run in interrupted:
            run_id = run.get('run_id', '')
            try:
                self.runtime.logger.info('STITCH: resuming interrupted run %s', run_id)
                code, result = self.resume_run({'run_id': run_id})
                if code == 200:
                    resumed.append(run_id)
                else:
                    failed.append(run_id)
            except Exception as e:
                self.runtime.logger.error('STITCH: resume failed %s: %s', run_id, e)
                failed.append(run_id)
        return 200, {'resumed': len(resumed), 'failed': len(failed), 'run_ids': resumed}

    def scheduler_list(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {'jobs': self._scheduler.list_jobs(), 'generated_at': _now()}

    def scheduler_enable(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        name = payload.get('name', '')
        enabled = bool(payload.get('enabled', True))
        with self._scheduler._lock:
            job = self._scheduler._jobs.get(name)
        if job is None:
            return 404, {'error': f'job not found: {name}'}
        job.enabled = enabled
        return 200, {'name': name, 'enabled': enabled}

    def _schedule_daily_backup(self):
        import threading, time
        def _backup_loop():
            while True:
                now = __import__('datetime').datetime.now()
                seconds_until_3am = ((3 - now.hour) % 24) * 3600 - now.minute * 60 - now.second
                if seconds_until_3am <= 0:
                    seconds_until_3am += 86400
                time.sleep(seconds_until_3am)
                try:
                    from cascadia.durability.backup import BackupManager
                    db = self.config.get('database_path', './data/runtime/cascadia.db')
                    bdir = self.config.get('backup_dir', './data/backups')
                    retention = self.config.get('backup_retention_days', 30)
                    mgr = BackupManager(db, bdir, retention)
                    mgr.create_backup()
                    mgr.purge_old()
                except Exception as e:
                    logger.error('Backup failed: %s', e)
        threading.Thread(target=_backup_loop, daemon=True, name='backup').start()

    def start(self) -> None:
        self._scheduler.start()
        self._schedule_daily_backup()
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='STITCH - Cascadia OS workflow automation')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    StitchService(a.config, a.name).start()


if __name__ == '__main__':
    main()
