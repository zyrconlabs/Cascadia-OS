# IoT Sensor Ingest — Cascadia OS Operator

**ID:** `iot-ingest` | **Port:** 8300 | **Category:** IoT | **Tier:** Pro

Receives IoT sensor readings via HTTP POST and publishes them to NATS for downstream processing (farm monitoring, dashboards, analytics).

## HTTP Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/ingest` | Ingest a single sensor reading |
| POST | `/ingest/batch` | Ingest an array of readings |
| GET | `/readings?limit=N` | Return last N readings (default 50, max 1000) |
| GET | `/health` | Operator health + readings count |

### Single Reading Payload
```json
{
  "device_id": "sensor-001",
  "sensor_type": "soil_moisture",
  "value": 45.2,
  "unit": "%",
  "timestamp": "2026-04-30T10:00:00Z"
}
```
`timestamp` is optional — server sets it if omitted.

### Batch Payload
```json
[
  {"device_id": "sensor-001", "sensor_type": "soil_moisture", "value": 45.2, "unit": "%"},
  {"device_id": "sensor-002", "sensor_type": "temperature", "value": 22.1, "unit": "C"}
]
```

## NATS Subjects

| Subject | Direction | Description |
|---------|-----------|-------------|
| `cascadia.operators.iot-ingest.call` | Subscribe | Receive task commands |
| `cascadia.operators.iot-ingest.response` | Publish | Task responses |
| `cascadia.iot.readings` | Publish | Every validated reading |

## NATS Actions

| Action | Params | Description |
|--------|--------|-------------|
| `get_readings` | `limit` (int) | Return last N in-memory readings |
| `clear_readings` | — | Clear the in-memory ring buffer |

## Install

```bash
bash install.sh
python3 operator.py
```
