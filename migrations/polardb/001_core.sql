-- RevGuard production schema for PolarDB for PostgreSQL / PostgreSQL 14+.
-- Monetary columns are NUMERIC(18,2); JSONB remains the lossless domain payload.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    status TEXT NOT NULL,
    claim_actual_amount NUMERIC(18,2),
    claim_expected_amount NUMERIC(18,2),
    currency TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_updated_cursor
    ON cases(updated_at DESC, case_id DESC);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    type TEXT NOT NULL,
    data JSONB NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_case
    ON evidence(case_id, collected_at, evidence_id);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approvals_case
    ON approvals(case_id, created_at DESC, approval_id DESC);

CREATE TABLE IF NOT EXISTS executions (
    action_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    idempotency_key TEXT UNIQUE,
    amount NUMERIC(18,2),
    currency TEXT,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_executions_case
    ON executions(case_id, created_at, action_id);

CREATE TABLE IF NOT EXISTS verifications (
    case_id TEXT PRIMARY KEY REFERENCES cases(case_id),
    expected_amount NUMERIC(18,2),
    actual_amount NUMERIC(18,2),
    variance NUMERIC(18,2),
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    skill_name TEXT NOT NULL,
    assigned_actor TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_case
    ON agent_tasks(case_id, updated_at, task_id);

CREATE TABLE IF NOT EXISTS agent_task_results (
    result_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id),
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(task_id, attempt)
);
CREATE INDEX IF NOT EXISTS idx_agent_task_results_task
    ON agent_task_results(task_id, attempt);

CREATE TABLE IF NOT EXISTS audit_events (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    detail JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    previous_hash TEXT NOT NULL,
    row_digest TEXT NOT NULL,
    row_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_audit_case
    ON audit_events(case_id, seq);

CREATE OR REPLACE FUNCTION revguard_audit_chain_before_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    prior_hash TEXT;
    canonical_row TEXT;
BEGIN
    -- Serialize one hash chain per case without locking unrelated cases.
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.case_id, 0));
    SELECT row_hash INTO prior_hash
      FROM audit_events
     WHERE case_id = NEW.case_id
     ORDER BY seq DESC
     LIMIT 1;
    NEW.previous_hash := COALESCE(prior_hash, 'GENESIS');
    canonical_row := jsonb_build_object(
        'case_id', NEW.case_id,
        'actor', NEW.actor,
        'event', NEW.event,
        'detail', NEW.detail,
        'created_at', NEW.created_at
    )::TEXT;
    NEW.row_digest := encode(digest(canonical_row, 'sha256'), 'hex');
    NEW.row_hash := encode(
        digest(NEW.previous_hash || ':' || NEW.row_digest, 'sha256'), 'hex'
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_revguard_audit_chain ON audit_events;
CREATE TRIGGER trg_revguard_audit_chain
BEFORE INSERT ON audit_events
FOR EACH ROW EXECUTE FUNCTION revguard_audit_chain_before_insert();

CREATE OR REPLACE FUNCTION revguard_reject_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only: % is forbidden', TG_OP
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_revguard_audit_append_only ON audit_events;
CREATE TRIGGER trg_revguard_audit_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
FOR EACH STATEMENT EXECUTE FUNCTION revguard_reject_audit_mutation();

CREATE TABLE IF NOT EXISTS trace_spans (
    span_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    parent_span_id TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    actor TEXT,
    status TEXT NOT NULL,
    sequence BIGINT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    duration_ms BIGINT,
    inputs JSONB,
    outputs JSONB,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_spans_case
    ON trace_spans(case_id, sequence, started_at);

