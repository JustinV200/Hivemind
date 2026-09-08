-- Create the Pheromone Trail's one table: an append-only log of PheromoneEvent rows.
--
-- Columns mirror PheromoneEvent's own fields (id, hive_id, node_id, at, kind, subject_id, actor)
-- plus `family` (the kind's prefix, indexed on its own for family-wide reads) and `body`, the
-- event's full model_dump_json() text -- the source of truth a reader decodes back through
-- parse_event_json. `at` is stored as datetime.isoformat()'s fixed-offset UTC string, which sorts
-- lexically the same as chronologically (ADR-0006). hivemind.pheromone.sqlite issues only INSERT
-- and INSERT OR IGNORE statements against this table: it never rewrites or removes a row once
-- written. The one place in hivemind.pheromone that ever removes a row is hivemind.pheromone.
-- retention, the Night Veil boundary (ADR-0007, codingrules section 12), and only at teardown.
CREATE TABLE IF NOT EXISTS pheromone_events (
    id TEXT PRIMARY KEY,
    hive_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    at TEXT NOT NULL,
    family TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    body TEXT NOT NULL
);

-- Trail order (at, node_id, id): every query() and export_segment() call orders by this exact
-- triple (hivemind.pheromone.trail.TRAIL_ORDER_KEY), so it is also the index that carries them.
CREATE INDEX IF NOT EXISTS idx_pheromone_events_trail_order ON pheromone_events (at, node_id, id);

-- Segment export: one node's events, bounded below by `at` (export_segment's `since`).
CREATE INDEX IF NOT EXISTS idx_pheromone_events_node_at ON pheromone_events (node_id, at);

-- TrailQuery.subject_id: "everything recorded about this Task/Cell/Tool/...".
CREATE INDEX IF NOT EXISTS idx_pheromone_events_subject_id ON pheromone_events (subject_id);

-- TrailQuery.kind: "every event of exactly this kind".
CREATE INDEX IF NOT EXISTS idx_pheromone_events_kind ON pheromone_events (kind);
