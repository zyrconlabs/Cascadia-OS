# IoT Integration Guide — Cascadia OS v0.47

## Overview

Cascadia OS includes CONDUIT, a native IoT bridge that normalizes sensor data from physical devices and routes it to the Cascadia OS workflow engine. Sensor events become workflow triggers. AI operators analyze sensor data and recommend actions. Human approval gates control any physical responses.

CONDUIT sits between your physical sensor network and your AI operator stack. It does not control actuators. It routes information.

```
Physical sensors → CONDUIT → VANGUARD → AI Operators → Approval Gates → Human Decision
```

---

## Supported Protocols

| Protocol | Status | Notes |
|----------|--------|-------|
| MQTT | Available (v0.47) | paho-mqtt required: `pip install paho-mqtt` |
| Modbus TCP/RTU | Planned (v0.48) | Industrial PLC and sensor integration |
| Zigbee | Planned (v0.49) | Low-power mesh sensor networks |
| GPIO (Raspberry Pi) | Planned (v0.49) | Direct pin read on zyrcon-arm platform |

---

## Quick Start — MQTT

### 1. Install the MQTT dependency

```bash
pip install paho-mqtt
```

### 2. Configure CONDUIT in config.json

```json
{
  "iot": {
    "enabled": true,
    "conduit_port": 6206,
    "mqtt": {
      "enabled": true,
      "broker_host": "192.168.1.100",
      "broker_port": 1883,
      "topics": [
        "greenhouse/+/temperature",
        "greenhouse/+/humidity",
        "factory/+/pressure"
      ],
      "username": "",
      "password": ""
    }
  }
}
```

### 3. Add CONDUIT to the components list

```json
{
  "name": "conduit",
  "module": "cascadia.iot.bridge",
  "port": 6206,
  "tier": 2,
  "heartbeat_file": "./data/runtime/conduit.heartbeat"
}
```

### 4. Restart Cascadia OS

```bash
bash start.sh
```

CONDUIT will appear in the PRISM health dashboard. Sensor events will begin flowing to VANGUARD within seconds of broker connection.

### 5. Verify operation

```bash
curl http://127.0.0.1:6206/api/conduit/status
# → {"adapters": [{"name": "mqtt", "connected": true}]}
```

---

## Sensor Envelope Format

Every sensor event delivered to VANGUARD has this structure:

```json
{
  "channel": "sensor",
  "device_id": "greenhouse_1",
  "topic": "greenhouse/1/temperature",
  "payload": {"temperature": 24.7, "humidity": 68.2},
  "source": "conduit",
  "ts": "2026-04-26T10:30:00.000000+00:00"
}
```

The `device_id` is extracted from the first segment of the MQTT topic path. For `greenhouse/1/temperature`, the device_id is `greenhouse`.

---

## Trigger Definitions

Triggers fire a workflow when a sensor value crosses a threshold.

```python
from cascadia.iot.trigger import TriggerDefinition, TriggerEngine

engine = TriggerEngine(vanguard_port=6202)

# Fire workflow 'high_temp_alert' when temperature exceeds 85°C
engine.register(TriggerDefinition(
    trigger_id='temp_high',
    device_id='factory_sensor_1',
    field='temperature',
    operator='gt',
    threshold=85.0,
    workflow_id='high_temp_alert',
    cooldown_seconds=300,  # Don't fire again for 5 minutes
))
```

### Supported Operators

| Operator | Meaning |
|----------|---------|
| `gt` | Greater than |
| `lt` | Less than |
| `gte` | Greater than or equal |
| `lte` | Less than or equal |
| `eq` | Equal to |
| `neq` | Not equal to |

### Cooldown

Every trigger has a cooldown period. Once a trigger fires, it will not fire again until the cooldown expires. This prevents alert storms from a single sustained threshold breach.

---

## Sensor History

Use `SensorStore` to query historical sensor readings:

```python
from cascadia.iot.sensor_store import SensorStore

store = SensorStore('./data/runtime/sensors.db')

# Query last 24 hours for a device
readings = store.query('greenhouse_1', hours=24)

# Get the most recent reading
latest = store.latest('greenhouse_1')

# List all devices with recorded readings
devices = store.list_devices()

# Purge readings older than 90 days
deleted = store.purge_old(retention_days=90)
```

---

