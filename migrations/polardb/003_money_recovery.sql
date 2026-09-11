
CREATE TABLE IF NOT EXISTS money_gateway_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1), data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS money_operations (
    operation_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, channel TEXT NOT NULL,
    kind TEXT NOT NULL, request_hash TEXT NOT NULL, status TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1, result TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_money_channel_status ON money_operations(channel,status);
CREATE TABLE IF NOT EXISTS money_ledger (
    ledger_id TEXT PRIMARY KEY, operation_id TEXT, reversal_of TEXT UNIQUE,
    case_id TEXT NOT NULL, order_id TEXT NOT NULL, currency TEXT NOT NULL,
    amount NUMERIC(18,2) NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS money_outbox (
    event_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS money_holds (
    channel TEXT NOT NULL, case_id TEXT NOT NULL, reason TEXT NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY(channel,case_id)
);

-- Upgrade the isolated pre-release schema as well as initialize new databases.
ALTER TABLE money_holds DROP CONSTRAINT IF EXISTS money_holds_pkey;
ALTER TABLE money_holds ADD PRIMARY KEY(channel,case_id);

-- Original monetary entries are immutable; compensation is a new linked entry.
CREATE OR REPLACE FUNCTION revguard_reject_ledger_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'money_ledger is append-only: % is forbidden', TG_OP USING ERRCODE = '55000';
END;
$$;
DROP TRIGGER IF EXISTS trg_money_ledger_append_only ON money_ledger;
CREATE TRIGGER trg_money_ledger_append_only
BEFORE UPDATE OR DELETE OR TRUNCATE ON money_ledger
FOR EACH STATEMENT EXECUTE FUNCTION revguard_reject_ledger_mutation();
