"""
watchdog.py - Cascadia OS v{VERSION}
External FLINT liveness monitor. Lives outside the supervision tree.
Monitors FLINT heartbeat only. Has no knowledge of operators or workflows.
If this file grows complex, that is a design error.
"""
# MATURITY: PRODUCTION — External liveness monitor. Simple by design.
from __future__ import annotations
import argparse, os, secrets as _secrets, subprocess, sys, time
from pathlib import Path
from cascadia import VERSION
from cascadia.shared.config import load_config
from cascadia.shared.logger import configure_logging
from cascadia.kernel.operator_manager import OperatorManager

class Watchdog:
    """Owns FLINT liveness monitoring. Does not own component-level supervision."""
    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
        self.config = load_config(config_path)
        self.logger = configure_logging(self.config['log_dir'], 'watchdog')
        self._validate_config()
        self._generate_internal_key()
        self.proc = None
        ops_path = self.config.get("operators_dir", "")
        operators_dir = Path(ops_path).expanduser() if ops_path else None
        self.operator_manager = OperatorManager(self.logger, operators_dir=operators_dir, config=self.config)

    def _generate_internal_key(self) -> None:
        if not self.config.get('security', {}).get('internal_api_key_required', False):
            return
        key = _secrets.token_hex(32)
        os.environ['CASCADIA_INTERNAL_KEY'] = key
        self.logger.info('Security: internal API key generated')

    def _validate_config(self) -> None:
        """Warn on insecure or placeholder config values at startup."""
        checks = [
            ('curtain.signing_secret', lambda c: c.get('curtain', {}).get('signing_secret', '')),
            ('license_secret',         lambda c: c.get('license_secret', '')),
        ]
        for label, getter in checks:
            val = getter(self.config)
            if not val or (isinstance(val, str) and val.startswith('replace-')):
                self.logger.warning('CONFIG: %s not set — replace placeholder before production use', label)
        if self.config.get('sentinel_fail_open', False):
            self.logger.warning('CONFIG: sentinel_fail_open=true — sentinel will not block side-effects on failure')

    def _check_database_integrity(self) -> bool:
        import sqlite3, sys
        from pathlib import Path
        db_path = self.config.get('database_path', './data/runtime/cascadia.db')
        if not Path(db_path).exists():
            return True  # Fresh install
        try:
            with sqlite3.connect(db_path) as conn:
                result = conn.execute('PRAGMA integrity_check').fetchone()
                if result and result[0] == 'ok':
                    return True
                self.logger.error('Database integrity FAILED: %s', result)
                self.logger.error('Restore from: data/backups/')
                return False
        except Exception as e:
            self.logger.error('Integrity check error: %s', e)
            return False

    def start_flint(self) -> None:
        self.logger.info('Watchdog starting FLINT')
        self.proc = subprocess.Popen([sys.executable, '-m', 'cascadia.kernel.flint', '--config', self.config_path], text=True)

    def flint_stale(self) -> bool:
        hb = Path(self.config['flint']['heartbeat_file'])
        return (not hb.exists()) or (time.time() - hb.stat().st_mtime > self.config['flint']['heartbeat_stale_after_seconds'])

    def restart_flint(self) -> None:
        self.logger.warning('FLINT heartbeat stale - restarting')
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired: self.proc.kill()
        self.start_flint()

    def _call_resume(self) -> None:
        import urllib.request, json
        time.sleep(5)  # Brief wait for STITCH to be ready
        stitch_port = None
        for c in self.config.get('components', []):
            if c.get('name') == 'stitch':
                stitch_port = c.get('port', 6201)
        if not stitch_port:
            return
        try:
            req = urllib.request.Request(
                f'http://127.0.0.1:{stitch_port}/api/stitch/resume',
                data=b'{}', method='POST',
                headers={'Content-Type': 'application/json'}
            )
            result = json.loads(urllib.request.urlopen(req, timeout=5).read())
            if result.get('resumed', 0) > 0:
                self.logger.info('Watchdog: resumed %s interrupted workflows', result['resumed'])
        except Exception as e:
            self.logger.warning('Watchdog: resume call failed: %s', e)

    def run(self) -> None:
        self.logger.info('Watchdog active - Cascadia OS v' + VERSION + '')
        # Branded terminal banner
        print('')
        print('  \033[31m╔══════════════════════════════════════╗\033[0m')
        print('  \033[31m║\033[0m  \033[31;1mZyrcon\033[0m  ·  \033[35mCascadia OS\033[0m  \033[31m║\033[0m')
        print('  \033[31m║\033[0m     AI Work Platform v' + VERSION + '       \033[31m║\033[0m')
        print('  \033[31m╚══════════════════════════════════════╝\033[0m')
        print('')
        if not self._check_database_integrity():
            self.logger.critical('Database corrupted. Restore from data/backups/')
            sys.exit(1)
        self.start_flint()
        import threading as _threading
        _threading.Thread(target=self._call_resume, daemon=True).start()
        self.operator_manager.run()
        # Startup grace: give FLINT time to boot before first stale check
        startup_grace = self.config['flint'].get('heartbeat_stale_after_seconds', 15) * 3
        self.logger.info('Watchdog giving FLINT %ss startup grace', startup_grace)
        time.sleep(startup_grace)
        while True:
            time.sleep(5)
            if self.proc and self.proc.poll() is not None:
                self.logger.warning('FLINT exited (code %s) - restarting', self.proc.returncode)
                self.start_flint(); continue
            if self.flint_stale():
                self.restart_flint()

def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument('--config', required=True)
    Watchdog(p.parse_args().config).run()

if __name__ == '__main__': main()
