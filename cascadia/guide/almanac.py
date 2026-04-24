"""
almanac/almanac.py - Cascadia OS v0.44
ALMANAC: Field guide, documentation, and operator knowledge base.

Owns: component catalog, operator playbooks, term definitions,
      runbook entries, and queryable reference data about the OS.
Does not own: runtime execution, storage of business data (VAULT),
              or communication (VANGUARD/BELL).

An almanac is a reference you consult regularly.
ALMANAC is what you open when you need to know what something does,
how to fix it, or what a term means.
"""
# MATURITY: FUNCTIONAL — Catalog, glossary, and runbook queryable. Auto-refresh from live system is v0.3.
from __future__ import annotations

import argparse
from typing import Any, Dict, List, Optional

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime

# ---------------------------------------------------------------------------
# Built-in knowledge base
# ---------------------------------------------------------------------------

COMPONENT_CATALOG: Dict[str, Dict[str, Any]] = {
    'FLINT': {
        'name': 'FLINT',
        'layer': 'OS / Control',
        'description': 'Server and OS control layer. Supervises all component processes, manages startup tiers, health monitoring, restart/backoff, and graceful shutdown.',
        'owns': ['Process lifecycle', 'Tiered startup', 'Health polling', 'Restart and backoff', 'Graceful drain and shutdown'],
        'does_not_own': ['Workflow planning', 'Scheduler logic', 'Approval UI', 'Store mechanics'],
        'start_command': 'python -m cascadia.watchdog --config config.json',
        'status_endpoint': '/api/flint/status',
    },
    'PRISM': {
        'name': 'PRISM',
        'layer': 'Dashboard',
        'description': 'Command center and visibility layer. Aggregates status from all Cascadia OS components into one queryable API. A non-technical user should understand system state from PRISM alone.',
        'owns': ['Status aggregation', 'Run visibility', 'Approval queue display', 'Blocked run display', 'Crew membership view'],
        'does_not_own': ['Execution', 'Storage', 'Encryption', 'Communication'],
        'status_endpoint': '/api/prism/overview',
    },
    'BELL': {
        'name': 'BELL',
        'layer': 'Communication',
        'description': 'Inbound chat interface and human-in-the-loop handler. How humans get the attention of Cascadia OS — and how Cascadia gets theirs back.',
        'owns': ['Chat session management', 'Message ingestion', 'Approval response collection', 'Human-triggered run starts'],
        'does_not_own': ['Operator execution', 'External channel routing', 'Encryption'],
    },
    'VANGUARD': {
        'name': 'VANGUARD',
        'layer': 'Gateway',
        'description': 'Zyrcon Vanguard communication gateway. The layer that meets the outside world before anything else in Cascadia OS does. Routes inbound channels and dispatches outbound messages.',
        'owns': ['Channel registration', 'Inbound normalization', 'Outbound dispatch', 'Webhook handling'],
        'does_not_own': ['Encryption', 'Chat sessions', 'Operator execution'],
    },
    'CURTAIN': {
        'name': 'CURTAIN',
        'layer': 'Encryption',
        'description': 'Encryption layer. Sits behind every communication channel. Transport encryption, at-rest data protection, HMAC signing, and key management.',
        'owns': ['Envelope signing', 'Field encryption', 'Session key generation', 'Signature verification'],
        'does_not_own': ['Routing', 'Capability enforcement', 'Communication channels'],
    },
    'SENTINEL': {
        'name': 'SENTINEL',
        'layer': 'Security',
        'description': 'Security protocols, compliance checking, and data governance. Evaluates every dangerous action against risk levels and compliance rules before it executes.',
        'owns': ['Risk classification', 'Compliance rule evaluation', 'Action verdict (allowed/requires_approval/blocked)'],
        'does_not_own': ['Encryption', 'Routing', 'Approval UI', 'Credential storage'],
        'verdicts': ['allowed', 'requires_approval', 'blocked'],
    },
    'VAULT': {
        'name': 'VAULT',
        'layer': 'Memory',
        'description': 'Private institutional memory. Structured durable storage for operator knowledge, customer context, approved outputs, and shared memory. Capability-checked on every access.',
        'owns': ['Durable key-value storage', 'Capability-gated read/write', 'Namespace isolation'],
        'does_not_own': ['Semantic retrieval', 'Memory ranking', 'Credential values'],
    },
    'BEACON': {
        'name': 'BEACON',
        'layer': 'Orchestration',
        'description': 'Orchestrator and capability-aware router. Decides which operator handles a task and routes messages between them. Checks capability manifests on every route.',
        'owns': ['Capability-checked routing', 'Operator handoffs', 'Task direction'],
        'does_not_own': ['Workflow planning', 'Scheduling', 'Approval decisions'],
    },
    'STITCH': {
        'name': 'STITCH',
        'layer': 'Automation',
        'description': 'Workflow automation engine. Connects steps, operators, triggers, and outcomes into durable sequences. Includes built-in workflow templates.',
        'owns': ['Workflow definition', 'Step sequencing', 'Run lifecycle tracking', 'Built-in templates'],
        'does_not_own': ['Step execution', 'Approval decisions', 'Storage', 'Communication'],
        'built_in_workflows': ['lead_follow_up', 'calendar_check'],
    },
    'HANDSHAKE': {
        'name': 'HANDSHAKE',
        'layer': 'Integration',
        'description': 'API bridge to external services. Registers connections to CRMs, ERPs, payment systems, and other external APIs. Works alongside CURTAIN for secure transport.',
        'owns': ['Connection registry', 'Credential reference storage', 'Outbound API proxy', 'Call logging'],
        'does_not_own': ['Credential values', 'Routing decisions', 'Workflow logic'],
        'supported_service_types': ['crm', 'erp', 'email', 'calendar', 'payment', 'storage', 'database', 'messaging', 'analytics', 'webhook', 'custom'],
    },
    'CREW': {
        'name': 'CREW',
        'layer': 'Registry',
        'description': 'Operator group registry. Tracks registered operators and validates capability manifests on every inbound route. A Crew is the group of operators working together on your tasks.',
        'owns': ['Operator registration', 'Capability validation', 'Group membership tracking'],
        'does_not_own': ['Workflow planning', 'Execution'],
    },
    'ALMANAC': {
        'name': 'ALMANAC',
        'layer': 'Knowledge',
        'description': 'Field guide and knowledge base. Documents all Cascadia OS components, terms, runbook entries, and operator playbooks. The reference you consult regularly.',
        'owns': ['Component catalog', 'Term definitions', 'Runbook entries', 'Operator playbooks'],
        'does_not_own': ['Runtime execution', 'Business data storage', 'Communication'],
    },
}

