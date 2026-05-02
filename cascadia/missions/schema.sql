-- Missions layer schema — SQLite, idempotent, all IF NOT EXISTS
-- Run via: python -m cascadia.missions.migrate

-- ──────────────────────────────────────────────────────────────
-- ORGANIZATIONS (must exist before any table that FKs to it)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS organizations (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  slug        TEXT UNIQUE,
  tier        TEXT NOT NULL DEFAULT 'business',
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ──────────────────────────────────────────────────────────────
-- MISSIONS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS missions (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  name            TEXT NOT NULL,
  description     TEXT,
  trigger_type    TEXT NOT NULL DEFAULT 'manual',
  trigger_config  TEXT NOT NULL DEFAULT '{}',
  status          TEXT NOT NULL DEFAULT 'active',
  schedule        TEXT,
  created_by      TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id)
);

-- ──────────────────────────────────────────────────────────────
-- MISSION RUNS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mission_runs (
  id              TEXT PRIMARY KEY,
  mission_id      TEXT NOT NULL,
  org_id          TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  status          TEXT NOT NULL DEFAULT 'pending',
  trigger_data    TEXT NOT NULL DEFAULT '{}',
  context_data    TEXT NOT NULL DEFAULT '{}',
  started_at      TEXT,
  completed_at    TEXT,
  failed_at       TEXT,
  error           TEXT,
  retry_count     INTEGER NOT NULL DEFAULT 0,
  last_error_at   TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (mission_id) REFERENCES missions(id),
  FOREIGN KEY (org_id) REFERENCES organizations(id)
);

-- ──────────────────────────────────────────────────────────────
-- MISSION RUN STEPS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mission_run_steps (
  id              TEXT PRIMARY KEY,
  mission_run_id  TEXT NOT NULL,
  step_name       TEXT NOT NULL,
  step_index      INTEGER NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',
  input_data      TEXT NOT NULL DEFAULT '{}',
  output_data     TEXT,
  error           TEXT,
  started_at      TEXT,
  completed_at    TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (mission_run_id) REFERENCES mission_runs(id)
);

-- ──────────────────────────────────────────────────────────────
-- LEADS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS leads (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  mission_run_id  TEXT,
  source          TEXT,
  status          TEXT NOT NULL DEFAULT 'new',
  name            TEXT,
  email           TEXT,
  phone           TEXT,
  company         TEXT,
  data            TEXT NOT NULL DEFAULT '{}',
  score           REAL,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id),
  FOREIGN KEY (mission_run_id) REFERENCES mission_runs(id)
);

-- ──────────────────────────────────────────────────────────────
-- LEAD ENRICHMENTS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lead_enrichments (
  id                TEXT PRIMARY KEY,
  lead_id           TEXT NOT NULL,
  enrichment_type   TEXT NOT NULL,
  source            TEXT,
  data              TEXT NOT NULL DEFAULT '{}',
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (lead_id) REFERENCES leads(id)
);

-- ──────────────────────────────────────────────────────────────
-- QUOTES
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS quotes (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  mission_run_id  TEXT,
  lead_id         TEXT,
  status          TEXT NOT NULL DEFAULT 'draft',
  title           TEXT,
  line_items      TEXT NOT NULL DEFAULT '[]',
  subtotal        REAL,
  tax             REAL,
  total           REAL,
  notes           TEXT,
  valid_until     TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id),
  FOREIGN KEY (mission_run_id) REFERENCES mission_runs(id),
  FOREIGN KEY (lead_id) REFERENCES leads(id)
);

-- ──────────────────────────────────────────────────────────────
-- PURCHASE ORDERS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS purchase_orders (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  mission_run_id  TEXT,
  quote_id        TEXT,
  status          TEXT NOT NULL DEFAULT 'pending',
  vendor          TEXT,
  items           TEXT NOT NULL DEFAULT '[]',
  total           REAL,
  ordered_at      TEXT,
  received_at     TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id),
  FOREIGN KEY (mission_run_id) REFERENCES mission_runs(id),
  FOREIGN KEY (quote_id) REFERENCES quotes(id)
);

