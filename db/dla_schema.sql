-- ============================================================
-- Data Lifecycle Agent — PostgreSQL Schema
-- ============================================================
-- Run order matters: referenced tables must exist before
-- foreign keys are created. Execute this file in one shot.
-- ============================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm"; -- for text search on reasoning

-- ============================================================
-- ENUM TYPES
-- ============================================================

CREATE TYPE user_role AS ENUM ('viewer', 'analyst', 'admin');

CREATE TYPE conversation_state AS ENUM (
  'active', 'pending_analysis', 'compressed', 'deleted', 'safety_locked'
);

CREATE TYPE job_status AS ENUM (
  'queued', 'running', 'completed', 'failed', 'standdown'
);

CREATE TYPE verdict AS ENUM ('keep', 'compress', 'delete', 'standdown');

CREATE TYPE compression_strategy AS ENUM ('summary', 'keypoints', 'qa');

CREATE TYPE scheduler_trigger AS ENUM ('cron', 'manual', 'api');

CREATE TYPE scheduler_status AS ENUM (
  'running', 'completed', 'aborted', 'standdown'
);

CREATE TYPE audit_event_type AS ENUM (
  'job_queued', 'job_started', 'job_completed', 'job_failed',
  'verdict_issued', 'confirmation_sent', 'confirmation_received',
  'deletion_executed', 'compression_executed',
  'standdown', 'safety_block', 'auth_login', 'auth_logout',
  'settings_changed', 'scheduler_run_started', 'scheduler_run_completed'
);

CREATE TYPE audit_actor_type AS ENUM ('agent', 'user', 'system');

CREATE TYPE safety_review_status AS ENUM ('pending', 'reviewed', 'cleared');

-- ============================================================
-- USERS
-- ============================================================

CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email           VARCHAR(320) NOT NULL UNIQUE,
  role            user_role    NOT NULL DEFAULT 'analyst',
  -- JSON settings: scheduler prefs, deletion thresholds, notification prefs
  settings        JSONB        NOT NULL DEFAULT '{}'::jsonb,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_email ON users (email);

-- ============================================================
-- CONVERSATIONS
-- Metadata only — no conversation content stored here.
-- Content lives in the conversation store (S3 / external DB).
-- ============================================================

CREATE TABLE conversations (
  id                  UUID              PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id             UUID              NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  -- ID in the external conversation storage system (S3 key, external DB id, etc.)
  external_id         VARCHAR(512)      NOT NULL UNIQUE,
  size_bytes          INTEGER           NOT NULL DEFAULT 0,
  token_count         INTEGER           NOT NULL DEFAULT 0,
  access_count        INTEGER           NOT NULL DEFAULT 0,
  last_accessed_at    TIMESTAMPTZ,
  -- Lifecycle state
  state               conversation_state NOT NULL DEFAULT 'active',
  -- Scores from semantic scorer (NULL until first analysis)
  uniqueness_score    FLOAT,
  utility_score       FLOAT,
  last_scored_at      TIMESTAMPTZ,
  -- Compression metadata (populated after compression)
  compression_ratio   FLOAT,
  compressed_at       TIMESTAMPTZ,
  -- Timestamps
  created_at          TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ       NOT NULL DEFAULT NOW(),

  CONSTRAINT valid_uniqueness CHECK (uniqueness_score IS NULL OR (uniqueness_score >= 0 AND uniqueness_score <= 1)),
  CONSTRAINT valid_utility    CHECK (utility_score IS NULL OR (utility_score >= 0 AND utility_score <= 1))
);

CREATE INDEX idx_conv_user        ON conversations (user_id);
CREATE INDEX idx_conv_state       ON conversations (state);
CREATE INDEX idx_conv_accessed    ON conversations (last_accessed_at);
CREATE INDEX idx_conv_scored      ON conversations (last_scored_at);
CREATE INDEX idx_conv_created     ON conversations (created_at);
-- Partial index: only active, unscored, eligible conversations
CREATE INDEX idx_conv_eligible    ON conversations (user_id, created_at)
  WHERE state = 'active' AND uniqueness_score IS NULL;

-- ============================================================
-- SAFETY FLAGS
-- Read-only from the agent's perspective (enforced via RLS).
-- The agent can only SELECT — never INSERT, UPDATE, or DELETE.
-- ============================================================

CREATE TABLE safety_flags (
  conversation_id     UUID                 PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
  flag_reason         VARCHAR(1024)        NOT NULL,
  flagged_by_system   VARCHAR(128)         NOT NULL,
  review_status       safety_review_status NOT NULL DEFAULT 'pending',
  flagged_at          TIMESTAMPTZ          NOT NULL DEFAULT NOW(),
  reviewed_at         TIMESTAMPTZ,
  reviewed_by         UUID                 REFERENCES users (id)
);

