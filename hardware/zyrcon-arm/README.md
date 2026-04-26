# Zyrcon IoT Node — Raspberry Pi 5 Setup Guide

**Status: Planned (v0.48)**

## Requirements
- Raspberry Pi 5 (8GB RAM)
- 32GB+ microSD card (Class 10 or faster)
- Raspberry Pi OS 64-bit Bookworm

## Quick Setup (when available)

```bash
# Flash Raspberry Pi OS 64-bit Bookworm
# Enable SSH in Raspberry Pi Imager

# Install Cascadia OS
git clone https://github.com/zyrconlabs/cascadia-os.git
cd cascadia-os
bash install.sh
```

## Hardware Platform Config

In `config.json`:
```json
{
  "hardware_platform": "zyrcon-arm"
}
```

## IoT Capabilities

The zyrcon-arm platform supports:
- MQTT (v0.47)
- Modbus TCP/RTU (v0.48)
- Zigbee (v0.49)
- GPIO direct pin access (v0.49)

## Use Cases

- Greenhouse sensor gateway
- Industrial equipment monitoring node
- Building automation edge device
- Agricultural IoT coordinator

## Performance Note

With 10 GB/s memory bandwidth, this platform is optimized for 3B parameter models. Use for sensor processing and workflow coordination, not complex reasoning tasks. Complex analysis can be offloaded to a zyrcon-mac or zyrcon-linux node via fleet routing.