-- ──────────────────────────────────────────────────────────────
-- INVOICES
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS invoices (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  mission_run_id  TEXT,
  quote_id        TEXT,
  lead_id         TEXT,
  status          TEXT NOT NULL DEFAULT 'draft',
  line_items      TEXT NOT NULL DEFAULT '[]',
  subtotal        REAL,
  tax             REAL,
  total           REAL,
  due_date        TEXT,
  paid_at         TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id),
  FOREIGN KEY (mission_run_id) REFERENCES mission_runs(id),
  FOREIGN KEY (quote_id) REFERENCES quotes(id),
  FOREIGN KEY (lead_id) REFERENCES leads(id)
);

-- ──────────────────────────────────────────────────────────────
-- CAMPAIGNS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
  id               TEXT PRIMARY KEY,
  org_id           TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  mission_run_id   TEXT,
  name             TEXT NOT NULL,
  campaign_type    TEXT NOT NULL DEFAULT 'email',
  status           TEXT NOT NULL DEFAULT 'draft',
  target_audience  TEXT NOT NULL DEFAULT '{}',
  content          TEXT NOT NULL DEFAULT '{}',
  scheduled_at     TEXT,
  sent_at          TEXT,
  stats            TEXT NOT NULL DEFAULT '{}',
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id),
  FOREIGN KEY (mission_run_id) REFERENCES mission_runs(id)
);

-- ──────────────────────────────────────────────────────────────
-- CAMPAIGN ITEMS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_items (
  id           TEXT PRIMARY KEY,
  campaign_id  TEXT NOT NULL,
  item_type    TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',
  recipient    TEXT,
  content      TEXT NOT NULL DEFAULT '{}',
  sent_at      TEXT,
  opened_at    TEXT,
  clicked_at   TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

-- ──────────────────────────────────────────────────────────────
-- REVIEW REQUESTS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS review_requests (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  mission_run_id  TEXT,
  lead_id         TEXT,
  status          TEXT NOT NULL DEFAULT 'pending',
  platform        TEXT,
  sent_at         TEXT,
  completed_at    TEXT,
  rating          INTEGER,
  review_text     TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id),
  FOREIGN KEY (mission_run_id) REFERENCES mission_runs(id),
  FOREIGN KEY (lead_id) REFERENCES leads(id)
);

-- ──────────────────────────────────────────────────────────────
-- TASKS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tasks (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  mission_run_id  TEXT,
  assigned_to     TEXT,
  title           TEXT NOT NULL,
  description     TEXT,
  status          TEXT NOT NULL DEFAULT 'open',
  priority        TEXT NOT NULL DEFAULT 'medium',
  due_at          TEXT,
  completed_at    TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id),
  FOREIGN KEY (mission_run_id) REFERENCES mission_runs(id)
);

-- ──────────────────────────────────────────────────────────────
-- BLOCKERS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS blockers (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  mission_run_id  TEXT NOT NULL,
  step_name       TEXT,
  reason          TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'open',
  resolved_at     TEXT,
  resolved_by     TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id),
  FOREIGN KEY (mission_run_id) REFERENCES mission_runs(id)
);

-- ──────────────────────────────────────────────────────────────
-- BRIEFS
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS briefs (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000001',
  mission_run_id  TEXT,
  brief_type      TEXT NOT NULL DEFAULT 'general',
  title           TEXT,
  content         TEXT NOT NULL DEFAULT '{}',
  status          TEXT NOT NULL DEFAULT 'draft',
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id),
  FOREIGN KEY (mission_run_id) REFERENCES mission_runs(id)
);

-- ──────────────────────────────────────────────────────────────
-- INDEXES
-- ──────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_missions_org_id        ON missions(org_id);
CREATE INDEX IF NOT EXISTS idx_missions_status        ON missions(status);

CREATE INDEX IF NOT EXISTS idx_mission_runs_mission_id ON mission_runs(mission_id);
CREATE INDEX IF NOT EXISTS idx_mission_runs_org_id     ON mission_runs(org_id);
CREATE INDEX IF NOT EXISTS idx_mission_runs_status     ON mission_runs(status);

CREATE INDEX IF NOT EXISTS idx_mission_run_steps_run   ON mission_run_steps(mission_run_id);

CREATE INDEX IF NOT EXISTS idx_leads_org_id           ON leads(org_id);
CREATE INDEX IF NOT EXISTS idx_leads_mission_run_id   ON leads(mission_run_id);
CREATE INDEX IF NOT EXISTS idx_leads_status           ON leads(status);

CREATE INDEX IF NOT EXISTS idx_lead_enrichments_lead  ON lead_enrichments(lead_id);

