#!/bin/bash
# Cascadia OS — Flint Menu Bar Controller
# Compatible with SwiftBar, xbar, and Argos (Linux GNOME)
# Refreshes every 5 seconds.
#
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>
# <swiftbar.version>1.0.0</swiftbar.version>
# <swiftbar.type>streamable</swiftbar.type>

SELF="$0"
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="$REPO_DIR/data/logs"
VAULT_DIR="$REPO_DIR/data/vault"
VENV="$REPO_DIR/venv/bin/python3"
PYTHON="${VENV:-python3}"

mkdir -p "$LOG_DIR"
mkdir -p "$VAULT_DIR/operators/scout"
mkdir -p "$VAULT_DIR/operators/recon"

FLINT_PORT=4011
CREW_PORT=5100
VAULT_PORT=5101
SENTINEL_PORT=5102
CURTAIN_PORT=5103
BEACON_PORT=6200
STITCH_PORT=6201
VANGUARD_PORT=6202
HANDSHAKE_PORT=6203
BELL_PORT=6204
ALMANAC_PORT=6205
PRISM_PORT=6300
SCOUT_PORT=7000
RECON_PORT=7001

# ── Actions ───────────────────────────────────────────────────────────────────
case "$1" in
  start-all)
    cd "$REPO_DIR"
    nohup $PYTHON -m cascadia.kernel.watchdog --config config.json \
      >> "$LOG_DIR/flint.log" 2>&1 &
    sleep 3
    nohup $PYTHON "$REPO_DIR/cascadia/operators/scout/scout_server.py" \
      >> "$LOG_DIR/scout.log" 2>&1 &
    exit 0
    ;;
  stop-all)
    pkill -f "cascadia.kernel.watchdog" 2>/dev/null || true
    pkill -f "cascadia.kernel.flint"   2>/dev/null || true
    pkill -f "scout_server.py"         2>/dev/null || true
    pkill -f "recon_worker.py"         2>/dev/null || true
    pkill -f "dashboard.py"            2>/dev/null || true
    exit 0
    ;;
  start-flint)
    cd "$REPO_DIR"
    nohup $PYTHON -m cascadia.kernel.watchdog --config config.json \
      >> "$LOG_DIR/flint.log" 2>&1 &
    exit 0
    ;;
  stop-flint)
    pkill -f "cascadia.kernel.watchdog" 2>/dev/null || true
    pkill -f "cascadia.kernel.flint"   2>/dev/null || true
    exit 0
    ;;
  start-scout)
    cd "$REPO_DIR/cascadia/operators/scout"
    nohup $PYTHON scout_server.py >> "$LOG_DIR/scout.log" 2>&1 &
    exit 0
    ;;
  stop-scout)
    pkill -f "scout_server.py" 2>/dev/null || true
    exit 0
    ;;
  start-recon)
    cd "$REPO_DIR/cascadia/operators/recon"
    nohup $PYTHON recon_worker.py >> "$LOG_DIR/recon.log" 2>&1 &
    nohup $PYTHON dashboard.py   >> "$LOG_DIR/recon-dashboard.log" 2>&1 &
    exit 0
    ;;
  stop-recon)
    pkill -f "recon_worker.py" 2>/dev/null || true
    pkill -f "dashboard.py"   2>/dev/null || true
    exit 0
    ;;
  open-prism)
    open "http://localhost:$PRISM_PORT/" 2>/dev/null || xdg-open "http://localhost:$PRISM_PORT/"
    exit 0
    ;;
  open-bell)
    open "http://localhost:$SCOUT_PORT/bell" 2>/dev/null || xdg-open "http://localhost:$SCOUT_PORT/bell"
    exit 0
    ;;
  open-recon)
    open "http://localhost:$RECON_PORT/" 2>/dev/null || xdg-open "http://localhost:$RECON_PORT/"
    exit 0
    ;;
  open-logs)
    open "$LOG_DIR" 2>/dev/null || xdg-open "$LOG_DIR"
    exit 0
    ;;
  open-vault)
    open "$VAULT_DIR" 2>/dev/null || xdg-open "$VAULT_DIR"
    exit 0
    ;;
  open-repo)
    open "$REPO_DIR" 2>/dev/null || xdg-open "$REPO_DIR"
    exit 0
    ;;
