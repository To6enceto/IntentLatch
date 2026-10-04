-- Cases are edited in place; runs and their per-case results are the history.
CREATE TABLE test_cases (
    code text PRIMARY KEY,
    name text NOT NULL,
    prompt text NOT NULL,
    model text NOT NULL,
    run_as_employee_id uuid NOT NULL REFERENCES employees (id),
    expected text NOT NULL CHECK (expected IN ('ALLOW', 'EDIT', 'BLOCK')),
    expected_policy_code text,
    must_not_contain text[] NOT NULL DEFAULT '{}',
    predefined boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expected <> 'ALLOW' OR expected_policy_code IS NULL),
    CHECK (expected = 'EDIT' OR cardinality(must_not_contain) = 0)
);

CREATE TABLE test_runs (
    id uuid PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    started_by text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('check', 'full')),
    policy_version integer NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'passed', 'failed')),
    totals jsonb NOT NULL
);
CREATE INDEX test_runs_started_at_idx ON test_runs (started_at);

CREATE TABLE test_case_results (
    run_id uuid NOT NULL REFERENCES test_runs (id),
    case_code text NOT NULL,
    expected text NOT NULL CHECK (expected IN ('ALLOW', 'EDIT', 'BLOCK')),
    actual text NOT NULL CHECK (actual IN ('ALLOW', 'EDIT', 'BLOCK', 'ERROR')),
    fired_policy_codes text[] NOT NULL,
    passed boolean NOT NULL,
    failure text,
    policy_version integer,
    duration_ms double precision NOT NULL CHECK (duration_ms >= 0),
    PRIMARY KEY (run_id, case_code)
);
