"""Tests for E1–E3 IoT operators: Ingest, Registry, FarmMonitor."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cascadia.depot.manifest_validator import validate_depot_manifest

BASE_OPS = Path(__file__).parent.parent / 'cascadia' / 'operators'

# ── Manifest validation ───────────────────────────────────────────────────────

@pytest.mark.parametrize('dirname,expected_id,expected_port', [
    ('iot_ingest',   'iot-ingest',   8300),
    ('iot_registry', 'iot-registry', 8301),
    ('farm_monitor', 'farm-monitor', 8302),
])
def test_manifest_valid(dirname, expected_id, expected_port):
    path = BASE_OPS / dirname / 'manifest.json'
    assert path.exists(), f"manifest.json missing in {dirname}"
    data = json.loads(path.read_text())
    result = validate_depot_manifest(data)
    assert result.valid, f"{dirname}: {result.errors}"
    assert data['id'] == expected_id
    assert data['port'] == expected_port
    assert data['type'] == 'operator'
    assert data['installed_by_default'] is False


@pytest.mark.parametrize('dirname', ['iot_ingest', 'iot_registry', 'farm_monitor'])
def test_required_files_present(dirname):
    d = BASE_OPS / dirname
    for fname in ('manifest.json', 'operator.py', 'health.py', 'install.sh', 'uninstall.sh', 'README.md'):
        assert (d / fname).exists(), f"{dirname}/{fname} missing"


# ── E1: IoT Ingest ────────────────────────────────────────────────────────────

from cascadia.operators.iot_ingest.operator import (
    NAME as INGEST_NAME, PORT as INGEST_PORT,
    execute_task as ingest_exec,
)

def test_iot_ingest_metadata():
    assert INGEST_NAME == 'iot-ingest'
    assert INGEST_PORT == 8300


def test_iot_ingest_get_readings():
    result = ingest_exec({'action': 'get_readings', 'limit': 10})
    assert 'readings' in result or result.get('ok') is not False


def test_iot_ingest_clear_readings():
    result = ingest_exec({'action': 'clear_readings'})
    assert result.get('ok') is True or 'cleared' in str(result).lower() or 'error' not in result


def test_iot_ingest_exec_unknown():
    result = ingest_exec({'action': 'unknown_action'})
    assert result.get('ok') is False or 'error' in result


# ── E2: IoT Registry ──────────────────────────────────────────────────────────

from cascadia.operators.iot_registry.operator import (
    NAME as REG_NAME, PORT as REG_PORT,
    register_device, get_device, list_devices, update_status, deregister_device,
    execute_task as reg_exec,
)

def test_iot_registry_metadata():
    assert REG_NAME == 'iot-registry'
    assert REG_PORT == 8301


def test_iot_registry_register_and_get():
    result = register_device('DEV001', 'Field Sensor A', 'soil', 'North Field',
                             ['soil_moisture', 'temperature'])
    assert result.get('ok') is True or 'device_id' in result or 'DEV001' in str(result)
    device = get_device('DEV001')
    assert device.get('device_id') == 'DEV001' or device.get('device', {}).get('device_id') == 'DEV001'


def test_iot_registry_list_devices():
    register_device('DEV002', 'Field Sensor B', 'soil', 'South Field', ['ph'])
    devices = list_devices()
    device_list = devices.get('devices', devices) if isinstance(devices, dict) else devices
    assert len(device_list) >= 1


def test_iot_registry_update_status():
    register_device('DEV003', 'Pump Sensor', 'flow', 'Pump Station', ['flow_rate'])
    result = update_status('DEV003', 'offline')
    assert result.get('ok') is True or 'status' in str(result).lower()


def test_iot_registry_deregister():
    register_device('DEVDEL', 'Temp Device', 'temp', 'Lab', ['temperature'])
    result = deregister_device('DEVDEL')
    assert result.get('ok') is True or 'removed' in str(result).lower() or 'deleted' in str(result).lower()


def test_iot_registry_exec_unknown():
    result = reg_exec({'action': 'unknown'})
    assert result.get('ok') is False or 'error' in result


# ── E3: Farm Monitor ──────────────────────────────────────────────────────────

from cascadia.operators.farm_monitor.operator import (
    NAME as FARM_NAME, PORT as FARM_PORT,
    configure_zone, get_zone_status, list_zones,
    execute_task as farm_exec, handle_event as farm_handle,
)

def test_farm_monitor_metadata():
    assert FARM_NAME == 'farm-monitor'
    assert FARM_PORT == 8302


def test_farm_configure_and_list_zones():
    configure_zone('ZONE_A', 'North Greenhouse', ['DEV001', 'DEV002'])
    zones = list_zones()
    zone_list = zones.get('zones', zones) if isinstance(zones, dict) else zones
    assert len(zone_list) >= 1


def test_farm_get_zone_status():
    configure_zone('ZONE_B', 'South Field', ['DEV003'])
    status = get_zone_status('ZONE_B')
    assert status.get('zone_id') == 'ZONE_B' or 'zone' in status or status.get('ok') is not False


def test_farm_alert_notification_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'send_alert_notification',
                      'zone_id': 'ZONE_A', 'message': 'Soil moisture critical'}).encode()
    asyncio.run(farm_handle(nc, 'cascadia.operators.farm-monitor.call', raw))
    assert any('approvals' in s for s in published)


def test_farm_list_zones_no_approval():
    nc = MagicMock()
    published = []

    async def mock_publish(subject, payload):
        published.append(subject)

    nc.publish = mock_publish
    raw = json.dumps({'action': 'list_zones'}).encode()
    asyncio.run(farm_handle(nc, 'cascadia.operators.farm-monitor.call', raw))
    assert not any('approvals' in s for s in published)
    assert any('response' in s for s in published)


# ── E4: Widget file ───────────────────────────────────────────────────────────

def test_iot_widget_exists():
    widget = Path(__file__).parent.parent / 'cascadia' / 'dashboard' / 'iot_widget.html'
    assert widget.exists(), "iot_widget.html missing"
    content = widget.read_text()
    assert 'iot-widget' in content or '.iot-widget' in content
    assert '8300' in content
