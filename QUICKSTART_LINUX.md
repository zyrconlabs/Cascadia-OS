# Cascadia OS — Linux Quickstart

Get from zero to a running AI operator platform in under 5 minutes on Linux.

---

## 1. Install

```bash
curl -fsSL https://raw.githubusercontent.com/zyrconlabs/cascadia-os/main/install.sh | bash
```

After install, Cascadia starts automatically and opens PRISM at `http://localhost:6300`.
Configure your AI model in **PRISM → Settings** — choose Local (llama.cpp), Cloud API, or Ollama.

| Local Model | Download | RAM needed | Best for |
|------|----------|------------|----------|
| 3B | 2.0 GB | 4 GB | Most systems, fast responses |
| 7B | 4.7 GB | 8 GB | Best quality/speed balance |
| 14B | 8.9 GB | 16 GB | Highest quality |

**Already have models?** Run `bash setup-llm.sh` — it will find existing files and let you point Cascadia at them.

---

## 2. Verify it's running

```bash
curl -s http://127.0.0.1:4011/health
```

Should return: `{"component": "flint", "state": "ready", "ok": true}`

**If port 4011 is not responding:**
```bash
# Check if the process is running
pgrep -f "cascadia.kernel" && echo "running" || echo "not running"

# Start it manually
cd ~/cascadia-os && source .venv/bin/activate
python -m cascadia.kernel.watchdog --config config.json &

# Check logs
tail -50 ~/cascadia-os/data/logs/flint.log
```

---

## 3. Open PRISM dashboard

```bash
xdg-open http://localhost:6300/
```

PRISM shows live status of all 11 kernel components and your installed operators, active workflow runs, pending approvals, and operator health.

---

## 4. Run the demo

```bash
bash demo.sh
```

~90 seconds. Shows a complete lead workflow: intake → classify → approval gate → crash → restart → resume → complete.

Run fully automatic (no Enter prompts):

```bash
bash demo.sh --auto
```

---

## 5. Start / stop manually

```bash
# Start everything
bash start.sh

# Stop everything  
bash stop.sh

# Or start just Cascadia OS
python3 -m cascadia.kernel.watchdog --config config.json
```

---

## 6. Run tests

```bash
python3 -m unittest discover -s tests -v
```

165 tests. All should pass.

---

## System tray integration

### Option 1: Argos (GNOME Shell)

Install [Argos GNOME extension](https://github.com/p-e-w/argos):

```bash
# The installer will automatically link to Argos if detected at:
~/.config/argos/cascadia.5s.sh
```

The Argos menu bar icon shows live status of all components. Click it to:
- Open PRISM dashboard
- Open SCOUT chat widget
- View logs

### Option 2: PyStray (Universal fallback)

Works on any Linux desktop environment:

```bash
# Install dependencies
pip install pystray pillow

# Run the system tray
python -m cascadia.flint.tray
```

To re-link the menu bar plugin after moving the repo:

```bash
bash flint-link.sh
```

---

## Configuration

Your config is at `~/cascadia-os/config.json`. Key settings:

```json
{
  "llm": {
    "provider": "llamacpp",
    "model": "qwen2.5-3b-instruct-q4_k_m.gguf",
    "base_url": "http://127.0.0.1:8080"
  },
  "curtain": {
    "signing_secret": "your-secret-here"
  }
}
```

Generate a strong signing secret:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Where things live

```
~/cascadia-os/
├── config.json          # Your config (not in git)
├── data/
│   ├── runtime/         # SQLite database
│   └── logs/            # Component logs
├── cascadia/            # Source code
│   ├── kernel/          # FLINT + Watchdog
│   ├── dashboard/       # PRISM
│   └── operators/       # SCOUT, RECON, etc.
├── demo.sh              # Investor demo
├── start.sh             # Start full stack
└── stop.sh              # Stop full stack
```

---

## Troubleshooting

**System tray not showing**
If Argos is not installed, run the fallback: `python -m cascadia.flint.tray`

**Port already in use**
Kill stale processes: `pkill -f "cascadia.kernel"` then restart.

**cryptography module not found**
Run: `pip3 install cryptography`

**BELL not responding on :6204**
FLINT tiers up — give it 10-15 seconds after start before all components are healthy.

---

*[Back to README](./README.md)*