## Analog Guard Channels

**Read this section carefully before deploying in any physical environment.**

Cascadia OS manages the AI decision layer. Physical safety enforcement is the responsibility of your hardware infrastructure.

For industrial, agricultural, and medical deployments:

**Software layer (Cascadia OS):**
- Receives sensor data via CONDUIT
- Classifies sensor events against risk policy (SENTINEL)
- Presents analysis and recommendations to human operators via PRISM
- Requires explicit human approval before any action with physical consequences
- Logs all decisions permanently for regulatory audit

**Hardware layer (your responsibility):**
- Emergency stop circuit — cuts power to actuators immediately on activation
- Hardware limiter — mechanically restricts actuator range of motion
- Thrust clamp — limits maximum force/speed/flow independent of software commands
- Physical interlock — prevents operation when safety conditions are not met
- PLC safety relay — independently monitors process variables and trips on exceedance

These hardware guard channels must operate independently of the software state and must be capable of vetoing unsafe commands within microseconds, without relying on Cascadia OS being operational. Cascadia OS does not replace physical safety systems. It coordinates with them.

This separation of software logic from physical safety enforcement is the correct legal architecture for regulated deployments.

**Actuator control is an Enterprise-only feature** and requires:
1. Enterprise tier license
2. Hardware guard channels documented and verified
3. Explicit human approval gate configured for every actuation action
4. Safety boundary documented in operator manifest

---

## Use Cases by Industry

### Greenhouse / Agriculture

**Sensor setup:** Temperature, humidity, CO2, soil moisture sensors connected via MQTT

**Trigger example:** Temperature exceeds 30°C → fire `check_irrigation_workflow`

**AI operator role:** Analyze temperature trend, check historical patterns, recommend action

**Human approval:** Required before any irrigation adjustment

**Guard channel:** Physical float valve, manual override on every irrigation zone

---

### HVAC / Building Management

**Sensor setup:** Zone temperature, occupancy, air quality, energy meters via MQTT or Modbus (v0.48)

**Trigger example:** CO2 exceeds 1000 ppm → fire `ventilation_alert_workflow`

**AI operator role:** Identify affected zones, correlate with occupancy data, draft recommendation

**Human approval:** Required before changing HVAC setpoints

**Guard channel:** Hardware high-limit thermostats, smoke/fire dampers operate independently

---

### Industrial / Manufacturing

**Sensor setup:** Vibration, temperature, pressure, flow rate sensors on process equipment

**Trigger example:** Vibration amplitude exceeds baseline by 3σ → fire `predictive_maintenance_workflow`

**AI operator role:** Classify fault type, estimate time-to-failure, draft maintenance work order

**Human approval:** Required before scheduling maintenance shutdown

**Guard channel:** Safety PLC monitors process variables and trips equipment independently of software

---

### Water Quality Monitoring

**Sensor setup:** pH, turbidity, chlorine, flow rate sensors via MQTT

**Trigger example:** pH drops below 6.5 → fire `water_quality_alert_workflow`

**AI operator role:** Correlate with upstream events, draft operator notification

**Human approval:** Required before any chemical dosing recommendation is acted upon

**Guard channel:** Hardware dosing pump limits, manual override at treatment facility

---

## Troubleshooting

### CONDUIT shows "not connected" in PRISM

- Verify MQTT broker is reachable: `ping <broker_host>`
- Check broker port: `nc -zv <broker_host> 1883`
- Verify `iot.mqtt.enabled: true` in config.json
- Check CONDUIT logs: `tail -f data/logs/conduit.log`

### No sensor events appearing in VANGUARD

- Verify topics match your sensor publisher's topic format
- Check MQTT topic subscriptions include wildcard (`+`) if needed
- Verify VANGUARD is running: `curl http://127.0.0.1:6202/health`

### paho-mqtt ImportError

```bash
pip install paho-mqtt
# or
pip3 install paho-mqtt
```

### Triggers not firing

- Verify `device_id` matches first MQTT topic segment
- Check trigger `field` matches the JSON key in your sensor payload
- Verify cooldown period hasn't recently expired
- Check CONDUIT logs for trigger evaluation messages

---

*CONDUIT is part of Cascadia OS Core (Apache 2.0). IoT actuator control requires Enterprise tier.*  
*For support: support@zyrcon.ai*