GLOSSARY: Dict[str, str] = {
    'operator': 'An autonomous or semi-autonomous worker that executes a specific set of tasks. Operators declare capabilities in a manifest and are managed by FLINT.',
    'crew': 'A group of operators working together on tasks for a tenant or customer. Visible in PRISM.',
    'run': 'One execution of an operator or workflow. Tracked in the durability layer with a unique run_id.',
    'run_state': 'The current workflow execution state of a run. Values: pending, running, blocked, retrying, waiting_human, poisoned, complete, failed, abandoned.',
    'process_state': 'The machine-level availability state of a component. Values: starting, ready, degraded, draining, offline.',
    'side_effect': 'An external action taken by an operator — email send, CRM write, file mutation, billing action. Tracked in the side_effects table for idempotency.',
    'idempotency': 'The guarantee that a side effect executes exactly once even if the run crashes and resumes. Enforced via SHA-256 keyed records in the side_effects table.',
    'capability': 'A declared permission an operator holds. Examples: email.send, vault.read, crm.write. Checked by BEACON and SENTINEL on every action.',
    'manifest': 'A JSON file declaring an operator-asset identity, version, capabilities, dependencies, permissions, and autonomy level.',
    'autonomy_level': 'Metadata in the operator manifest indicating how independently it may act. Values: manual_only, assistive, semi_autonomous, autonomous.',
    'approval': 'A human decision required before a gated action can execute. Managed by approval_store. Visible in PRISM. Responded to via BELL.',
    'dependency_block': 'A run state where a required operator or permission is missing. PRISM shows blocked_reason and blocking_entity. The system waits — it does not self-install.',
    'step_journal': 'Append-only log of every step in a run. Source of truth for resume decisions. resume_manager reads this to find the safe restart point.',
    'vault': 'Private institutional memory. Durable, capability-checked key-value storage for operator knowledge and customer context.',
    'flint': 'The OS control layer. Supervises all processes. If FLINT is healthy, the system is running.',
    'sentinel': 'Security layer. Evaluates action risk and compliance rules. Returns: allowed, requires_approval, or blocked.',
    'curtain': 'Encryption layer. Every message that leaves Cascadia OS goes through CURTAIN.',
    'beacon': 'Orchestrator. Routes tasks to the right operator and checks capabilities before routing.',
    'stitch': 'Workflow automation. Connects steps into named, reusable sequences with built-in templates.',
    'handshake': 'API bridge. Registers connections to external services. Works alongside CURTAIN.',
    'vanguard': 'Communication gateway. First contact with the outside world. Normalizes all inbound channels.',
    'bell': 'Chat interface. How humans talk to Cascadia OS and how it asks them for approvals.',
    'prism': 'Dashboard. Everything visible in one place. The window into what Cascadia OS is doing.',
    'grid': 'Decentralized compute network. Distributed inference with private operator isolation. Roadmap.',
    'depot': 'Operator app store and marketplace. Installable operator-assets. Roadmap.',
    'once': 'Installer software. One command to set up Cascadia OS on a new machine.',
    'almanac': 'Field guide and knowledge base. Covers every component, term, and runbook entry in Cascadia OS. The reference you consult regularly.',
}

