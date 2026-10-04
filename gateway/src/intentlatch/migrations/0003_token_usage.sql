-- Append-only: one row per answered chat request, counted against the caller's team.
CREATE TABLE token_usage (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_id uuid NOT NULL REFERENCES teams (id),
    tokens bigint NOT NULL CHECK (tokens >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX token_usage_team_id_created_at_idx ON token_usage (team_id, created_at);
