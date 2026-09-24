-- Create the flight recorder's two tables (roadmap step 6.6, ADR-0032: "two tables in the Hive's
-- SQLite file"). Until phase 7's Nectar intake exists, these rows are a recording's Nectar body; a
-- Bee Bread RECORDING entry references each recording by id.
--
-- exoskeleton_recordings: one row per recording (one Exoskeleton attach). Indexed columns serve
-- the lookups `hive recordings list` and the retention prune make; `body` is the header's full
-- RecordingInfo JSON, the source of truth every read decodes (the memory tables' own shape).
-- Timestamps are fixed-width UTC ISO text, so they compare and sort lexically the same as in time.
CREATE TABLE IF NOT EXISTS exoskeleton_recordings (
    id TEXT PRIMARY KEY,
    cell_id TEXT NOT NULL,
    task_id TEXT,
    clearance TEXT NOT NULL,
    started_at TEXT NOT NULL,
    body TEXT NOT NULL
);

-- `recordings(cell_id=...)` and the Night Veil purge: one Cell's recordings, newest first.
CREATE INDEX IF NOT EXISTS idx_exoskeleton_recordings_cell
    ON exoskeleton_recordings (cell_id, started_at);

-- `recordings()` across every Cell, and the retention prune's `started_at < cutoff` scan.
CREATE INDEX IF NOT EXISTS idx_exoskeleton_recordings_started
    ON exoskeleton_recordings (started_at);

-- exoskeleton_recording_actions: one row per recorded GUI proposal, `seq` counting from 1 within
-- its recording in the order the recorder added them. `body` is the RecordedAction's JSON with
-- both frames taken out (a Frame refuses JSON on purpose, so pixels can never leak into text);
-- each frame's PNG bytes sit in a BLOB column beside it, with the capture time Frame.from_png
-- needs to rebuild the identical Frame. Columns rather than a frames table: an action has at most
-- two frames and each belongs to exactly one side of exactly one action, so the whole action is
-- one row (one read, one write, no join), the ADR keeps its two tables, and a purge or prune that
-- removes the row removes its pixels with it (no orphaned frame is possible).
CREATE TABLE IF NOT EXISTS exoskeleton_recording_actions (
    recording_id TEXT NOT NULL REFERENCES exoskeleton_recordings (id),
    seq INTEGER NOT NULL,
    finished_at TEXT NOT NULL,
    body TEXT NOT NULL,
    before_png BLOB,
    before_captured_at TEXT,
    after_png BLOB,
    after_captured_at TEXT,
    PRIMARY KEY (recording_id, seq),
    -- A frame's bytes and its capture time travel together: one without the other is a bug.
    CHECK ((before_png IS NULL) = (before_captured_at IS NULL)),
    CHECK ((after_png IS NULL) = (after_captured_at IS NULL))
);
