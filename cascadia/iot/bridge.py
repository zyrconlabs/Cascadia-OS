"""
bridge.py — Cascadia OS v0.47
CONDUIT: IoT bridge service.
Owns: sensor adapter management, sensor event normalization, VANGUARD routing.
Does not own: sensor storage (SensorStore), workflow execution (STITCH), risk classification (SENTINEL).
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib import request as urllib_request

from cascadia.shared.config import load_config
from cascadia.shared.service_runtime import ServiceRuntime

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConduitService:
    """
    CONDUIT - IoT bridge service.
    Owns sensor adapter lifecycle and event routing to VANGUARD.
    Does not own sensor history, workflow execution, or risk classification.
    """

    def __init__(self, config_path: str, name: str) -> None:
        self._config_path = config_path
        self.config = load_config(config_path)
        component = next(c for c in self.config['components'] if c['name'] == name)
        self.runtime = ServiceRuntime(
            name=name, port=component['port'],
            heartbeat_file=component['heartbeat_file'],
            log_dir=self.config['log_dir'],
        )
        self.iot_cfg = self.config.get('iot', {})
        self._adapters: List[Any] = []

        vanguard_comp = next((c for c in self.config['components'] if c['name'] == 'vanguard'), None)
        self._vanguard_port = vanguard_comp['port'] if vanguard_comp else 6202

        self.runtime.register_route('GET', '/health', self._health)
        self.runtime.register_route('GET', '/api/conduit/status', self._status)

    def _health(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            'ok': True,
            'component': 'conduit',
            'adapters': [a.name for a in self._adapters],
        }

    def _status(self, _: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        return 200, {
            'adapters': [
                {'name': a.name, 'connected': a.is_connected()}
                for a in self._adapters
            ],
            'generated_at': _now(),
        }

    def _load_adapters(self) -> None:
        mqtt_cfg = self.iot_cfg.get('mqtt', {})
        if mqtt_cfg.get('enabled', False):
            from cascadia.iot.adapters.mqtt import MQTTAdapter
            adapter = MQTTAdapter(mqtt_cfg)
            self._adapters.append(adapter)
            logger.info('CONDUIT: loaded MQTT adapter')

    def _on_sensor_event(self, device_id: str, topic: str, payload: Any) -> None:
        """Receive a sensor event and forward to VANGUARD."""
        envelope = {
            'channel': 'sensor',
            'device_id': device_id,
            'topic': topic,
            'payload': payload,
            'source': 'conduit',
            'ts': _now(),
        }
        try:
            data = json.dumps(envelope).encode('utf-8')
            req = urllib_request.Request(
                f'http://127.0.0.1:{self._vanguard_port}/api/vanguard/ingest',
                data=data, method='POST',
                headers={'Content-Type': 'application/json'},
            )
            urllib_request.urlopen(req, timeout=2)
        except Exception as exc:
            logger.warning('CONDUIT: VANGUARD ingest failed for device %s: %s', device_id, exc)

    def start(self) -> None:
        if not self.iot_cfg.get('enabled', False):
            logger.info('CONDUIT: IoT disabled in config (iot.enabled=false). Skipping adapter load.')
            self.runtime.start()
            return

        self._load_adapters()
        for adapter in self._adapters:
            try:
                adapter.start(self._on_sensor_event)
                logger.info('CONDUIT: started adapter %s', adapter.name)
            except Exception as exc:
                logger.error('CONDUIT: adapter %s start failed: %s', adapter.name, exc)

        self.runtime.start()

    def stop(self) -> None:
        for adapter in self._adapters:
            try:
                adapter.stop()
            except Exception as exc:
                logger.error('CONDUIT: adapter %s stop error: %s', adapter.name, exc)


def main() -> None:
    p = argparse.ArgumentParser(description='CONDUIT — Cascadia OS IoT bridge')
    p.add_argument('--config', required=True)
    p.add_argument('--name', required=True)
    a = p.parse_args()
    ConduitService(a.config, a.name).start()


if __name__ == '__main__':
    main()
