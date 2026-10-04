CREATE TABLE console_users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username text NOT NULL,
    password_hash text NOT NULL,
    role text NOT NULL CHECK (role IN ('viewer', 'analyst', 'admin')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX console_users_username_lower_key ON console_users (lower(username));

-- Only a SHA-256 of each session token is stored.
CREATE TABLE console_sessions (
    token_hash bytea PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES console_users (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX console_sessions_user_id_idx ON console_sessions (user_id);
