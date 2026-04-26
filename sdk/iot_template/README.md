# IoT Operator Template — Cascadia OS SDK

This template demonstrates how to build an operator that receives and analyzes IoT sensor data from CONDUIT.

## How sensor data arrives

```
Physical sensor → MQTT broker → CONDUIT → VANGUARD → your operator (/api/sensor)
```

1. CONDUIT subscribes to MQTT topics on your broker
2. When a sensor publishes, CONDUIT normalizes the message into a standard envelope
3. CONDUIT sends the envelope to VANGUARD via `/api/vanguard/ingest`
4. VANGUARD routes the `sensor` channel events to registered operators

Your operator receives POST requests on `/api/sensor` with this payload:

```json
{
  "channel": "sensor",
  "device_id": "greenhouse_1",
  "topic": "greenhouse/1/temperature",
  "payload": {"temperature": 24.7, "humidity": 68.2},
  "source": "conduit",
  "ts": "2026-04-26T10:30:00+00:00"
}
```

## How to query sensor history

```python
from cascadia.iot.sensor_store import SensorStore

store = SensorStore('./data/runtime/sensors.db')
readings = store.query('greenhouse_1', hours=24)
latest = store.latest('greenhouse_1')
```

## Safety boundary

**Analysis is free. Actuation is Enterprise.**

This template performs AI analysis only. It evaluates sensor data, detects anomalies, and requests human approval through SENTINEL.

It does **not** directly control actuators. Physical actuation requires:
1. Enterprise tier license from Zyrcon
2. Hardware guard channels independent of this software (emergency stop, hardware limiter, PLC safety relay)
3. Explicit human approval through SENTINEL for every actuation event

This is the correct legal architecture for regulated deployments under EU AI Act Article 14.

## Example use cases

### Greenhouse temperature monitoring

```python
TEMP_THRESHOLD = 30.0  # degrees C

if temp > TEMP_THRESHOLD:
    # Analyze trend, not just current reading
    history = store.query(device_id, hours=2)
    avg = sum(r['value'] for r in history if r['value']) / max(len(history), 1)
    if avg > TEMP_THRESHOLD:
        # Request approval before recommending action
        if sentinel_check('cooling_recommendation', {'temp': avg}):
            vault_store(f'alert:{device_id}', json.dumps({'temp': avg, 'action': 'recommend_cooling'}))
```

### HVAC fault detection

Monitor air handler units. If temperature differential between supply and return air drops below threshold, flag for maintenance review.

### Water quality monitoring

Track pH, turbidity, and chlorine levels. Flag exceedances for operator review. Never automatically adjust chemical dosing.

### Smart building occupancy

Correlate occupancy sensors with HVAC setpoints. Recommend schedule adjustments. Human approves setpoint changes.

## Configuring sensor routing

In VANGUARD configuration, route `sensor` channel events to your operator:

```json
{
  "routing": {
    "sensor": "my_iot_operator"
  }
}
```

## Revenue

Your first $25,000 in DEPOT sales: yours entirely (0%).
After $25,000 lifetime: you keep 80%. Zyrcon keeps 20%.

IoT operator marketplace is live at: depot.zyrcon.ai
