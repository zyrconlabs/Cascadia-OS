"""
system_monitor.py — Cascadia OS v0.46
Hardware health monitoring for the Zyrcon AI Server.
Owns: reading CPU, RAM, disk, temperature, model inference speed.
Does not own: alerting (HANDSHAKE), display (PRISM), process management (FLINT).
"""
# MATURITY: PRODUCTION — psutil-based, macOS-optimized, fails gracefully.
from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, Optional

from cascadia.shared.logger import get_logger

logger = get_logger('system_monitor')


class SystemMonitor:
    """Owns hardware health snapshots. Fails gracefully when psutil is absent."""

    def snapshot(self) -> Dict[str, Any]:
        """Return current hardware health snapshot."""
        try:
            import psutil
            cpu_pct = psutil.cpu_percent(interval=0.5)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            return {
                'cpu_percent':     round(cpu_pct, 1),
                'ram_used_gb':     round(ram.used / 1e9, 1),
                'ram_total_gb':    round(ram.total / 1e9, 1),
                'ram_percent':     round(ram.percent, 1),
                'disk_used_gb':    round(disk.used / 1e9, 1),
                'disk_total_gb':   round(disk.total / 1e9, 1),
                'disk_percent':    round(disk.percent, 1),
                'temperature_c':   self._get_temperature(),
                'model_speed_tps': self._get_model_speed(),
                'available':       True,
            }
        except ImportError:
            logger.warning('SystemMonitor: psutil not installed — pip install psutil')
            return {'available': False}
        except Exception as e:
            logger.error('SystemMonitor: snapshot failed: %s', e)
            return {'available': False}

    def _get_temperature(self) -> Optional[float]:
        """Get CPU temperature via powermetrics (macOS only)."""
        try:
            result = subprocess.run(
                ['sudo', 'powermetrics', '-n', '1', '-i', '100',
                 '--samplers', 'smc', '-a'],
                capture_output=True, text=True, timeout=2,
            )
            for line in result.stdout.split('\n'):
                if 'CPU die temperature' in line:
                    return float(line.split(':')[1].strip().split()[0])
        except Exception:
            pass
        return None

    def _get_model_speed(self) -> Optional[float]:
        """Estimate inference speed from llama.cpp logs (tokens/sec)."""
        try:
            log_path = './data/logs/llm.log'
            with open(log_path) as f:
                lines = f.readlines()[-50:]
            for line in reversed(lines):
                m = re.search(r'(\d+\.?\d*)\s*tokens/s', line)
                if m:
                    return float(m.group(1))
        except Exception:
            pass
        return None
