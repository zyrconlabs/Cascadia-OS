# Zyrcon AI Server — Mac mini M4 Setup Guide

## Requirements
- macOS 14 (Sonoma) or later
- 16GB RAM minimum (24GB recommended for 14B models)
- 256GB storage minimum

## Quick Setup

```bash
# Clone and install
git clone https://github.com/zyrconlabs/cascadia-os.git
cd cascadia-os
bash install.sh

# Install llama.cpp (for local inference)
brew install llama.cpp

# Configure and start
cp config.example.json config.json
# Edit config.json: set hardware_platform to "zyrcon-mac"
bash start.sh
```

## Hardware Platform Config

In `config.json`:
```json
{
  "hardware_platform": "zyrcon-mac"
}
```

BEACON will load `hardware/zyrcon-mac/specs.json` and use it for inference routing decisions.

## Thunderbolt 5 Clustering

The Mac mini M4 supports Thunderbolt 5 clustering for frontier-class models. Connect 8 units via Thunderbolt 5 hub to pool 273 GB/s × 8 = effective bandwidth for 671B+ parameter models.

See Enterprise docs: `clustering_guide.md`

## Recommended Models

| Model | RAM | Use case |
|-------|-----|---------|
| qwen2.5-3b | 8GB | Fast tasks, quick responses |
| qwen2.5-7b | 16GB | Recommended for most workflows |
| qwen2.5-14b | 24GB | Complex reasoning, large documents |
