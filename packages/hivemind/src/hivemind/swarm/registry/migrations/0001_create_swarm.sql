-- Roadmap step 11.2: the Swarm registry's two tables, in the Hive's one database (ADR-0006), its
-- own migration series "swarm".
--
-- swarm_invites: one row per device invite the operator minted (`hive swarm invite`). The key is
-- the SHA-256 of the token exactly as it travels; the token itself is never stored, and neither
-- is the invite string (docs/waggle/enrolment.md section 3.2). `state` and `expires_at` are
-- copies of what `body` (the SwarmInvite as JSON, the source of truth) says, kept in plain
-- columns so a sweep can find lapsed invites without decoding every row.
CREATE TABLE IF NOT EXISTS swarm_invites (
    code_hash TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    device_id TEXT,
    body TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_swarm_invites_state ON swarm_invites (state, expires_at);

-- swarm_nodes: one row per enrolled device, never deleted (a revoked node stays for the audit).
-- The unique columns are the identities a device cannot share with another: its node id, the
-- Real Cell and the Warden the Queen minted for it, and its public key. `body` (the SwarmNode as
-- JSON) is the source of truth; `status` and `level` are copies kept for filtering and for the
-- Appendix C machines' columns, written in the same statement as the body.
CREATE TABLE IF NOT EXISTS swarm_nodes (
    device_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL UNIQUE,
    cell_id TEXT NOT NULL UNIQUE,
    warden_id TEXT NOT NULL UNIQUE,
    public_key_hex TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    level TEXT NOT NULL,
    enrolled_at TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_swarm_nodes_status ON swarm_nodes (status, enrolled_at);
