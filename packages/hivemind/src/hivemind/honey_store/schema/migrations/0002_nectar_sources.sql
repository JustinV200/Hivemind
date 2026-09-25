-- Add honey_nectar_sources: one row per additional source whose deposit deduplicated onto an
-- existing honey_nectar row by content, never by source_key (ADR-0037). A row records who else
-- sent the same content: its own dedupe key, task, Cell, bee, observed and received times, origin,
-- origin tier and the label it declared -- never the content itself. The same internal source
-- delivered twice, or a duplicate whose provenance already matches a stored source, adds nothing
-- (the two unique indexes below), so this table grows with distinct sources, never with retries.
--
-- ON DELETE CASCADE mirrors honey_vectors' own use of the mechanism (0001): the store's connection
-- always enables foreign keys (hivemind.common.sqlite.connect's own PRAGMA), so a Night Veil
-- Cell's teardown purge -- which deletes only its own EPHEMERAL honey_nectar rows -- takes any
-- source rows recorded against them along with it, the same way it already takes their vectors.
CREATE TABLE IF NOT EXISTS honey_nectar_sources (
    id INTEGER PRIMARY KEY,
    nectar_id TEXT NOT NULL REFERENCES honey_nectar (id) ON DELETE CASCADE,
    source_key TEXT,
    task_id TEXT,
    cell_id TEXT NOT NULL,
    bee TEXT,
    observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    origin TEXT NOT NULL,
    origin_tier TEXT NOT NULL,
    clearance TEXT NOT NULL,
    event_id TEXT
);

-- has_source's second table: a source_key is recorded here at most once store-wide, exactly like
-- honey_nectar's own idx_honey_nectar_source_key (0001).
CREATE UNIQUE INDEX IF NOT EXISTS idx_honey_nectar_sources_source_key
    ON honey_nectar_sources (source_key) WHERE source_key IS NOT NULL;

-- The uniqueness rule ADR-0037 asks for: the same provenance (nectar, source_key, task, Cell,
-- bee) is recorded once. COALESCE folds the three nullable columns to '' so SQLite's ordinary
-- multi-column UNIQUE index -- which never treats two NULLs as equal -- still compares "no
-- task"/"no bee"/"no source_key" consistently across rows, instead of letting every NULL
-- combination be its own, always-distinct case.
CREATE UNIQUE INDEX IF NOT EXISTS idx_honey_nectar_sources_provenance ON honey_nectar_sources (
    nectar_id, COALESCE(source_key, ''), COALESCE(task_id, ''), cell_id, COALESCE(bee, '')
);

-- nectar_sources' own scan: every extra source recorded for one Nectar row.
CREATE INDEX IF NOT EXISTS idx_honey_nectar_sources_nectar ON honey_nectar_sources (nectar_id);
