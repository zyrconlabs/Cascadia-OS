# Cascadia OS — Hardware Platform Guide

Overview of certified hardware configurations and platform specs for Cascadia OS deployment.

## Supported Platforms

| Platform | Base Hardware | Status | Price | IoT |
|---------|---------------|--------|-------|-----|
| [zyrcon-mac](zyrcon-mac/specs.json) | Apple Mac mini M4 | Production | $899 | No |
| [zyrcon-linux](zyrcon-linux/specs.json) | x86/ARM Linux | Available | — | Yes |
| [zyrcon-arm](zyrcon-arm/specs.json) | Raspberry Pi 5 8GB | Planned | $199 | Yes |

## Platform Selection

See the individual specs.json files for memory bandwidth, model recommendations, and capability flags.

## Thunderbolt 5 Clustering

8 Mac mini M4 Pro units connected via Thunderbolt 5 provide:
- 273 GB/s × 8 = pooled memory bandwidth for frontier-class models (671B+)
- Fraction of NVIDIA DGX Station cost (~$11,200 vs $100,000)
- Single PRISM fleet dashboard for all nodes
- See Enterprise docs: clustering_guide.md

## OS-Level Scripts

Platform-specific startup scripts:
- macOS: macos/ or install.sh
- Linux: linux/ or hardware/zero_touch_deploy.sh
- Windows: windows/
