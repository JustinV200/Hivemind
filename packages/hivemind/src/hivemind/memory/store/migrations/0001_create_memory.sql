-- Create the four memory tables: pins, notes, handoffs and episodes.
--
-- Every table carries a `clearance` column (codingrules section 8.9: "every tier carries a
-- clearance") so list_pins/list_notes/list_episodes can filter by a reader's allowance with a
-- plain indexed `IN (...)` over HoneyClearance's three wire values, without decoding `body` (the
-- row's full model_dump_json() text, the source of truth every read decodes back through
-- Pin/Note/Handoff/EpisodeRecord.model_validate_json). Timestamps are stored as
-- datetime.isoformat()'s fixed-offset UTC strings (ADR-0006), matching every other Hive table.
CREATE TABLE IF NOT EXISTS memory_pins (
    id TEXT PRIMARY KEY,
    clearance TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL
);

-- list_pins' own order, and the clearance filter both together.
CREATE INDEX IF NOT EXISTS idx_memory_pins_clearance_created_at
    ON memory_pins (clearance, created_at);

-- memory_notes: the only table a bee writes to directly (roadmap step 3.14). `author` plus
-- `written_at` is the lookup path add_note's own per-author eviction (this module's third
-- DELETE, alongside remove_pin and purge_episodes_before) scans on the same index list_notes uses.
CREATE TABLE IF NOT EXISTS memory_notes (
    id TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    clearance TEXT NOT NULL,
    written_at TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_notes_author_written_at
    ON memory_notes (author, written_at);

CREATE INDEX IF NOT EXISTS idx_memory_notes_clearance ON memory_notes (clearance);

-- memory_handoffs: keyed by the memory.checkpoint trail event's own id (HandoffRef.event_id),
-- never a separate Handoff id kind (none exists). `task_id` is nullable: a Handoff need not
-- concern any one task.
CREATE TABLE IF NOT EXISTS memory_handoffs (
    event_id TEXT PRIMARY KEY,
    task_id TEXT,
    clearance TEXT NOT NULL,
    written_at TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_handoffs_task_id ON memory_handoffs (task_id);

-- memory_episodes: one row per awake episode or autopilot decision (roadmap step 3.14), retained
-- on its own window and purged by purge_episodes_before, never on the trail (codingrules section
-- 12: "Thoughts are memory, not audit").
CREATE TABLE IF NOT EXISTS memory_episodes (
    id TEXT PRIMARY KEY,
    principal TEXT NOT NULL,
    at TEXT NOT NULL,
    clearance TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_episodes_principal_at ON memory_episodes (principal, at);

-- purge_episodes_before's own scan: every episode older than a cutoff, regardless of principal.
CREATE INDEX IF NOT EXISTS idx_memory_episodes_at ON memory_episodes (at);
