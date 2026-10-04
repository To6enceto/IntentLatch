-- Append-only and hash-chained: seq orders the chain, and the triggers refuse any rewrite.
CREATE TABLE decision_records (
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    id uuid PRIMARY KEY,
    request_id uuid NOT NULL,
    ts timestamptz NOT NULL,
    employee_id uuid NOT NULL,
    team text NOT NULL,
    model text NOT NULL,
    direction text NOT NULL CHECK (direction IN ('prompt', 'response')),
    outcome text NOT NULL CHECK (outcome IN ('allowed', 'edited', 'blocked')),
    control_agent_status text NOT NULL
        CHECK (control_agent_status IN ('skipped', 'pass', 'blocked', 'modified', 'error')),
    policy_results jsonb NOT NULL,
    source_masked text NOT NULL,
    rewritten_masked text,
    tokens_in integer NOT NULL CHECK (tokens_in >= 0),
    tokens_out integer NOT NULL CHECK (tokens_out >= 0),
    compute_seconds numeric(12, 3) NOT NULL CHECK (compute_seconds >= 0),
    stage_latency_ms jsonb NOT NULL,
    policy_version integer NOT NULL,
    feed_version text,
    prev_hash text NOT NULL,
    hash text NOT NULL UNIQUE,
    test_run_id uuid
);
CREATE INDEX decision_records_ts_idx ON decision_records (ts);

CREATE FUNCTION decision_records_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'decision_records is append-only';
END;
$$;
CREATE TRIGGER decision_records_no_rewrite BEFORE UPDATE OR DELETE ON decision_records
    FOR EACH ROW EXECUTE FUNCTION decision_records_append_only();
CREATE TRIGGER decision_records_no_truncate BEFORE TRUNCATE ON decision_records
    FOR EACH STATEMENT EXECUTE FUNCTION decision_records_append_only();
