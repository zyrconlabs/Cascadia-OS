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
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

        # Scheduler for recurring workflow triggers
        from cascadia.automation.scheduler import Scheduler
        self._scheduler = Scheduler()
        self._register_scheduled_jobs()

        # Register built-in workflows
        self._register_builtins()

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

    def api_run_workflow(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """POST /api/workflows/{id}/run — start a run for a specific workflow."""
        wf_id = payload.get('id', '')
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

    def api_list_runs(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """GET /api/workflows/{id}/runs — list runs for a specific workflow."""
        wf_id = payload.get('id', '')
        with self._lock:
            runs = [r.to_dict() for r in self._runs.values() if r.workflow_id == wf_id]
        runs.sort(key=lambda r: r.get('created_at', ''), reverse=True)
        return 200, {'runs': runs, 'count': len(runs), 'workflow_id': wf_id}

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

    def start(self) -> None:
        self._scheduler.start()
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='STITCH - Cascadia OS workflow automation')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    StitchService(a.config, a.name).start()


if __name__ == '__main__':
    main()
