-- Create the Honey Store's five tables of record, its external-content FTS5 index and its sync
-- triggers (ADR-0035). Every clearance column carries a companion `clearance_rank` integer
-- (HoneyClearance.rank: C0=0, C1=1, C2=2) so every clearance filter is a plain numeric
-- `clearance_rank <= ?` rather than a string comparison or a CASE expression. Timestamps are
-- stored as datetime.isoformat()'s fixed-offset UTC strings (ADR-0006), matching every other
-- Hive table.

-- honey_nectar: one row per deposit. Two dedupe keys, checked in this order by add_nectar:
-- `source_key` (an internal origin's own key, e.g. "handoff:<event id>") when the caller has one,
-- else `sha256` (the content's own digest). `ephemeral_cell_id` is set only for a Night Veil
-- Cell's own side-channel rows (ADR-0035): never ripened, purged by purge_ephemeral at teardown.
CREATE TABLE IF NOT EXISTS honey_nectar (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    media_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content BLOB NOT NULL,
    size_bytes INTEGER NOT NULL,
    task_id TEXT,
    cell_id TEXT NOT NULL,
    bee TEXT,
    observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    clearance TEXT NOT NULL,
    clearance_rank INTEGER NOT NULL,
    origin_tier TEXT NOT NULL,
    scope TEXT NOT NULL,
    state TEXT NOT NULL,
    ripen_attempts INTEGER NOT NULL DEFAULT 0,
    tainted INTEGER NOT NULL DEFAULT 0,
    source_key TEXT,
    event_id TEXT,
    ephemeral_cell_id TEXT
);

-- pending_nectar's own scan: RECEIVED rows, oldest first.
CREATE INDEX IF NOT EXISTS idx_honey_nectar_state_received_at
    ON honey_nectar (state, received_at);
-- add_nectar's sha256 dedupe lookup (not unique: the store trusts its own asyncio.Lock-guarded
-- select-then-insert, not a constraint, to keep the two dedupe paths symmetric with source_key).
CREATE INDEX IF NOT EXISTS idx_honey_nectar_sha256 ON honey_nectar (sha256);
-- add_nectar's source_key dedupe lookup and has_source's own check; unique and nullable, so many
-- rows may share NULL (no source_key) while any real value can appear at most once.
CREATE UNIQUE INDEX IF NOT EXISTS idx_honey_nectar_source_key
    ON honey_nectar (source_key) WHERE source_key IS NOT NULL;
-- purge_ephemeral's own scan: a Night Veil Cell's side-channel rows, by Cell id.
CREATE INDEX IF NOT EXISTS idx_honey_nectar_ephemeral_cell
    ON honey_nectar (ephemeral_cell_id) WHERE ephemeral_cell_id IS NOT NULL;

-- honey: the ripened rows. `honey_seq` is a plain rowid alias (SQLite's INTEGER PRIMARY KEY): the
-- public id stays `id` (a HoneyId) everywhere above the store; `honey_seq` exists only so
-- honey_vectors and the FTS5 index below can key a fast integer join/foreign key instead of a
-- text one. Idempotent on (nectar_id, part, chunk_index): ripen() relies on this UNIQUE constraint
-- to detect "already ripened" without a separate existence check racing its own insert.
CREATE TABLE IF NOT EXISTS honey (
    honey_seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    nectar_id TEXT NOT NULL REFERENCES honey_nectar (id),
    part TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    body TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    clearance TEXT NOT NULL,
    clearance_rank INTEGER NOT NULL,
    ripener_model TEXT,
    kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    scope TEXT NOT NULL,
    origin_tier TEXT NOT NULL,
    task_id TEXT,
    cell_id TEXT NOT NULL,
    bee TEXT,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    tainted INTEGER NOT NULL DEFAULT 0,
    retired_at TEXT,
    embedding_model TEXT,
    UNIQUE (nectar_id, part, chunk_index)
);

-- honey_for_nectar's own scan.
CREATE INDEX IF NOT EXISTS idx_honey_nectar_id ON honey (nectar_id);
-- Every ReadFilter's scope GLOB clause scans this.
CREATE INDEX IF NOT EXISTS idx_honey_scope ON honey (scope);
-- Every ReadFilter's remaining three clauses (tainted, retired_at, clearance_rank), together: the
-- order search_text/search_vectors/list_honey/count_withheld all filter by before ranking.
CREATE INDEX IF NOT EXISTS idx_honey_live ON honey (tainted, retired_at, clearance_rank);

-- honey_vectors: one row per (Honey row, embedding model). Two models may coexist for the same
-- honey_seq while a re-embed is in flight (ADR-0036); ON DELETE CASCADE keeps a retired or
-- (future) purged Honey row from leaving orphaned vectors behind.
CREATE TABLE IF NOT EXISTS honey_vectors (
    honey_seq INTEGER NOT NULL REFERENCES honey (honey_seq) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dims INTEGER NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (honey_seq, model)
);

-- search_vectors' own model isolation filter, and pending_vectors' anti-join.
CREATE INDEX IF NOT EXISTS idx_honey_vectors_model ON honey_vectors (model);

-- honey_watermarks: a small name -> value table for whatever cursor a later dispatch's pipeline
-- needs to remember between passes (e.g. "how far the last Bee Bread sweep got").
CREATE TABLE IF NOT EXISTS honey_watermarks (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- honey_proposals: a human's "propose a note" queue (roadmap 7.10), drained by the House Bee into
-- ordinary Nectar; `nectar_id` and `drained_at` stay NULL until mark_proposal_drained runs.
CREATE TABLE IF NOT EXISTS honey_proposals (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    drained_at TEXT,
    nectar_id TEXT
);

-- pending_proposals' own scan: not yet drained, oldest first.
CREATE INDEX IF NOT EXISTS idx_honey_proposals_pending
    ON honey_proposals (created_at) WHERE drained_at IS NULL;

-- honey_fts: an external-content FTS5 index over honey's own title/summary/body columns, so the
-- indexed text is never duplicated on disk (ADR-0035). `content_rowid='honey_seq'` ties every FTS
-- row to its honey row by the same integer key honey_vectors uses; 'porter unicode61
-- remove_diacritics 2' stems English words and folds accents, so "running" and "run", or
-- "café" and "cafe", match the same query terms.
CREATE VIRTUAL TABLE IF NOT EXISTS honey_fts USING fts5(
    title, summary, body,
    content='honey',
    content_rowid='honey_seq',
    tokenize = 'porter unicode61 remove_diacritics 2'
);

-- Three triggers keep honey_fts in sync with honey (the standard external-content pattern:
-- SQLite's own documentation for fts5, section 4.3.1). A plain INSERT indexes a new row; a
-- 'delete' command (fts5's own special first-column value) removes an old row's terms before an
-- UPDATE re-indexes it, or permanently on a DELETE. The UPDATE trigger fires only when indexed
-- text changes: a relabel, a taint, a retirement or a re-embed's model stamp touches none of it,
-- and re-indexing on each would make a whole-store re-embed rewrite the whole index too.
CREATE TRIGGER IF NOT EXISTS honey_fts_ai AFTER INSERT ON honey BEGIN
    INSERT INTO honey_fts (rowid, title, summary, body)
    VALUES (new.honey_seq, new.title, new.summary, new.body);
END;

CREATE TRIGGER IF NOT EXISTS honey_fts_ad AFTER DELETE ON honey BEGIN
    INSERT INTO honey_fts (honey_fts, rowid, title, summary, body)
    VALUES ('delete', old.honey_seq, old.title, old.summary, old.body);
END;

CREATE TRIGGER IF NOT EXISTS honey_fts_au AFTER UPDATE OF title, summary, body ON honey BEGIN
    INSERT INTO honey_fts (honey_fts, rowid, title, summary, body)
    VALUES ('delete', old.honey_seq, old.title, old.summary, old.body);
    INSERT INTO honey_fts (rowid, title, summary, body)
    VALUES (new.honey_seq, new.title, new.summary, new.body);
END;
