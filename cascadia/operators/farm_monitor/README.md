# Farm Monitor — Cascadia OS Operator

**ID:** `farm-monitor` | **Port:** 8302 | **Category:** IoT | **Tier:** Pro | **Industry:** Agriculture

Monitors agricultural IoT sensors for soil moisture, temperature, humidity, and pH. Subscribes to live readings from the `iot-ingest` operator and triggers alerts when values breach configurable thresholds.

## Default Thresholds

| Sensor | Warn | Alert |
|--------|------|-------|
| `soil_moisture` | < 30% | < 20% |
| `temperature` | > 35°C | > 40°C |
| `humidity` | < 40% | < 30% |
| `ph` | outside 5.5–7.5 | outside 5.0–8.0 |

Thresholds are overridable per zone via `configure_zone`.

## NATS Subjects

| Subject | Direction | Description |
|---------|-----------|-------------|
| `cascadia.operators.farm-monitor.call` | Subscribe | Receive task commands |
| `cascadia.operators.farm-monitor.response` | Publish | Task responses |
| `cascadia.iot.readings` | Subscribe | Live sensor readings from iot-ingest |
| `cascadia.iot.alerts` | Publish | Threshold breach alerts |
| `cascadia.approvals.request` | Publish | Approval-gated: external notification |

## NATS Actions

| Action | Approval | Description |
|--------|----------|-------------|
| `configure_zone` | No | Create/update a zone with devices and thresholds |
| `get_zone_status` | No | Get last readings and active alerts for a zone |
| `list_zones` | No | List all zones with status summary |
| `check_thresholds` | No | Manually check a reading against a zone |
| `send_alert_notification` | **Yes** | Send external notification (requires approval) |

## Zone Configuration Example

```json
{
  "action": "configure_zone",
  "zone_id": "field-a",
  "name": "Field A — Wheat",
  "devices": ["sensor-001", "sensor-002"],
  "thresholds": {
    "soil_moisture": {"warn_below": 35, "alert_below": 25}
  }
}
```

## Install

```bash
bash install.sh
python3 operator.py
```