esac

# ── Health checks ─────────────────────────────────────────────────────────────
check() {
  curl -sf --max-time 1 "http://127.0.0.1:$1$2" > /dev/null 2>&1 && echo "1" || echo "0"
}

flint_up=$(check $FLINT_PORT /health)
crew_up=$(check $CREW_PORT /health)
vault_up=$(check $VAULT_PORT /health)
sentinel_up=$(check $SENTINEL_PORT /health)
curtain_up=$(check $CURTAIN_PORT /health)
beacon_up=$(check $BEACON_PORT /health)
stitch_up=$(check $STITCH_PORT /health)
vanguard_up=$(check $VANGUARD_PORT /health)
handshake_up=$(check $HANDSHAKE_PORT /health)
bell_up=$(check $BELL_PORT /health)
almanac_up=$(check $ALMANAC_PORT /health)
prism_up=$(check $PRISM_PORT /health)
scout_up=$(check $SCOUT_PORT /api/health)
recon_up=$(check $RECON_PORT /api/health)

total=13
online=$(( flint_up + crew_up + vault_up + sentinel_up + curtain_up + \
           beacon_up + stitch_up + vanguard_up + handshake_up + \
           bell_up + almanac_up + prism_up + scout_up ))

# Lead count from vault
LEADS_FILE="$VAULT_DIR/operators/scout/leads.json"
lead_count=0
if [ -f "$LEADS_FILE" ]; then
  lead_count=$(python3 -c "import json; d=json.load(open('$LEADS_FILE')); print(len(d))" 2>/dev/null || echo "0")
fi

# ── Menu bar title ─────────────────────────────────────────────────────────────
if [ "$flint_up" = "1" ] && [ "$online" -ge 10 ]; then
  echo "⬡ COS $online/$total | color=#00C853 font=Menlo-Bold size=12"
elif [ "$online" -gt 0 ]; then
  echo "◑ COS $online/$total | color=#FF9500 font=Menlo-Bold size=12"
else
  echo "○ COS | color=#FF3B30 font=Menlo-Bold size=12"
fi

echo "---"
echo "Cascadia OS v0.32 | font=Menlo-Bold size=13"
echo "---"

# ── Kernel ────────────────────────────────────────────────────────────────────
echo "KERNEL | color=#888888 font=Menlo-Bold size=11"
if [ "$flint_up" = "1" ]; then
  echo "⬤ FLINT          :$FLINT_PORT | color=#00C853 font=Menlo size=12"
  echo "-- Stop FLINT | bash='$SELF' param1=stop-flint terminal=false refresh=true color=#FF6B6B"
else
  echo "◯ FLINT          offline | color=#FF3B30 font=Menlo size=12"
  echo "-- Start FLINT | bash='$SELF' param1=start-flint terminal=false refresh=true color=#00C853"
fi

echo "---"

# ── Foundation ────────────────────────────────────────────────────────────────
echo "FOUNDATION | color=#888888 font=Menlo-Bold size=11"
[ "$crew_up"     = "1" ] && echo "⬤ CREW           :$CREW_PORT | color=#00C853 font=Menlo size=12"     || echo "◯ CREW           :$CREW_PORT | color=#888888 font=Menlo size=12"
[ "$vault_up"    = "1" ] && echo "⬤ VAULT          :$VAULT_PORT | color=#00C853 font=Menlo size=12"    || echo "◯ VAULT          :$VAULT_PORT | color=#888888 font=Menlo size=12"
[ "$sentinel_up" = "1" ] && echo "⬤ SENTINEL       :$SENTINEL_PORT | color=#00C853 font=Menlo size=12" || echo "◯ SENTINEL       :$SENTINEL_PORT | color=#888888 font=Menlo size=12"
[ "$curtain_up"  = "1" ] && echo "⬤ CURTAIN        :$CURTAIN_PORT | color=#00C853 font=Menlo size=12"  || echo "◯ CURTAIN        :$CURTAIN_PORT | color=#888888 font=Menlo size=12"

echo "---"

