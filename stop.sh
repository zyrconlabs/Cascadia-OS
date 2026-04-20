#!/bin/bash
echo "Stopping Cascadia OS stack..."
pkill -f "cascadia.kernel.watchdog" 2>/dev/null && echo "✓ Watchdog stopped" || true
pkill -f "cascadia.kernel.flint"   2>/dev/null && echo "✓ FLINT stopped"    || true
pkill -f "operators/recon/dashboard" 2>/dev/null && echo "✓ RECON stopped"  || true
pkill -f "operators/scout/scout_server" 2>/dev/null && echo "✓ SCOUT stopped" || true
lsof -ti :8080 | xargs kill -9 2>/dev/null && echo "✓ llama.cpp stopped" || true
echo "Done."
