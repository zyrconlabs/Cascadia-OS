"""
cascadia/flint/tray.py — Cascadia OS system tray controller
Cross-platform fallback for Linux/Windows (and Mac without SwiftBar).
Requires: pip install pystray pillow

Run: python -m cascadia.flint.tray
"""
from __future__ import annotations
import subprocess, sys, threading, time, urllib.request, json
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print("Install tray dependencies: pip install pystray pillow")
    sys.exit(1)

REPO = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
LOG_DIR = REPO / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

PORTS = {
    "FLINT":     (4011, "/health"),
    "CREW":      (5100, "/health"),
    "VAULT":     (5101, "/health"),
    "SENTINEL":  (5102, "/health"),
    "CURTAIN":   (5103, "/health"),
    "BEACON":    (6200, "/health"),
    "STITCH":    (6201, "/health"),
    "VANGUARD":  (6202, "/health"),
    "HANDSHAKE": (6203, "/health"),
    "BELL":      (6204, "/health"),
    "ALMANAC":   (6205, "/health"),
    "PRISM":     (6300, "/health"),
    "SCOUT":     (7002, "/api/health"),
    "RECON":     (8002, "/api/health"),
}

def check(port: int, path: str) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1)
        return True
    except Exception:
        return False

def online_count() -> int:
    return sum(check(p, h) for p, h in PORTS.values())

def make_icon(online: int, total: int) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if online == total:
        color = (0, 200, 83, 255)
    elif online > 0:
        color = (255, 149, 0, 255)
    else:
        color = (255, 59, 48, 255)
    draw.ellipse([8, 8, 56, 56], fill=color)
    return img

def run_bg(cmd: list[str], log: str) -> None:
    with open(LOG_DIR / log, "a") as f:
        subprocess.Popen(cmd, stdout=f, stderr=f)

def start_all(_=None) -> None:
    run_bg([PYTHON, "-m", "cascadia.kernel.watchdog", "--config", str(REPO / "config.json")], "flint.log")
    time.sleep(3)
    scout = REPO / "cascadia/operators/scout/scout_server.py"
    if scout.exists():
        run_bg([PYTHON, str(scout)], "scout.log")

def stop_all(_=None) -> None:
    for pattern in ["cascadia.kernel.watchdog", "cascadia.kernel.flint",
                    "scout_server.py", "recon_worker.py", "dashboard.py"]:
        subprocess.run(["pkill", "-f", pattern], capture_output=True)

def start_scout(_=None) -> None:
    scout = REPO / "cascadia/operators/scout/scout_server.py"
    if scout.exists():
        run_bg([PYTHON, str(scout)], "scout.log")

def start_recon(_=None) -> None:
    recon = REPO / "cascadia/operators/recon"
    if recon.exists():
        run_bg([PYTHON, str(recon / "recon_worker.py")], "recon.log")
        run_bg([PYTHON, str(recon / "dashboard.py")], "recon-dashboard.log")

def open_url(url: str) -> None:
    import webbrowser
    webbrowser.open(url)

def open_prism(_=None) -> None: open_url("http://localhost:6300/")
def open_bell(_=None) -> None:  open_url("http://localhost:7002/bell")
def open_logs(_=None) -> None:
    import os
    if sys.platform == "darwin": os.system(f"open {LOG_DIR}")
    elif sys.platform == "linux": os.system(f"xdg-open {LOG_DIR}")
    else: os.startfile(LOG_DIR)

def build_menu(icon: pystray.Icon) -> pystray.Menu:
    total = len(PORTS)
    online = online_count()
    flint_up = check(4011, "/health")
    scout_up = check(7002, "/api/health")
    recon_up = check(8002, "/api/health")
    prism_up = check(6300, "/health")

    items = [
        pystray.MenuItem(f"Cascadia OS  {online}/{total} online", None, enabled=False),
        pystray.Menu.SEPARATOR,
    ]

    if online > 0:
        items.append(pystray.MenuItem("Stop All", stop_all))
    else:
        items.append(pystray.MenuItem("Start All", start_all))

    items.append(pystray.Menu.SEPARATOR)

    if not scout_up:
        items.append(pystray.MenuItem("Start Scout", start_scout))
    if not recon_up:
        items.append(pystray.MenuItem("Start Recon", start_recon))

    if prism_up:
        items.append(pystray.MenuItem("Open PRISM", open_prism))
    if scout_up:
        items.append(pystray.MenuItem("Open Bell (Scout)", open_bell))

    items += [
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("View Logs", open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda: icon.stop()),
    ]
    return pystray.Menu(*items)

def update_loop(icon: pystray.Icon) -> None:
    total = len(PORTS)
    while True:
        online = online_count()
        icon.icon  = make_icon(online, total)
        icon.title = f"Cascadia OS — {online}/{total} online"
        icon.menu  = build_menu(icon)
        time.sleep(5)

def main() -> None:
    total = len(PORTS)
    online = online_count()
    icon = pystray.Icon(
        "cascadia",
        make_icon(online, total),
        f"Cascadia OS — {online}/{total} online",
        build_menu(None),
    )
    threading.Thread(target=update_loop, args=(icon,), daemon=True).start()
    icon.run()

if __name__ == "__main__":
    main()
