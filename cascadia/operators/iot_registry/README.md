# IoT Device Registry — Cascadia OS Operator

**ID:** `iot-registry` | **Port:** 8301 | **Category:** IoT | **Tier:** Pro

Register, discover, and manage IoT devices connected to Cascadia OS. Acts as the source of truth for device metadata, type, location, and online/offline status.

## Device Schema

```json
{
  "device_id": "sensor-001",
  "name": "Field A Moisture Sensor",
  "type": "moisture-sensor",
  "location": "Field A, Zone 3",
  "sensor_types": ["soil_moisture", "temperature"],
  "status": "online",
  "last_seen": "2026-04-30T10:00:00Z",
  "metadata": {}
}
```

**Valid statuses:** `online`, `offline`, `unknown`

## NATS Subjects

| Subject | Direction | Description |
|---------|-----------|-------------|
| `cascadia.operators.iot-registry.call` | Subscribe | Receive task commands |
| `cascadia.operators.iot-registry.response` | Publish | Task responses |

## NATS Actions

| Action | Required Params | Description |
|--------|----------------|-------------|
| `register_device` | `device_id`, `name`, `type`, `location`, `sensor_types` | Register or update a device |
| `get_device` | `device_id` | Fetch a single device |
| `list_devices` | `type?`, `status?`, `location?` | List devices with optional filters |
| `update_status` | `device_id`, `status` | Update online/offline/unknown status |
| `deregister_device` | `device_id` | Remove device from registry |

## Install

```bash
bash install.sh
python3 operator.py
```
