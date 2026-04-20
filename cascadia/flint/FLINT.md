# Flint — Cascadia OS Menu Bar Controller

Flint puts Cascadia OS in your menu bar. Start, stop, and monitor every component without opening a terminal.

## What it shows

```
⬡ COS 13/13   ← green = all online
◑ COS 6/13    ← amber = partial
○ COS         ← red = offline
```

Clicking opens the full menu:

- Live status dot for every component with its port number
- SCOUT lead count from vault
- Start / Stop per operator
- Start All / Stop All
- Open PRISM dashboard
- Open Bell (Scout chat widget)
- Open Recon dashboard
- Open logs folder
- Open vault folder

## Auto-install (via install.sh)

The installer detects your menu bar app and copies the plugin automatically:

| Platform | App detected | Plugin destination |
|---|---|---|
| Mac | SwiftBar | `~/Library/Application Support/SwiftBar/Plugins/` |
| Mac | xbar | `~/Library/Application Support/xbar/plugins/` |
| Linux | Argos (GNOME) | `~/.config/argos/` |

If none are detected, the installer prints manual instructions.

## Manual install — SwiftBar (Mac, recommended)

```bash
brew install swiftbar

mkdir -p ~/Library/Application\ Support/SwiftBar/Plugins
cp cascadia/flint/cascadia.5s.sh \
   ~/Library/Application\ Support/SwiftBar/Plugins/
```

Open SwiftBar, select that folder as your plugin directory. Cascadia appears in the menu bar immediately.

## Manual install — xbar (Mac)

```bash
brew install xbar
cp cascadia/flint/cascadia.5s.sh \
   ~/Library/Application\ Support/xbar/plugins/
```

## Manual install — Argos (Linux GNOME)

```bash
sudo apt install gnome-shell-extension-argos
mkdir -p ~/.config/argos
cp cascadia/flint/cascadia.5s.sh ~/.config/argos/
```

## Cross-platform tray (Linux / Windows / Mac fallback)

If you don't have SwiftBar/xbar/Argos, use the Python tray app:

```bash
pip install pystray pillow
python -m cascadia.flint.tray
```

This runs a system tray icon that polls all components every 5 seconds with the same start/stop controls.

## The `5s` in the filename

The `5s` tells SwiftBar/xbar/Argos to refresh the plugin every 5 seconds. Change it to `10s`, `30s`, or `1m` for less frequent polling if needed — just rename the file.

## Ports monitored

| Component | Port |
|---|---|
| FLINT | 4011 |
| CREW | 5100 |
| VAULT | 5101 |
| SENTINEL | 5102 |
| CURTAIN | 5103 |
| BEACON | 6200 |
| STITCH | 6201 |
| VANGUARD | 6202 |
| HANDSHAKE | 6203 |
| BELL | 6204 |
| ALMANAC | 6205 |
| PRISM | 6300 |
| SCOUT | 7000 |
| RECON | 7001 |
