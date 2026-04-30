#!/usr/bin/env python3
"""
Seed demo data — Gulf Coast HVAC Services scenario.
Inserts a realistic lead workflow into Cascadia OS databases so PRISM
looks alive immediately after startup.

Run from cascadia-os root:
    python3 scripts/seed_demo_data.py

Safe to re-run: checks for existing demo run before inserting.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from the cascadia-os root without installing the package.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

# ── Demo identifiers ──────────────────────────────────────────────────────────

DEMO_RUN_ID    = "run-demo-hvac-20260430"
DEMO_NAMESPACE = "demo"

# ── Timestamps (backdated for realism) ───────────────────────────────────────

def _utc(delta_minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=delta_minutes)).isoformat()

T_LEAD_IN  = _utc(240)   # 4h ago — lead arrived
T_SCOUT    = _utc(235)   # Scout: lead qualified
T_RECON    = _utc(227)   # Recon: company researched
T_QUOTE    = _utc(219)   # Quote: proposal drafted + approval requested
T_NOW      = _utc(0)

# ── Config ────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    cfg_path = REPO_ROOT / "config.json"
    if cfg_path.exists():
        return json.loads(cfg_path.read_text())
    return {}

CFG      = _load_config()
DB_PATH  = str(REPO_ROOT / CFG.get("database_path", "data/runtime/cascadia.db"))
VAULT_DB = str(REPO_ROOT / "data/runtime/cascadia_vault.db")
AUDIT_DB = REPO_ROOT / "data/runtime/audit.db"

# ── Demo payload ──────────────────────────────────────────────────────────────

LEAD = {
    "contact_name":  "Marcus Webb",
    "contact_email": "marcus.webb@gulfcoasthvac.com",
    "contact_phone": "+1 (713) 555-0192",
    "job_request":   "Emergency AC replacement for commercial warehouse — 5-ton unit, approx 3,000 sq ft",
    "received_via":  "intake_form",
    "received_at":   T_LEAD_IN,
}

SCOUT_OUTPUT = {
    "operator":       "scout",
    "lead_score":     87,
    "classification": "hot_lead",
    "budget_signal":  "strong",
    "timeline":       "immediate",
    "notes":          "Prospect used 'emergency' and 'ASAP' — high urgency signal. Likely buyer.",
    "completed_at":   T_SCOUT,
}

RECON_OUTPUT = {
    "operator":          "recon",
    "company_name":      "Gulf Coast HVAC Services",
    "location":          "Houston, TX 77002",
    "founded":           2008,
    "employees":         12,
    "revenue_estimate":  "$1.8M/year",
    "industry":          "HVAC / Commercial Mechanical",
    "has_contract":      False,
    "notes":             "Established regional operator, no current maintenance contract. Recent web activity: 'commercial AC replacement Houston'.",
    "completed_at":      T_RECON,
}

QUOTE_OUTPUT = {
    "operator":        "quote",
    "proposal_number": "GCHS-2026-001",
    "service":         "Emergency commercial AC replacement",
    "equipment":       "Carrier WeatherMaker 5-ton RTU (48TC)",
    "line_items": [
        {"description": "Carrier 5-ton RTU unit",      "amount": 4200.00},
        {"description": "Labor — removal and install",  "amount": 2800.00},
        {"description": "Refrigerant charge (R-410A)",  "amount": 420.00},
        {"description": "Electrical disconnect work",   "amount": 580.00},
    ],
    "subtotal":        8000.00,
    "tax":             660.00,
    "total":           8660.00,
    "currency":        "USD",
    "timeline":        "2 business days from approval",
    "valid_until":     _utc(-0)[:10],  # today
    "completed_at":    T_QUOTE,
}

PENDING_ACTION = {
    "action":      "email.send",
    "destination": "marcus.webb@gulfcoasthvac.com",
    "subject":     "Proposal: Emergency AC Replacement — Gulf Coast HVAC Services",
    "risk_level":  "medium",
    "description": "Send proposal to customer",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _already_seeded(db_path: str) -> bool:
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT run_id FROM runs WHERE run_id = ?", (DEMO_RUN_ID,)
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _ensure_dirs() -> None:
    for p in [DB_PATH, VAULT_DB, str(AUDIT_DB)]:
        Path(p).parent.mkdir(parents=True, exist_ok=True)


# ── Seed run + steps + approval ───────────────────────────────────────────────

def seed_run() -> int:
    """Insert run, steps, and pending approval. Returns approval_id."""
    from cascadia.durability.run_store import RunStore
    from cascadia.durability.step_journal import StepJournal

    store   = RunStore(DB_PATH)
    journal = StepJournal(store)

    # Run record
    store.create_run({
        "run_id":          DEMO_RUN_ID,
        "operator_id":     "scout",
        "tenant_id":       "default",
        "goal":            "Qualify lead: Marcus Webb — Gulf Coast HVAC Services, Houston TX",
        "current_step":    "send_proposal",
        "input_snapshot":  LEAD,
        "state_snapshot":  {
            "scout":  SCOUT_OUTPUT,
            "recon":  RECON_OUTPUT,
            "quote":  QUOTE_OUTPUT,
        },
        "run_state":       "waiting_human",
        "process_state":   "paused",
        "lead_received_at": T_LEAD_IN,
        "created_at":      T_LEAD_IN,
        "updated_at":      T_QUOTE,
    })

    # Step 0 — Scout: qualify lead
    journal.append_step(
        run_id       = DEMO_RUN_ID,
        step_name    = "qualify_lead",
        step_index   = 0,
        started_at   = T_LEAD_IN,
        completed_at = T_SCOUT,
        input_state  = LEAD,
        output_state = SCOUT_OUTPUT,
    )

    # Step 1 — Recon: research company
    journal.append_step(
        run_id       = DEMO_RUN_ID,
        step_name    = "research_company",
        step_index   = 1,
        started_at   = T_SCOUT,
        completed_at = T_RECON,
        input_state  = {"lead": LEAD, "scout": SCOUT_OUTPUT},
        output_state = RECON_OUTPUT,
    )

    # Step 2 — Quote: draft proposal
    journal.append_step(
        run_id       = DEMO_RUN_ID,
        step_name    = "draft_proposal",
        step_index   = 2,
        started_at   = T_RECON,
        completed_at = T_QUOTE,
        input_state  = {"lead": LEAD, "scout": SCOUT_OUTPUT, "recon": RECON_OUTPUT},
        output_state = QUOTE_OUTPUT,
    )

    # Pending approval — send proposal email
    approval_id = store.insert_approval({
        "run_id":     DEMO_RUN_ID,
        "step_index": 3,
        "action_key": PENDING_ACTION["action"],
        "decision":   "pending",
        "actor":      None,
        "reason":     "",
        "created_at": T_QUOTE,
        "decided_at": None,
    })

    # Set risk_level (field not in base insert_approval signature)
    store.update_approval(approval_id, risk_level=PENDING_ACTION["risk_level"])

    print(f"  ✓ Run created: {DEMO_RUN_ID}")
    print(f"  ✓ 3 steps inserted (Scout → Recon → Quote)")
    print(f"  ✓ Approval #{approval_id} pending: {PENDING_ACTION['action']}")
    return approval_id


# ── Seed audit trail ──────────────────────────────────────────────────────────

def seed_audit(approval_id: int) -> None:
    from cascadia.system.audit_log import AuditLog

    log = AuditLog(AUDIT_DB)

    log.record(
        event_type  = "lead_received",
        run_id      = DEMO_RUN_ID,
        actor       = "intake_form",
        action_key  = "lead.intake",
        risk_level  = "low",
    )

    log.record(
        event_type  = "operator_run_complete",
        run_id      = DEMO_RUN_ID,
        actor       = "scout",
        decision    = "hot_lead",
        action_key  = "lead.qualify",
        risk_level  = "low",
    )

    log.record(
        event_type  = "operator_run_complete",
        run_id      = DEMO_RUN_ID,
        actor       = "recon",
        decision    = "research_complete",
        action_key  = "company.research",
        risk_level  = "low",
    )

    log.record(
        event_type   = "approval_requested",
        approval_id  = approval_id,
        run_id       = DEMO_RUN_ID,
        actor        = "quote",
        decision     = "pending",
        action_key   = PENDING_ACTION["action"],
        risk_level   = PENDING_ACTION["risk_level"],
    )

    print("  ✓ 4 audit entries written (chain-hashed)")


# ── Seed vault ────────────────────────────────────────────────────────────────

def seed_vault() -> None:
    Path(VAULT_DB).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(VAULT_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vault (
            key        TEXT NOT NULL,
            namespace  TEXT NOT NULL DEFAULT 'default',
            value      TEXT,
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (key, namespace)
        )
    """)

    entries = [
        ("business:gulf_coast_hvac", json.dumps({
            "name":     "Gulf Coast HVAC Services",
            "location": "Houston, TX 77002",
            "industry": "HVAC / Commercial Mechanical",
            "founded":  2008,
        })),
        ("lead:marcus_webb", json.dumps(LEAD)),
        ("proposal:GCHS-2026-001", json.dumps(QUOTE_OUTPUT)),
    ]

    for key, value in entries:
        conn.execute("""
            INSERT OR REPLACE INTO vault (key, namespace, value, created_by, created_at, updated_at)
            VALUES (?, ?, ?, 'demo_seed', ?, ?)
        """, (key, DEMO_NAMESPACE, value, T_QUOTE, T_NOW))

    conn.commit()
    conn.close()
    print("  ✓ 3 vault entries written (business, lead, proposal)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\nCascadia OS — seeding demo data (Gulf Coast HVAC Services)\n")
    _ensure_dirs()

    if _already_seeded(DB_PATH):
        print("  ⚠  Demo data already present — run reset_demo.py first to re-seed\n")
        sys.exit(0)

    approval_id = seed_run()
    seed_audit(approval_id)
    seed_vault()

    print("\n  Done. Open PRISM to see the demo:\n")
    print("    http://127.0.0.1:6300\n")


if __name__ == "__main__":
    main()
