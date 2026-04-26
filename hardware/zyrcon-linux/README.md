# Zyrcon Linux Server — Setup Guide

## Requirements
- Ubuntu 22.04+ (x86_64 or ARM)
- 8GB RAM minimum
- 100GB storage

## Quick Setup

```bash
# Clone and install
git clone https://github.com/zyrconlabs/cascadia-os.git
cd cascadia-os
bash install.sh

# Or use zero-touch deploy for production
bash hardware/zero_touch_deploy.sh
```

## Hardware Platform Config

In `config.json`:
```json
{
  "hardware_platform": "zyrcon-linux"
}
```

## IoT Support

Linux deployment supports MQTT and Modbus (v0.48) protocols via CONDUIT. Enable in config.json:

```json
{
  "iot": {
    "enabled": true,
    "mqtt": {
      "enabled": true,
      "broker_host": "localhost",
      "broker_port": 1883
    }
  }
}
```

## GPU Acceleration

If your Linux server has an NVIDIA GPU:
1. Install CUDA drivers
2. Build llama.cpp with CUDA: `cmake -DLLAMA_CUDA=ON ..`
3. Set `"n_gpu_layers": 99` in config.json llm section
