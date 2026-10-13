-- Roadmap step 9.2a (ADR-0048): the Basket's three tables. The blob BYTES live in a directory
-- (`<root>/<sha256[:2]>/<sha256>`, hivemind.basket.store.files); these tables hold what describes
-- them, who keeps a copy and what is queued to ship.

-- One row per blob, keyed by the content hash: depositing the same bytes twice is one row. The
-- manifest's signature columns sit on the row (a manifest is the blob plus a signer and a
-- signature), so one SELECT rebuilds both. `clearance_rank` repeats the label's rank so a reader
-- can filter by it without parsing the name (codingrules 9: compare rank, never the value).
-- `state` is the only column `retire` changes; `retired_reason` is for the human and is never
-- sent to the trail as text.
CREATE TABLE IF NOT EXISTS basket_blobs (
    sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 1),
    media_kind TEXT NOT NULL,
    media_type TEXT NOT NULL,
    platform_tags TEXT NOT NULL,
    clearance TEXT NOT NULL,
    clearance_rank INTEGER NOT NULL,
    origin_cell_id TEXT NOT NULL,
    origin_task_id TEXT,
    origin_bee TEXT,
    deposited_at TEXT NOT NULL,
    deposit_event_id TEXT,
    tainted INTEGER NOT NULL CHECK (tainted IN (0, 1)),
    state TEXT NOT NULL CHECK (state IN ('PRESENT', 'RETIRED')),
    retired_at TEXT,
    retired_reason TEXT,
    manifest_signer TEXT NOT NULL,
    manifest_signature TEXT NOT NULL
);

-- `hive basket list` filters by kind, state and origin Cell.
CREATE INDEX IF NOT EXISTS idx_basket_blobs_kind_state ON basket_blobs (media_kind, state);
CREATE INDEX IF NOT EXISTS idx_basket_blobs_origin ON basket_blobs (origin_cell_id);

-- Which blobs a Cell keeps resident, with the two times eviction orders by. A blob row can never
-- be deleted while a Cell holds it (the foreign key), which retire respects by never deleting.
CREATE TABLE IF NOT EXISTS basket_residency (
    cell_id TEXT NOT NULL,
    sha256 TEXT NOT NULL REFERENCES basket_blobs (sha256),
    resident_since TEXT NOT NULL,
    last_used TEXT NOT NULL,
    PRIMARY KEY (cell_id, sha256)
);

-- The shipment queue: a request to get a blob to a Cell, pending until completed_at is set.
CREATE TABLE IF NOT EXISTS basket_shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL REFERENCES basket_blobs (sha256),
    destination_cell_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    completed_at TEXT
);

-- `pending_shipments` reads only the rows not yet completed.
CREATE INDEX IF NOT EXISTS idx_basket_shipments_pending ON basket_shipments (completed_at);
