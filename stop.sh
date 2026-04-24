#!/bin/bash
echo "Stopping Cascadia OS stack..."
pkill -f "cascadia.kernel.watchdog" 2>/dev/null && echo "✓ Watchdog stopped" || true
pkill -f "cascadia.kernel.flint"   2>/dev/null && echo "✓ FLINT stopped"    || true
# Commercial operators (cascadia-os-operators) stop themselves — no pkill needed here
lsof -ti :8080 | xargs kill -9 2>/dev/null && echo "✓ llama.cpp stopped" || true
echo "Done."
