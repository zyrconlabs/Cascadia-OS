"""
watchdog.py - Cascadia OS v{VERSION}
External FLINT liveness monitor. Lives outside the supervision tree.
Monitors FLINT heartbeat only. Has no knowledge of operators or workflows.
If this file grows complex, that is a design error.
"""
# MATURITY: PRODUCTION — External liveness monitor. Simple by design.
from __future__ import annotations
import argparse, subprocess, sys, time
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
        self.proc = None
        ops_path = self.config.get("operators_dir", "")
        operators_dir = Path(ops_path).expanduser() if ops_path else None
        self.operator_manager = OperatorManager(self.logger, operators_dir=operators_dir)

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

    def run(self) -> None:
        self.logger.info('Watchdog active - Cascadia OS v' + VERSION + '')
        # Branded terminal banner
        print('')
        print('  \033[31m╔══════════════════════════════════════╗\033[0m')
        print('  \033[31m║\033[0m  \033[31;1mZyrcon\033[0m  ·  \033[35mCascadia OS\033[0m  \033[31m║\033[0m')
        print('  \033[31m║\033[0m     AI Work Platform v' + VERSION + '       \033[31m║\033[0m')
        print('  \033[31m╚══════════════════════════════════════╝\033[0m')
        print('')
        self.start_flint()
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
