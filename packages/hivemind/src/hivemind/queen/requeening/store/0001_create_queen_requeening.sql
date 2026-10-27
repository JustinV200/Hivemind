-- Roadmap 13.1 and 13.2 (ADR-0062): what a restarted Queen needs that no other store keeps.

-- Her own periodic Handoff: one row, the newest, its body the QueenSnapshot as JSON.
CREATE TABLE queen_snapshot (
    slot INTEGER PRIMARY KEY CHECK (slot = 1),  -- Only ever row 1: put_snapshot replaces it.
    taken_at TEXT NOT NULL,
    body TEXT NOT NULL
);

-- The Alarms and questions she handled from Wardens, so a resend after a restart is dropped.
CREATE TABLE queen_handled (
    principal TEXT NOT NULL,         -- The Warden whose link it arrived on.
    envelope_id TEXT NOT NULL,       -- A resend keeps the envelope's id.
    handled_us INTEGER NOT NULL,     -- When she handled it, microseconds since the epoch (UTC).
    body TEXT NOT NULL,              -- The HandledEnvelope as JSON, a question's wiring included.
    has_question INTEGER NOT NULL,   -- 1 when body carries wiring; a plain note never replaces it.
    PRIMARY KEY (principal, envelope_id)
);

CREATE INDEX idx_queen_handled_at ON queen_handled (handled_us);

-- The port each of her listeners bound last, for one the manifest lets the OS choose.
CREATE TABLE queen_listener_ports (
    name TEXT PRIMARY KEY,
    port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535)
);
