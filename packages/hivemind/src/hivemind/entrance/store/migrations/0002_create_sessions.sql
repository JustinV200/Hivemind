-- Roadmap step 10.5e: the tables login, sessions, step-up and the Entrance Reducer keep, in the
-- Entrance's own series beside 0001's operator, devices and invites (ADR-0033).
--
-- Sessions are plain columns rather than a JSON body (unlike 0001's rows): every authenticated
-- request moves last_seen_at and spends a nonce, and a guarded column update needs no decode.
-- Pending confirmations follow 0001's shape (filter columns plus the record's JSON body), since
-- they are written rarely and carry a JSON payload anyway. Timestamps are datetime.isoformat()
-- UTC strings, which sort lexically in time order (ADR-0006), so the purges below compare them in
-- SQL. Foreign keys are enforced (hivemind.common.sqlite.connect), so nothing here can name a
-- device the Entrance never enrolled.

-- One row per session a login opened (hivemind.entrance.auth.session.models.Session), keyed by
-- the SHA-256 of its bearer token: the token itself is never stored. The Hive Stand console's
-- sessions are never written here (ADR-0033: kept in memory only). An ended session keeps its row
-- as history; ended_at and end_reason are set together, once.
CREATE TABLE IF NOT EXISTS entrance_sessions (
    token_hash TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES entrance_devices (id),
    binding_kind TEXT NOT NULL CHECK (binding_kind IN ('ed25519', 'p256')),
    binding_key TEXT NOT NULL,
    listener TEXT NOT NULL CHECK (listener IN ('loopback', 'remote')),
    address TEXT NOT NULL,
    network TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    stepped_up_until TEXT,
    needs_step_up INTEGER NOT NULL DEFAULT 0 CHECK (needs_step_up IN (0, 1)),
    ended_at TEXT,
    end_reason TEXT,
    CHECK ((ended_at IS NULL) = (end_reason IS NULL))
);

-- "End every open session of this device" (offboarding) and "of this listener" (the Reducer).
CREATE INDEX IF NOT EXISTS idx_entrance_sessions_open_device
    ON entrance_sessions (device_id) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_entrance_sessions_open_listener
    ON entrance_sessions (listener) WHERE ended_at IS NULL;

-- Every nonce a signed request (or a socket's first frame) has spent, until twice the skew window
-- has passed; persisted so a restart does not reopen a replay window. Globally unique, so one
-- captured request cannot be replayed under another session bound to the same key.
CREATE TABLE IF NOT EXISTS entrance_nonces (
    nonce TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- The purge each claim runs first: "every nonce whose expiry has passed".
CREATE INDEX IF NOT EXISTS idx_entrance_nonces_expiry ON entrance_nonces (expires_at);

-- Consecutive valid-proof login failures per device (a wrong password after a good device proof);
-- lockout_attempts of them lock the device, and a success deletes the row.
CREATE TABLE IF NOT EXISTS entrance_login_failures (
    device_id TEXT PRIMARY KEY REFERENCES entrance_devices (id),
    consecutive INTEGER NOT NULL CHECK (consecutive >= 1),
    last_failed_at TEXT NOT NULL
);

-- Every network a device has been cleared on (the travel lock compares a login's network with
-- them): a canonical /24, /64 or derp:<region>, first and last seen.
CREATE TABLE IF NOT EXISTS entrance_device_networks (
    device_id TEXT NOT NULL REFERENCES entrance_devices (id),
    network TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (device_id, network)
);

-- One row per request held until a person confirms it
-- (hivemind.entrance.auth.confirm.models.PendingConfirmation); body is the source of truth,
-- status and the times are copies for filtering and ordering, written in the same statement.
CREATE TABLE IF NOT EXISTS entrance_pending (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES entrance_devices (id),
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'CONFIRMED', 'EXPIRED', 'CANCELLED')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    body TEXT NOT NULL
);

-- list_by_status(status) and its order, and the expiry sweep: "every PENDING one, oldest first".
CREATE INDEX IF NOT EXISTS idx_entrance_pending_status ON entrance_pending (status, created_at, id);

-- The Entrance mode (hivemind.entrance.reducer.EntranceMode): one row, ever, written by the first
-- change (no row reads as OPEN). The CHECK makes a second row impossible, not just unused.
CREATE TABLE IF NOT EXISTS entrance_mode (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL CHECK (mode IN ('OPEN', 'REDUCED')),
    changed_at TEXT NOT NULL
);