RUNBOOK: Dict[str, Dict[str, str]] = {
    'run_not_resuming': {
        'title': 'Run not resuming after restart',
        'symptom': 'A run that was interrupted is still showing pending after FLINT restarted.',
        'check': 'Query runs WHERE run_state = pending. Check step_journal for the run_id.',
        'cause': 'resume_manager reads the step journal and side_effects table. If a side effect is still planned (not committed), it will resume from that step.',
        'fix': 'Check the side_effects table for status=planned entries. If the action was actually completed, manually commit the effect key or re-run from that step.',
    },
    'approval_not_waking': {
        'title': 'Run stuck in waiting_human after approval recorded',
        'symptom': 'User approved an action but the run is still waiting_human.',
        'check': 'SELECT * FROM approvals WHERE run_id = ? AND action_key = ?. Check decision field.',
        'cause': 'approval_store.record_decision calls wake_blocked_run, which sets run_state to retrying. If run_state is still waiting_human, the decision was not recorded correctly.',
        'fix': 'Manually call approval_store.record_decision with the correct approval_id. Check PRISM /api/prism/approvals for the approval_id.',
    },
    'component_not_starting': {
        'title': 'Component stuck in starting state',
        'symptom': 'PRISM shows a component with process_state=starting and never reaches ready.',
        'check': 'Check data/logs/<component>.log. Check that the port is not already in use.',
        'cause': 'Most common cause: port conflict, missing config field, or import error in the component module.',
        'fix': 'Check logs. Ensure config.json has correct port and heartbeat_file paths. Run component directly: python -m cascadia.<component> --config config.json --name <name>.',
    },
    'capability_denied': {
        'title': 'Operator capability denied by BEACON',
        'symptom': 'BEACON returns 403 capability_denied for an operator action.',
        'check': 'Check the operator manifest capabilities list. Check CREW /crew for registered capabilities.',
        'cause': 'Operator not registered with CREW, or manifest does not declare the required capability.',
        'fix': 'Add the required capability to the operator manifest and re-register with CREW.',
    },
    'sentinel_blocks_action': {
        'title': 'SENTINEL blocks an action that should be allowed',
        'symptom': 'SENTINEL returns verdict=blocked for an action.',
        'check': 'GET SENTINEL /risk-levels. Check COMPLIANCE_RULES for the action.',
        'cause': 'The action has an empty allowed_autonomy list in COMPLIANCE_RULES (shell.exec is always blocked), or the operator autonomy_level is too low.',
        'fix': 'Review the operator manifest autonomy_level. shell.exec is never allowed — use a specific tool instead.',
    },
}


