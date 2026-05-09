#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Cascadia OS — full stack shutdown
# Stops all services in reverse startup order with PID file + port fallback.
# ═══════════════════════════════════════════════════════════════════════════
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# Signal intentional stop so LaunchAgent does not restart the stack
touch "$REPO/data/runtime/cascadia.stopped"

echo "Stopping Cascadia OS stack..."
echo ""

# ── Helper: stop by PID file (primary) + port kill (fallback) ─────────────
stop_service() {
    local name=$1
    local port=$2
    local pid_file="data/runtime/pids/${name}.pid"

    echo "▸ Stopping $name..."

    if [ -f "$pid_file" ]; then
        PID=$(cat "$pid_file")
        if kill -0 "$PID" 2>/dev/null; then
            kill -TERM "$PID" 2>/dev/null
            sleep 1
            kill -9 "$PID" 2>/dev/null || true
        fi
        rm -f "$pid_file"
    fi

    PORT_PID=$(lsof -ti:"$port" 2>/dev/null)
    if [ -n "$PORT_PID" ]; then
        kill -9 $PORT_PID 2>/dev/null || true
    fi

    echo "✓ $name stopped"
}

# ── 9. Health Monitor ─────────────────────────────────────────────────────
stop_service "health_monitor" 6209

# CHIEF and SOCIAL stopped by OperatorManager stop_all() — do not stop here

# ── 6.5. RECON ────────────────────────────────────────────────────────────
stop_service "recon" 8002

# ── 6. Mission Manager ────────────────────────────────────────────────────
stop_service "mission_manager" 6207

# ── 5. PRISM Dashboard ────────────────────────────────────────────────────
stop_service "prism" 6300

# ── 4. Cascadia OS (watchdog + all FLINT child components) ────────────────
echo "▸ Stopping Cascadia OS..."
pkill -f "cascadia.kernel.watchdog" 2>/dev/null || true
pkill -f "cascadia.kernel.flint"    2>/dev/null || true
for _PORT in 5100 5101 5102 5103 \
             6200 6201 6202 6203 6204 6205 6206 \
             6207 6100 4011; do
    _PID=$(lsof -ti:$_PORT 2>/dev/null)
    [ -n "$_PID" ] && kill -TERM $_PID 2>/dev/null || true
done
sleep 2
echo "▸ Verifying all component ports are clear..."
COMPONENT_PORTS="4011 5100 5101 5102 5103 6200 6201 6202 6203 6204 6205 6206 6207 6300 6301 6100"
MAX_WAIT=15
ELAPSED=0
ALL_CLEAR=false
while [ $ELAPSED -lt $MAX_WAIT ]; do
    STILL_BOUND=""
    for PORT in $COMPONENT_PORTS; do
        if lsof -ti:$PORT > /dev/null 2>&1; then
            STILL_BOUND="$STILL_BOUND $PORT"
            lsof -ti:$PORT | xargs kill -9 2>/dev/null || true
        fi
    done
    if [ -z "$STILL_BOUND" ]; then
        ALL_CLEAR=true
        break
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done
if [ "$ALL_CLEAR" = true ]; then
    echo "✓ All component ports clear"
else
    echo "⚠ Ports still bound after ${MAX_WAIT}s:$STILL_BOUND"
    echo "  Force-killed — safe to restart"
fi
echo "✓ Cascadia OS components stopped"

# ── 3. License Gate ───────────────────────────────────────────────────────
stop_service "license_gate" 6100

# ── 2. llama.cpp ──────────────────────────────────────────────────────────
stop_service "llama" 8080

# ── 1. NATS ───────────────────────────────────────────────────────────────
echo "▸ Stopping NATS..."
NATS_PF="data/runtime/pids/nats.pid"
if [ -f "$NATS_PF" ]; then
    NATS_PID=$(cat "$NATS_PF")
    if kill -0 "$NATS_PID" 2>/dev/null; then
        kill -TERM "$NATS_PID" 2>/dev/null
        sleep 1
    fi
    rm -f "$NATS_PF"
fi
NATS_PORT_PID=$(lsof -ti:4222 2>/dev/null)
if [ -n "$NATS_PORT_PID" ]; then
    kill -TERM $NATS_PORT_PID 2>/dev/null || true
    sleep 1
    echo "✓ NATS stopped"
else
    echo "  NATS was not running"
fi

# ── Port verification ──────────────────────────────────────────────────────
echo ""
echo "Verifying clean shutdown..."
CONFLICTS=""
for _P in 4222 4011 5100 6100 6207 6300 8002 8006 8011 6209; do
    _PPID=$(lsof -ti:$_P 2>/dev/null)
    [ -n "$_PPID" ] && CONFLICTS="$CONFLICTS $_P($_PPID)"
done
if [ -z "$CONFLICTS" ]; then
    echo "✓ All ports clear — safe to restart"
else
    echo "⚠ Ports still in use:$CONFLICTS"
    echo "  Run: kill -9 \$(lsof -ti:PORT) to force clear"
fi

echo ""
echo "Done."
