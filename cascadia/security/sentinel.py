"""sentinel.py - Cascadia OS v0.43 | SENTINEL: Security, compliance, data governance."""
from __future__ import annotations
import argparse
from typing import Any, Dict, List
from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime

RISK_LEVELS: Dict[str, str] = {
    'email.send':'medium','email.delete':'high','crm.write':'low','crm.delete':'high',
    'file.read':'low','file.write':'medium','file.delete':'high','file.overwrite':'medium',
    'calendar.read':'low','calendar.write':'medium','invoice.create':'high',
    'billing.write':'high','shell.exec':'critical','browser.submit':'medium',
    'payment.create':'high','vault.read':'low','vault.write':'medium',
}
COMPLIANCE_RULES: Dict[str, List[str]] = {
    'email.send':['semi_autonomous','autonomous'],'crm.write':['assistive','semi_autonomous','autonomous'],
    'invoice.create':['autonomous'],'billing.write':['autonomous'],'shell.exec':[],
    'file.delete':['autonomous'],'calendar.write':['semi_autonomous','autonomous'],
}

class SentinelService:
    """SENTINEL - Owns security and compliance evaluation. Does not own routing or encryption."""
# MATURITY: FUNCTIONAL — Risk classification and compliance rules work. Wiring into operator execution loop is v0.3.
    def __init__(self, config_path: str, name: str) -> None:
        config = load_config(config_path)
        comp = next(c for c in config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(name=name, port=comp['port'], heartbeat_file=comp['heartbeat_file'], log_dir=config['log_dir'])
        self.runtime.register_route('POST', '/check', self.check_action)
        self.runtime.register_route('POST', '/compliance', self.check_compliance)
        self.runtime.register_route('GET', '/risk-levels', self.get_risk_levels)

    def check_action(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        action = payload.get('action', ''); autonomy = payload.get('autonomy_level', 'manual_only'); op = payload.get('operator_id', '')
        risk = RISK_LEVELS.get(action, 'low'); allowed = COMPLIANCE_RULES.get(action)
        if allowed is None: verdict, reason = 'allowed', 'no sentinel rule'
        elif len(allowed) == 0: verdict, reason = 'blocked', f'{action} blocked by sentinel'
        elif autonomy in allowed: verdict, reason = 'allowed', f'autonomy {autonomy} satisfies rule'
        else: verdict, reason = 'requires_approval', f'autonomy {autonomy} insufficient for {action} (risk:{risk})'
        self.runtime.logger.info('SENTINEL %s %s %s -> %s', action, op, autonomy, verdict)
        return 200, {'verdict': verdict, 'action': action, 'risk_level': risk, 'operator_id': op, 'autonomy_level': autonomy, 'reason': reason}

    def check_compliance(self, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        results = []; any_blocked = False
        for action in payload.get('actions', []):
            _, r = self.check_action({'action': action, 'operator_id': payload.get('operator_id',''), 'autonomy_level': payload.get('autonomy_level','manual_only')})
            results.append(r)
            if r['verdict'] == 'blocked': any_blocked = True
        return 200, {'operator_id': payload.get('operator_id',''), 'all_clear': not any_blocked, 'results': results}

    def get_risk_levels(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {'risk_levels': RISK_LEVELS, 'compliance_rules': COMPLIANCE_RULES}

    def start(self) -> None: self.runtime.start()

def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument('--config', required=True); p.add_argument('--name', required=True); a = p.parse_args(); SentinelService(a.config, a.name).start()
if __name__ == '__main__': main()
