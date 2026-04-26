#!/usr/bin/env bash
# Cascadia OS — Server Health Check
# Runs a quick sanity check on a Cascadia node and prints a status report.
# Usage: bash server_health_check.sh [--config config.json] [--json]
set -euo pipefail

CONFIG="${1:-config.json}"
JSON_MODE="${JSON_MODE:-0}"
PRISM_PORT="${PRISM_PORT:-6300}"
PASS=0
WARN=0
FAIL=0

_pass() { echo "  [OK]   $1"; ((PASS++)) || true; }
_warn() { echo "  [WARN] $1"; ((WARN++)) || true; }
_fail() { echo "  [FAIL] $1"; ((FAIL++)) || true; }

echo "=== Cascadia OS Health Check — $(date '+%Y-%m-%d %H:%M:%S') ==="
echo

# ── Python version ────────────────────────────────────────────────────────────

echo "Infrastructure"
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "missing")
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
  _pass "Python $PY_VER"
else
  _fail "Python $PY_VER (3.11+ required)"
fi

# ── Disk space ────────────────────────────────────────────────────────────────

if command -v df >/dev/null 2>&1; then
  DISK_AVAIL=$(df -BG . 2>/dev/null | awk 'NR==2{gsub("G",""); print $4}' || echo 0)
  if [ "${DISK_AVAIL:-0}" -ge 10 ]; then
    _pass "Disk space: ${DISK_AVAIL}G available"
  elif [ "${DISK_AVAIL:-0}" -ge 2 ]; then
    _warn "Disk space: ${DISK_AVAIL}G available (low)"
  else
    _fail "Disk space: ${DISK_AVAIL}G available (critically low)"
  fi
fi

# ── Memory ────────────────────────────────────────────────────────────────────

if [ -f /proc/meminfo ]; then
  MEM_FREE_KB=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
  MEM_FREE_GB=$(( MEM_FREE_KB / 1048576 ))
  if [ "$MEM_FREE_GB" -ge 4 ]; then
    _pass "Memory: ${MEM_FREE_GB}G available"
  elif [ "$MEM_FREE_GB" -ge 1 ]; then
    _warn "Memory: ${MEM_FREE_GB}G available (low)"
  else
    _fail "Memory: ${MEM_FREE_GB}G available (critically low)"
  fi
elif command -v sysctl >/dev/null 2>&1; then
  RAM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
  RAM_GB=$(( RAM_BYTES / 1073741824 ))
  _pass "RAM: ${RAM_GB}G total (macOS)"
fi

# ── Cascadia data directory ───────────────────────────────────────────────────

echo
echo "Data"
for DIR in data/runtime data/logs; do
  if [ -d "$DIR" ]; then
    _pass "Directory: $DIR"
  else
    _fail "Missing directory: $DIR"
  fi
done

if [ -f data/runtime/cascadia.db ]; then
  DB_SIZE=$(du -sh data/runtime/cascadia.db 2>/dev/null | cut -f1)
  _pass "Database: cascadia.db ($DB_SIZE)"
else
  _warn "Database: cascadia.db not found (will be created on first run)"
fi

# ── PRISM health endpoint ─────────────────────────────────────────────────────

echo
echo "Services"
if curl -sf "http://127.0.0.1:$PRISM_PORT/api/prism/health-check" >/dev/null 2>&1; then
  _pass "PRISM responding on port $PRISM_PORT"
else
  _fail "PRISM not responding on port $PRISM_PORT"
fi

# ── Python package imports ────────────────────────────────────────────────────

echo
echo "Python packages"
for PKG in flask flask_cors; do
  if python3 -c "import $PKG" 2>/dev/null; then
    _pass "$PKG"
  else
    _fail "$PKG (pip install $PKG)"
  fi
done

# ── Summary ───────────────────────────────────────────────────────────────────

echo
echo "=== Summary: $PASS passed · $WARN warnings · $FAIL failed ==="

if [ "$JSON_MODE" = "1" ]; then
  python3 -c "
import json, sys
print(json.dumps({'pass': $PASS, 'warn': $WARN, 'fail': $FAIL, 'ok': $FAIL == 0}))
"
fi

[ "$FAIL" -eq 0 ]
