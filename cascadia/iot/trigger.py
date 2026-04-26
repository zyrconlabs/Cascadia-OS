"""
trigger.py — Cascadia OS v0.47
TriggerEngine: IoT threshold-based workflow trigger evaluation.
Owns: trigger definition evaluation, cooldown tracking, VANGUARD ingest routing.
Does not own: sensor storage (SensorStore), workflow execution (STITCH), risk (SENTINEL).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib import request as urllib_request

logger = logging.getLogger(__name__)

OPERATORS = ('gt', 'lt', 'gte', 'lte', 'eq', 'neq')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TriggerDefinition:
    """
    One threshold trigger definition.
    Owns: threshold evaluation and cooldown tracking.
    Does not own: workflow routing or side effects.
    """

    def __init__(
        self,
        trigger_id: str,
        device_id: str,
        field: str,
        operator: str,
        threshold: float,
        workflow_id: str,
        cooldown_seconds: int = 300,
    ) -> None:
        if operator not in OPERATORS:
            raise ValueError(f'Invalid operator {operator!r}. Must be one of {OPERATORS}')
        self.trigger_id = trigger_id
        self.device_id = device_id
        self.field = field
        self.operator = operator
        self.threshold = threshold
        self.workflow_id = workflow_id
        self.cooldown_seconds = cooldown_seconds
        self._last_fired: Optional[float] = None

    def evaluate(self, value: float) -> bool:
        """Return True if the value satisfies this trigger's threshold condition."""
        op = self.operator
        t = self.threshold
        if op == 'gt':
            return value > t
        elif op == 'lt':
            return value < t
        elif op == 'gte':
            return value >= t
        elif op == 'lte':
            return value <= t
        elif op == 'eq':
            return value == t
        elif op == 'neq':
            return value != t
        return False

    def is_cooled_down(self) -> bool:
        """Return True if enough time has passed since last fire."""
        if self._last_fired is None:
            return True
        return time.time() - self._last_fired >= self.cooldown_seconds

    def mark_fired(self) -> None:
        """Record the current time as the last fire time."""
        self._last_fired = time.time()


class TriggerEngine:
    """
    Evaluates sensor payloads against registered triggers.
    Owns: trigger registration, evaluation loop, VANGUARD ingest on fire.
    Does not own: sensor storage, workflow execution, approval decisions.
    """

    def __init__(self, vanguard_port: int = 6202) -> None:
        self._triggers: List[TriggerDefinition] = []
        self._vanguard_port = vanguard_port

    def register(self, trigger: TriggerDefinition) -> None:
        """Register a trigger definition."""
        self._triggers.append(trigger)

    def process(self, device_id: str, payload: Dict[str, Any]) -> List[str]:
        """
        Evaluate payload against all triggers for this device_id.
        Returns list of workflow_ids that were fired.
        """
        fired: List[str] = []
        for trigger in self._triggers:
            if trigger.device_id != device_id:
                continue
            value = payload.get(trigger.field)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if trigger.evaluate(value) and trigger.is_cooled_down():
                trigger.mark_fired()
                self._fire(trigger, device_id, value, payload)
                fired.append(trigger.workflow_id)
        return fired

    def _fire(self, trigger: TriggerDefinition, device_id: str, value: float, payload: Dict[str, Any]) -> None:
        """POST a trigger event to VANGUARD /api/vanguard/ingest."""
        envelope = {
            'channel': 'iot_trigger',
            'trigger_id': trigger.trigger_id,
            'device_id': device_id,
            'field': trigger.field,
            'value': value,
            'threshold': trigger.threshold,
            'operator': trigger.operator,
            'workflow_id': trigger.workflow_id,
            'payload': payload,
            'source': 'conduit_trigger',
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
            logger.info('CONDUIT trigger fired: %s → workflow %s (device %s, %s=%s)',
                        trigger.trigger_id, trigger.workflow_id, device_id, trigger.field, value)
        except Exception as exc:
            logger.warning('CONDUIT trigger fire failed: %s', exc)
