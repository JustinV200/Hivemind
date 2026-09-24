-- Roadmap steps 10.4 and 10.5d: the Entrance tables' first three (codingrules Appendix C, "Operator
-- credential, enrolled devices, sessions, push subscriptions": password hash and public keys only).
-- Later steps add their own numbers: 0002 sessions, 0003 push subscriptions, 0004 goals by device.
--
-- Rows follow the Brood Chamber's shape: the columns a query filters or orders by, plus `body`,
-- the record's full pydantic JSON, the source of truth every read decodes (`EnrolledDevice`,
-- `DeviceInvite`). Timestamps are datetime.isoformat() UTC strings, which sort lexically in time
-- order (ADR-0006). Every status change and every invite use runs inside one BEGIN IMMEDIATE
-- transaction that re-reads the row first (SqliteEntranceStore), so the state machine and the
-- single-use rule are applied to the row as it is, never to a stale copy.

-- One row, ever (Brood 1.0 has exactly one operator): the operator password's Argon2id PHC
-- string, never the password. The CHECK makes a second operator row impossible, not just unused.
CREATE TABLE IF NOT EXISTS entrance_operator (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

-- One row per enrolled device (hivemind.entrance.enrol.models.EnrolledDevice), whatever its status;
-- a device that leaves for good (DENIED, EXPIRED, REVOKED) keeps its row as history.
CREATE TABLE IF NOT EXISTS entrance_devices (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL
);

-- list_devices(status) and its order: "every PENDING device, oldest first".
CREATE INDEX IF NOT EXISTS idx_entrance_devices_status ON entrance_devices (status, created_at, id);

-- One row per invite (hivemind.entrance.enrol.models.DeviceInvite), keyed by the SHA-256 of its
-- code (the code itself is never stored). An invite admits exactly one device record, which must
-- exist first; UNIQUE on device_id makes a second invite for the same record impossible.
CREATE TABLE IF NOT EXISTS entrance_invites (
    code_hash TEXT PRIMARY KEY,
    device_id TEXT NOT NULL UNIQUE REFERENCES entrance_devices (id),
    body TEXT NOT NULL
);