# ── Runtime ───────────────────────────────────────────────────────────────────
echo "RUNTIME | color=#888888 font=Menlo-Bold size=11"
[ "$beacon_up"    = "1" ] && echo "⬤ BEACON         :$BEACON_PORT | color=#00C853 font=Menlo size=12"    || echo "◯ BEACON         :$BEACON_PORT | color=#888888 font=Menlo size=12"
[ "$stitch_up"    = "1" ] && echo "⬤ STITCH         :$STITCH_PORT | color=#00C853 font=Menlo size=12"    || echo "◯ STITCH         :$STITCH_PORT | color=#888888 font=Menlo size=12"
[ "$vanguard_up"  = "1" ] && echo "⬤ VANGUARD       :$VANGUARD_PORT | color=#00C853 font=Menlo size=12"  || echo "◯ VANGUARD       :$VANGUARD_PORT | color=#888888 font=Menlo size=12"
[ "$handshake_up" = "1" ] && echo "⬤ HANDSHAKE      :$HANDSHAKE_PORT | color=#00C853 font=Menlo size=12" || echo "◯ HANDSHAKE      :$HANDSHAKE_PORT | color=#888888 font=Menlo size=12"
[ "$bell_up"      = "1" ] && echo "⬤ BELL           :$BELL_PORT | color=#00C853 font=Menlo size=12"      || echo "◯ BELL           :$BELL_PORT | color=#888888 font=Menlo size=12"
[ "$almanac_up"   = "1" ] && echo "⬤ ALMANAC        :$ALMANAC_PORT | color=#00C853 font=Menlo size=12"   || echo "◯ ALMANAC        :$ALMANAC_PORT | color=#888888 font=Menlo size=12"
[ "$prism_up"     = "1" ] && echo "⬤ PRISM          :$PRISM_PORT | color=#00C853 font=Menlo size=12"     || echo "◯ PRISM          :$PRISM_PORT | color=#888888 font=Menlo size=12"

echo "---"

# ── Operators ─────────────────────────────────────────────────────────────────
echo "OPERATORS | color=#888888 font=Menlo-Bold size=11"

if [ "$scout_up" = "1" ]; then
  echo "⬤ SCOUT          :$SCOUT_PORT | color=#00C853 font=Menlo size=12"
  echo "  $lead_count leads captured | color=#555555 font=Menlo size=11"
  echo "-- Open Bell (chat) | bash='$SELF' param1=open-bell terminal=false color=#60A5FA"
  echo "-- Stop Scout | bash='$SELF' param1=stop-scout terminal=false refresh=true color=#FF6B6B"
else
  echo "◯ SCOUT          offline | color=#888888 font=Menlo size=12"
  echo "-- Start Scout | bash='$SELF' param1=start-scout terminal=false refresh=true color=#00C853"
fi

if [ "$recon_up" = "1" ]; then
  echo "⬤ RECON          :$RECON_PORT | color=#00C853 font=Menlo size=12"
  echo "-- Open Recon Dashboard | bash='$SELF' param1=open-recon terminal=false color=#60A5FA"
  echo "-- Stop Recon | bash='$SELF' param1=stop-recon terminal=false refresh=true color=#FF6B6B"
else
  echo "◯ RECON          offline | color=#888888 font=Menlo size=12"
  echo "-- Start Recon | bash='$SELF' param1=start-recon terminal=false refresh=true color=#00C853"
fi

echo "---"

# ── Quick actions ─────────────────────────────────────────────────────────────
if [ "$prism_up" = "1" ]; then
  echo "Open PRISM | bash='$SELF' param1=open-prism terminal=false color=#60A5FA"
fi

if [ "$online" -gt 0 ]; then
  echo "Stop All | bash='$SELF' param1=stop-all terminal=false refresh=true color=#FF6B6B"
else
  echo "Start All | bash='$SELF' param1=start-all terminal=false refresh=true color=#00C853"
fi

echo "---"
echo "Open Logs | bash='$SELF' param1=open-logs terminal=false color=#888888"
echo "Open Vault | bash='$SELF' param1=open-vault terminal=false color=#888888"
echo "Open Repo Folder | bash='$SELF' param1=open-repo terminal=false color=#888888"
echo "---"
echo "Refresh | refresh=true color=#444444"
