CREATE TABLE policies (
    code text PRIMARY KEY,
    ai boolean NOT NULL,
    text text,
    kind text CHECK (kind IN ('authority', 'limit', 'regex')),
    params jsonb NOT NULL,
    action text NOT NULL CHECK (action IN ('block', 'edit')),
    applies_to text NOT NULL CHECK (applies_to IN ('prompt', 'response', 'both')),
    enabled boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((ai AND text IS NOT NULL AND kind IS NULL)
        OR (NOT ai AND text IS NULL AND kind IS NOT NULL)),
    CHECK (kind NOT IN ('authority', 'limit')
        OR (action = 'block' AND applies_to = 'prompt'))
);

-- One global counter, bumped in the same transaction as every policy change.
CREATE TABLE policy_version (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version integer NOT NULL CHECK (version >= 0)
);
INSERT INTO policy_version (version) VALUES (0);
