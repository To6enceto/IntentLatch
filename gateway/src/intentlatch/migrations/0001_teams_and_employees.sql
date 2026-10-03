CREATE TABLE teams (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    authorized_models text[] NOT NULL CHECK (cardinality(authorized_models) > 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX teams_name_lower_key ON teams (lower(name));

CREATE TABLE employees (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id uuid NOT NULL REFERENCES teams (id),
    name text NOT NULL,
    token_id uuid NOT NULL,
    token_issued_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX employees_team_id_idx ON employees (team_id);