class AlmanacService:
    """
    ALMANAC - Field guide and knowledge base service.
    Owns component catalog, glossary, and runbook.
    Does not own runtime execution or business data storage.
    """

    def __init__(self, config_path: str, name: str) -> None:
        self.config = load_config(config_path)
        component = next(c for c in self.config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=component['port'],
            heartbeat_file=component['heartbeat_file'],
            log_dir=self.config['log_dir'],
        )
        self.runtime.register_route('GET',  '/components',           self.list_components)
        self.runtime.register_route('POST', '/component',            self.get_component)
        self.runtime.register_route('GET',  '/glossary',             self.full_glossary)
        self.runtime.register_route('POST', '/glossary/term',        self.define_term)
        self.runtime.register_route('GET',  '/runbook',              self.list_runbook)
        self.runtime.register_route('POST', '/runbook/entry',        self.get_runbook_entry)
        self.runtime.register_route('POST', '/search',               self.search)

    def list_components(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            'components': [
                {'name': k, 'layer': v['layer'], 'description': v['description'][:80] + '...'}
                for k, v in COMPONENT_CATALOG.items()
            ],
            'count': len(COMPONENT_CATALOG),
        }

    def get_component(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        name = payload.get('name', '').upper()
        comp = COMPONENT_CATALOG.get(name)
        if comp is None:
            return 404, {'error': f'component not found: {name}', 'available': list(COMPONENT_CATALOG.keys())}
        return 200, comp

    def full_glossary(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {'terms': GLOSSARY, 'count': len(GLOSSARY)}

    def define_term(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        term = payload.get('term', '').lower()
        definition = GLOSSARY.get(term)
        if definition is None:
            return 404, {'error': f'term not found: {term}'}
        return 200, {'term': term, 'definition': definition}

    def list_runbook(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            'entries': [
                {'id': k, 'title': v['title'], 'symptom': v['symptom']}
                for k, v in RUNBOOK.items()
            ],
            'count': len(RUNBOOK),
        }

    def get_runbook_entry(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        entry_id = payload.get('id', '')
        entry = RUNBOOK.get(entry_id)
        if entry is None:
            return 404, {'error': f'runbook entry not found: {entry_id}',
                         'available': list(RUNBOOK.keys())}
        return 200, {'id': entry_id, **entry}

    def search(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        """Simple keyword search across components, glossary, and runbook."""
        query = payload.get('query', '').lower()
        if not query:
            return 400, {'error': 'query required'}
        results: List[Dict[str, Any]] = []
        for name, comp in COMPONENT_CATALOG.items():
            if query in name.lower() or query in comp['description'].lower():
                results.append({'type': 'component', 'name': name, 'match': comp['description'][:100]})
        for term, defn in GLOSSARY.items():
            if query in term or query in defn.lower():
                results.append({'type': 'glossary', 'term': term, 'match': defn[:100]})
        for entry_id, entry in RUNBOOK.items():
            if query in entry['title'].lower() or query in entry['symptom'].lower():
                results.append({'type': 'runbook', 'id': entry_id, 'title': entry['title']})
        return 200, {'query': query, 'results': results, 'count': len(results)}

    def start(self) -> None:
        self.runtime.logger.info('ALMANAC field guide active')
        self.runtime.start()


def main() -> None:
    p = argparse.ArgumentParser(description='ALMANAC - Cascadia OS field guide')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    AlmanacService(a.config, a.name).start()


if __name__ == '__main__':
    main()