-- ============================================================
-- COST SNAPSHOTS
-- Point-in-time pricing captured by the cost oracle.
-- Used by the decision engine to look up historical cost at
-- the time a decision was made.
-- ============================================================

CREATE TABLE cost_snapshots (
  id                        UUID    PRIMARY KEY DEFAULT uuid_generate_v4(),
  provider                  VARCHAR(64)  NOT NULL,   -- 'aws', 'gcp', 'azure'
  region                    VARCHAR(64),
  storage_cost_per_gb_day   NUMERIC(12,8) NOT NULL,
  compute_cost_per_ktok     NUMERIC(12,8) NOT NULL,
  peak_factor               NUMERIC(6,3) NOT NULL DEFAULT 1.0,
  captured_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cost_captured ON cost_snapshots (captured_at DESC);
CREATE INDEX idx_cost_provider ON cost_snapshots (provider, captured_at DESC);

-- ============================================================
-- SCHEDULER RUNS
-- One row per execution of the batch scheduler.
-- ============================================================

CREATE TABLE scheduler_runs (
  id                  UUID              PRIMARY KEY DEFAULT uuid_generate_v4(),
  triggered_by        scheduler_trigger NOT NULL DEFAULT 'cron',
  triggered_by_user   UUID              REFERENCES users (id),
  status              scheduler_status  NOT NULL DEFAULT 'running',
  standdown_reason    VARCHAR(512),
  -- Counters (updated as jobs are processed)
  jobs_queued         INTEGER           NOT NULL DEFAULT 0,
  jobs_processed      INTEGER           NOT NULL DEFAULT 0,
  jobs_kept           INTEGER           NOT NULL DEFAULT 0,
  jobs_compressed     INTEGER           NOT NULL DEFAULT 0,
  jobs_deleted        INTEGER           NOT NULL DEFAULT 0,
  jobs_failed         INTEGER           NOT NULL DEFAULT 0,
  -- Financial summary
  total_saving_usd    NUMERIC(12,6)     NOT NULL DEFAULT 0,
  agent_cost_usd      NUMERIC(12,6)     NOT NULL DEFAULT 0,
  net_saving_usd      NUMERIC(12,6)     NOT NULL DEFAULT 0,
  -- System conditions at time of run
  compute_load_pct    INTEGER,
  peak_factor         NUMERIC(6,3),
  -- Timing
  started_at          TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
  completed_at        TIMESTAMPTZ
);

CREATE INDEX idx_sched_started ON scheduler_runs (started_at DESC);
CREATE INDEX idx_sched_status  ON scheduler_runs (status);

-- ============================================================
-- ANALYSIS JOBS
-- One row per conversation analysed in a batch.
-- ============================================================

CREATE TABLE analysis_jobs (
  id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  conversation_id     UUID        NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
  scheduler_run_id    UUID        REFERENCES scheduler_runs (id),
  status              job_status  NOT NULL DEFAULT 'queued',
  standdown_reason    VARCHAR(512),
  -- Agent introspection: what did it cost to run this analysis?
  agent_tokens_used   INTEGER     NOT NULL DEFAULT 0,
  agent_cost_usd      NUMERIC(12,8) NOT NULL DEFAULT 0,
  -- Cost snapshot used for this analysis
  cost_snapshot_id    UUID        REFERENCES cost_snapshots (id),
  -- Timing
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at          TIMESTAMPTZ,
  completed_at        TIMESTAMPTZ,
  -- Error detail (if failed)
  error_message       TEXT
);

CREATE INDEX idx_jobs_conv     ON analysis_jobs (conversation_id);
CREATE INDEX idx_jobs_run      ON analysis_jobs (scheduler_run_id);
CREATE INDEX idx_jobs_status   ON analysis_jobs (status);
CREATE INDEX idx_jobs_created  ON analysis_jobs (created_at DESC);

-- ============================================================
-- DECISIONS
-- The verdict issued by the decision engine for each job.
-- One decision per job. Immutable once created.
-- ============================================================

CREATE TABLE decisions (
  id                      UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  job_id                  UUID        NOT NULL UNIQUE REFERENCES analysis_jobs (id) ON DELETE CASCADE,
  conversation_id         UUID        NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
  -- The verdict
  verdict                 verdict     NOT NULL,
  -- Cost breakdown at decision time
  storage_saving_usd      NUMERIC(12,8) NOT NULL DEFAULT 0,
  recompute_cost_usd      NUMERIC(12,8) NOT NULL DEFAULT 0,
  agent_cost_usd          NUMERIC(12,8) NOT NULL DEFAULT 0,
  net_saving_usd          NUMERIC(12,8) NOT NULL DEFAULT 0,
  -- Scores used
  uniqueness_score        FLOAT,
  utility_score           FLOAT,
  -- Reasoning from semantic scorer
  reasoning               TEXT,
  -- Confirmation gate
  confirmation_required   BOOLEAN     NOT NULL DEFAULT FALSE,
  confirmation_token      VARCHAR(512) UNIQUE,   -- signed JWT for email link
  confirmation_expires_at TIMESTAMPTZ,
  confirmed_at            TIMESTAMPTZ,
  confirmed_by            UUID        REFERENCES users (id),
  rejected_at             TIMESTAMPTZ,
  rejected_by             UUID        REFERENCES users (id),
  -- Execution
  executed_at             TIMESTAMPTZ,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_dec_conv        ON decisions (conversation_id);
CREATE INDEX idx_dec_verdict     ON decisions (verdict);
CREATE INDEX idx_dec_created     ON decisions (created_at DESC);
CREATE INDEX idx_dec_pending     ON decisions (id)
  WHERE confirmation_required = TRUE AND confirmed_at IS NULL AND rejected_at IS NULL;

-- ============================================================
-- COMPRESSIONS
-- Record of every compression operation executed.
-- ============================================================

CREATE TABLE compressions (
  id                    UUID                  PRIMARY KEY DEFAULT uuid_generate_v4(),
  decision_id           UUID                  NOT NULL UNIQUE REFERENCES decisions (id) ON DELETE CASCADE,
  conversation_id       UUID                  NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
  strategy              compression_strategy  NOT NULL,
  original_size_bytes   INTEGER               NOT NULL,
  compressed_size_bytes INTEGER               NOT NULL,
  compression_ratio     FLOAT                 NOT NULL,
  tokens_used           INTEGER               NOT NULL DEFAULT 0,
  cost_usd              NUMERIC(12,8)         NOT NULL DEFAULT 0,
  -- Rollback window: original preserved for 48hr post-compression
  original_preserved_until TIMESTAMPTZ,
  rolled_back_at        TIMESTAMPTZ,
  created_at            TIMESTAMPTZ           NOT NULL DEFAULT NOW(),

  CONSTRAINT valid_ratio CHECK (compression_ratio > 0 AND compression_ratio <= 1)
);

CREATE INDEX idx_comp_conv    ON compressions (conversation_id);
CREATE INDEX idx_comp_created ON compressions (created_at DESC);

-- ============================================================
-- DELETIONS
-- Record of every permanent deletion executed.
-- ============================================================

CREATE TABLE deletions (
  id                    UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  decision_id           UUID        NOT NULL UNIQUE REFERENCES decisions (id) ON DELETE CASCADE,
  conversation_id       UUID        NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
  confirmed_by          UUID        NOT NULL REFERENCES users (id),
  storage_freed_bytes   INTEGER     NOT NULL DEFAULT 0,
  net_saving_usd        NUMERIC(12,8) NOT NULL DEFAULT 0,
  confirmed_at          TIMESTAMPTZ NOT NULL,
  executed_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_del_conv    ON deletions (conversation_id);
CREATE INDEX idx_del_created ON deletions (executed_at DESC);

-- ============================================================
-- AUDIT LOG
-- Append-only. No UPDATE or DELETE ever. Enforced via RLS.
-- Every agent action, user action, and system event is recorded
-- here with full context in detail JSONB.
-- ============================================================

CREATE TABLE audit_log (
  id              BIGSERIAL       PRIMARY KEY,  -- sequential, not UUID — ordering matters
  event_type      audit_event_type NOT NULL,
  actor_type      audit_actor_type NOT NULL,
  actor_id        UUID,           -- user id or NULL for system/agent events
  -- References (all nullable — not every event touches every entity)
  conversation_id UUID,
  job_id          UUID,
  decision_id     UUID,
  scheduler_run_id UUID,
  -- Full event context
  detail          JSONB           NOT NULL DEFAULT '{}'::jsonb,
  -- Network context
  ip_address      INET,
  user_agent      VARCHAR(512),
  created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_created      ON audit_log (created_at DESC);
CREATE INDEX idx_audit_event        ON audit_log (event_type);
CREATE INDEX idx_audit_actor        ON audit_log (actor_id);
CREATE INDEX idx_audit_conv         ON audit_log (conversation_id);
CREATE INDEX idx_audit_job          ON audit_log (job_id);
-- GIN index for JSONB detail queries
CREATE INDEX idx_audit_detail       ON audit_log USING GIN (detail);

-- ============================================================
-- ROW-LEVEL SECURITY
-- Enforces architectural constraints at the database level,
-- not just the application level.
-- ============================================================

ALTER TABLE safety_flags ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log    ENABLE ROW LEVEL SECURITY;

-- Agent role: can only SELECT safety_flags, never write
CREATE POLICY safety_flags_agent_read
  ON safety_flags FOR SELECT
  TO dla_agent
  USING (true);

-- Audit log: anyone can INSERT, nobody can UPDATE or DELETE
CREATE POLICY audit_log_insert_only
  ON audit_log FOR INSERT
  TO dla_agent, dla_api
  WITH CHECK (true);

-- No SELECT policy needed — audit reads go through API layer only

-- ============================================================
-- DATABASE ROLES
-- ============================================================

-- Application roles (passwords set at deploy time via secrets manager)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dla_agent') THEN
    CREATE ROLE dla_agent LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dla_api') THEN
    CREATE ROLE dla_api LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dla_readonly') THEN
    CREATE ROLE dla_readonly LOGIN;
  END IF;
END
$$;

-- Agent worker: full access to operational tables, read-only on safety_flags
GRANT SELECT, INSERT, UPDATE ON
  conversations, analysis_jobs, decisions, compressions,
  deletions, scheduler_runs, cost_snapshots
TO dla_agent;
GRANT INSERT ON audit_log TO dla_agent;
GRANT SELECT ON safety_flags TO dla_agent;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO dla_agent;

-- API layer: read + limited writes (no direct deletion execution)
GRANT SELECT, INSERT, UPDATE ON
  users, conversations, analysis_jobs, decisions,
  scheduler_runs, cost_snapshots, compressions
TO dla_api;
GRANT INSERT ON audit_log TO dla_api;
GRANT SELECT ON safety_flags TO dla_api;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO dla_api;

-- Dashboard read replica role
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dla_readonly;

-- ============================================================
-- USEFUL VIEWS
-- ============================================================

-- Dashboard summary: pending confirmations
CREATE VIEW v_pending_confirmations AS
SELECT
  d.id              AS decision_id,
  d.conversation_id,
  c.user_id,
  d.verdict,
  d.net_saving_usd,
  d.reasoning,
  d.confirmation_expires_at,
  d.created_at
FROM decisions d
JOIN conversations c ON c.id = d.conversation_id
WHERE d.confirmation_required = TRUE
  AND d.confirmed_at IS NULL
  AND d.rejected_at IS NULL
  AND d.confirmation_expires_at > NOW()
ORDER BY d.created_at;

-- ROI view: savings vs agent cost per scheduler run
CREATE VIEW v_run_roi AS
SELECT
  sr.id,
  sr.started_at,
  sr.status,
  sr.jobs_processed,
  sr.jobs_deleted,
  sr.jobs_compressed,
  sr.total_saving_usd,
  sr.agent_cost_usd,
  sr.net_saving_usd,
  CASE
    WHEN sr.agent_cost_usd > 0
    THEN ROUND((sr.net_saving_usd / sr.agent_cost_usd)::numeric, 2)
    ELSE NULL
  END AS roi_ratio
FROM scheduler_runs sr
ORDER BY sr.started_at DESC;

-- Conversation health: all metadata in one place
CREATE VIEW v_conversation_health AS
SELECT
  c.id,
  c.user_id,
  c.external_id,
  c.size_bytes,
  c.token_count,
  c.access_count,
  c.state,
  c.uniqueness_score,
  c.utility_score,
  c.last_accessed_at,
  EXTRACT(DAY FROM NOW() - c.created_at)::int AS age_days,
  sf.flag_reason IS NOT NULL AS is_flagged,
  d.verdict        AS last_verdict,
  d.net_saving_usd AS last_net_saving,
  d.created_at     AS last_analysed_at
FROM conversations c
LEFT JOIN safety_flags sf ON sf.conversation_id = c.id
LEFT JOIN LATERAL (
  SELECT verdict, net_saving_usd, created_at
  FROM decisions
  WHERE conversation_id = c.id
  ORDER BY created_at DESC
  LIMIT 1
) d ON true;

-- ============================================================
-- UPDATED_AT TRIGGER
-- Automatically updates the updated_at column on mutations.
-- ============================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at
  BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_conversations_updated_at
  BEFORE UPDATE ON conversations
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- END OF SCHEMA
-- ============================================================
