"""
watchdog.py - Cascadia OS v0.2
External FLINT liveness monitor. Lives outside the supervision tree.
Monitors FLINT heartbeat only. Has no knowledge of operators or workflows.
If this file grows complex, that is a design error.
"""
# MATURITY: PRODUCTION — External liveness monitor. Simple by design.
from __future__ import annotations
import argparse, subprocess, sys, time
from pathlib import Path
from cascadia.shared.config import load_config
from cascadia.shared.logger import configure_logging

class Watchdog:
    """Owns FLINT liveness monitoring. Does not own component-level supervision."""
    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
        self.config = load_config(config_path)
        self.logger = configure_logging(self.config['log_dir'], 'watchdog')
        self.proc = None

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
        self.logger.info('Watchdog active - Cascadia OS v0.2')
        self.start_flint()
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
