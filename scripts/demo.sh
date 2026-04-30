#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Cascadia OS — One-Click Demo Mode
# Goal: fresh install → bash scripts/demo.sh → PRISM looks alive in <60s
#
# Usage:
#   bash scripts/demo.sh           # interactive
#   bash scripts/demo.sh --auto    # no pause prompts (CI / recording)
#   bash scripts/demo.sh --reset   # wipe demo data and exit
# ═══════════════════════════════════════════════════════════════════════════

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

PYTHON="${PYTHON:-python3}"
FLINT_PORT=4011
PRISM_PORT=6300
AUTO="${1:-}"

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; AMBER='\033[0;33m'; BLUE='\033[0;34m'
BOLD='\033[1m'; DIM='\033[2m'; RED='\033[0;31m'; RESET='\033[0m'

step()  { echo -e "\n${BOLD}${BLUE}▸ $1${RESET}"; }
ok()    { echo -e "  ${GREEN}✓${RESET} $1"; }
warn()  { echo -e "  ${AMBER}⚠${RESET}  $1"; }
err()   { echo -e "  ${RED}✗${RESET}  $1"; }
info()  { echo -e "  ${DIM}$1${RESET}"; }

pause() {
    if [[ "$AUTO" != "--auto" && "$AUTO" != "--reset" ]]; then
        echo -e "\n  ${AMBER}Press Enter to continue...${RESET}"
        read -r
    else
        sleep "${1:-1}"
    fi
}

api_get() {
    curl -sf --max-time 4 "http://127.0.0.1:${1}${2}" 2>/dev/null
}

# ── Reset mode ────────────────────────────────────────────────────────────────
if [[ "$AUTO" == "--reset" ]]; then
    step "Resetting demo data"
    "$PYTHON" scripts/reset_demo.py
    ok "Demo data removed"
    exit 0
fi

# ── Header ────────────────────────────────────────────────────────────────────
clear
echo -e "${BOLD}"
echo "  ╔═══════════════════════════════════════════════════════╗"
echo "  ║           CASCADIA OS — DEMO MODE                    ║"
echo "  ║   Gulf Coast HVAC Services · Houston TX              ║"
echo "  ╚═══════════════════════════════════════════════════════╝"
echo -e "${RESET}"
echo "  Scenario: lead arrives → Scout qualifies → Recon researches"
echo "            → Quote drafts proposal → awaiting your approval"
echo ""
echo "  This script seeds realistic demo data so PRISM looks alive"
echo "  immediately. No operators need to be running."
echo ""
pause 1

# ── Step 1: Check / start Cascadia OS ────────────────────────────────────────
step "Checking Cascadia OS"

is_running() {
    api_get "$FLINT_PORT" "/api/flint/status" > /dev/null 2>&1
}

if is_running; then
    ok "Cascadia OS already running"
else
    warn "Cascadia OS not running — starting now..."
    mkdir -p data/logs

    nohup "$PYTHON" -m cascadia.kernel.watchdog --config config.json \
        >> data/logs/flint.log 2>&1 &

    info "Waiting for FLINT to be ready (up to 30s)..."
    WAIT=0
    until is_running || [[ $WAIT -ge 30 ]]; do
        sleep 2; WAIT=$((WAIT + 2))
        printf "."
    done
    echo ""

    if is_running; then
        ok "Cascadia OS started (${WAIT}s)"
    else
        err "Failed to start — check data/logs/flint.log"
        err "Manual start: python3 -m cascadia.kernel.watchdog --config config.json"
        exit 1
    fi
fi

# ── Step 2: Confirm PRISM is up ───────────────────────────────────────────────
step "Confirming PRISM dashboard"

PRISM_WAIT=0
until api_get "$PRISM_PORT" "/" > /dev/null 2>&1 || [[ $PRISM_WAIT -ge 15 ]]; do
    sleep 1; PRISM_WAIT=$((PRISM_WAIT + 1))
done

if api_get "$PRISM_PORT" "/" > /dev/null 2>&1; then
    ok "PRISM is up on port $PRISM_PORT"
else
    warn "PRISM did not respond — demo data will still be seeded"
    warn "Open http://127.0.0.1:$PRISM_PORT once it starts"
fi

# ── Step 3: Reset any previous demo data ─────────────────────────────────────
step "Cleaning previous demo data (if any)"
"$PYTHON" scripts/reset_demo.py 2>/dev/null | grep -v "^$" | sed 's/^/  /'
ok "Clean slate ready"

# ── Step 4: Seed demo data ────────────────────────────────────────────────────
step "Seeding demo data"
echo ""

if ! "$PYTHON" scripts/seed_demo_data.py; then
    err "Seed script failed — check output above"
    exit 1
fi

# ── Step 5: Summary ───────────────────────────────────────────────────────────
step "Demo ready"
echo ""
echo -e "  ${BOLD}What you'll see in PRISM:${RESET}"
echo ""
echo "    Business:  Gulf Coast HVAC Services, Houston TX"
echo "    Lead:      Marcus Webb — emergency AC replacement"
echo "    Status:    1 pending approval (proposal ready to send)"
echo ""
echo "  ${BOLD}Workflow seeded:${RESET}"
echo "    ✓  Scout   — lead qualified (score: 87/100, hot)"
echo "    ✓  Recon   — company researched (12 employees, no contract)"
echo "    ✓  Quote   — proposal drafted (\$8,660 total)"
echo "    ⏳ Waiting — your approval to send proposal to customer"
echo ""
echo -e "  ${BOLD}${GREEN}Open PRISM:${RESET}  http://127.0.0.1:${PRISM_PORT}"
echo ""
echo "  In the Approvals panel, you'll see:"
echo "    Action:     email.send"
echo "    To:         marcus.webb@gulfcoasthvac.com"
echo "    Risk:       medium"
echo "    Decision:   [Approve] [Deny]"
echo ""

# Try to open browser (macOS)
if [[ "$AUTO" != "--auto" ]] && command -v open &>/dev/null; then
    echo -e "  ${DIM}Opening browser in 2s... (Ctrl-C to skip)${RESET}"
    sleep 2
    open "http://127.0.0.1:${PRISM_PORT}" 2>/dev/null || true
fi

echo ""
echo -e "  ${DIM}To reset demo data:  bash scripts/demo.sh --reset${RESET}"
echo -e "  ${DIM}To re-run demo:      bash scripts/demo.sh${RESET}"
echo ""
