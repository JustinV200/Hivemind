-- Roadmap step 10.6a (docs/adr/0043, "Only the Queen isolates a Cell"): the Queen's own Guard
-- request table. One row per Guard report the Guard Bee filed through her door, written before the
-- door returns, so a request filed just before a restart is still decided after it. `body` is the
-- row's full GuardRequest.model_dump_json(), the source of truth every read decodes; the other
-- columns mirror the fields a read filters or orders by. Timestamps are datetime.isoformat() UTC
-- strings, which sort lexically the same as in time (ADR-0006).
CREATE TABLE IF NOT EXISTS guard_requests (
    id TEXT PRIMARY KEY,             -- the GuardReport's own guardrep_ id
    filed_at TEXT NOT NULL,          -- when the door wrote it: its age in the Queen's inbox
    decided_at TEXT,                 -- NULL while the request waits for her tick
    hold_cell_id TEXT,               -- the Cell a Hive Stand fallback held the goals off, if any
    hold_released_at TEXT,           -- set once the human's lift released that hold
    body TEXT NOT NULL
);

-- The Queen's drain: "every undecided request, oldest first", on every tick.
CREATE INDEX IF NOT EXISTS idx_guard_requests_pending ON guard_requests (decided_at, filed_at, id);

-- Placement's read and the lift's release: "every active hold", "every active hold on this Cell".
CREATE INDEX IF NOT EXISTS idx_guard_requests_holds ON guard_requests (hold_cell_id, hold_released_at);