CREATE INDEX IF NOT EXISTS idx_quotes_org_id          ON quotes(org_id);
CREATE INDEX IF NOT EXISTS idx_quotes_mission_run_id  ON quotes(mission_run_id);
CREATE INDEX IF NOT EXISTS idx_quotes_lead_id         ON quotes(lead_id);

CREATE INDEX IF NOT EXISTS idx_purchase_orders_org_id         ON purchase_orders(org_id);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_mission_run_id ON purchase_orders(mission_run_id);

CREATE INDEX IF NOT EXISTS idx_invoices_org_id          ON invoices(org_id);
CREATE INDEX IF NOT EXISTS idx_invoices_mission_run_id  ON invoices(mission_run_id);
CREATE INDEX IF NOT EXISTS idx_invoices_lead_id         ON invoices(lead_id);

CREATE INDEX IF NOT EXISTS idx_campaigns_org_id         ON campaigns(org_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_mission_run_id ON campaigns(mission_run_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_status         ON campaigns(status);

CREATE INDEX IF NOT EXISTS idx_campaign_items_campaign  ON campaign_items(campaign_id);

CREATE INDEX IF NOT EXISTS idx_review_requests_org_id         ON review_requests(org_id);
CREATE INDEX IF NOT EXISTS idx_review_requests_mission_run_id ON review_requests(mission_run_id);
CREATE INDEX IF NOT EXISTS idx_review_requests_lead_id        ON review_requests(lead_id);

CREATE INDEX IF NOT EXISTS idx_tasks_org_id         ON tasks(org_id);
CREATE INDEX IF NOT EXISTS idx_tasks_mission_run_id ON tasks(mission_run_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status         ON tasks(status);

CREATE INDEX IF NOT EXISTS idx_blockers_org_id         ON blockers(org_id);
CREATE INDEX IF NOT EXISTS idx_blockers_mission_run_id ON blockers(mission_run_id);

CREATE INDEX IF NOT EXISTS idx_briefs_org_id         ON briefs(org_id);
CREATE INDEX IF NOT EXISTS idx_briefs_mission_run_id ON briefs(mission_run_id);

-- ──────────────────────────────────────────────────────────────
-- EXTEND APPROVALS TABLE (backward compatible — columns nullable)
-- Handled via Python column-existence check in migrate.py
-- ──────────────────────────────────────────────────────────────
ALTER TABLE approvals ADD COLUMN mission_id     TEXT;
ALTER TABLE approvals ADD COLUMN mission_run_id TEXT;

-- mission_runs additional columns (added in s3b)
ALTER TABLE mission_runs ADD COLUMN workflow_id    TEXT;
ALTER TABLE mission_runs ADD COLUMN trigger_type   TEXT;
ALTER TABLE mission_runs ADD COLUMN parent_run_id  TEXT;

CREATE INDEX IF NOT EXISTS idx_approvals_mission_id     ON approvals(mission_id);
CREATE INDEX IF NOT EXISTS idx_approvals_mission_run_id ON approvals(mission_run_id);

-- ──────────────────────────────────────────────────────────────
-- MISSION ITEMS  (revenue pipeline items surfaced from email scan)
-- ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mission_items (
  id                TEXT PRIMARY KEY,
  mission_id        TEXT NOT NULL,
  mission_run_id    TEXT,
  item_type         TEXT NOT NULL,
  title             TEXT NOT NULL,
  description       TEXT,
  source_type       TEXT,
  source_id         TEXT,
  customer_name     TEXT,
  company_name      TEXT,
  amount            REAL,
  due_date          TEXT,
  confidence        REAL,
  urgency_score     INTEGER DEFAULT 0,
  value_score       INTEGER DEFAULT 0,
  status            TEXT DEFAULT 'new',
  recommended_action TEXT,
  approval_required INTEGER DEFAULT 1,
  raw_json          TEXT,
  created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mission_items_mission_id   ON mission_items(mission_id);
CREATE INDEX IF NOT EXISTS idx_mission_items_status       ON mission_items(status);
CREATE INDEX IF NOT EXISTS idx_mission_items_item_type    ON mission_items(item_type);

-- ──────────────────────────────────────────────────────────────
-- DEFAULT ORGANIZATION ROW
-- ──────────────────────────────────────────────────────────────
INSERT INTO organizations (id, name, slug, tier)
VALUES (
  '00000000-0000-0000-0000-000000000001',
  'Local Business',
  'local',
  'business'
)
ON CONFLICT (id) DO NOTHING;
