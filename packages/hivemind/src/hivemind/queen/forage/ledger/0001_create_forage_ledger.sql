-- Roadmap step 4.7: the Forage ledger's durable state (Appendix C, "Capacity, grants, hosting
-- decisions, snapshots | Forage ledger (SQLite) | Yes"). Every table holds one JSON body per row
-- (mirroring hivemind.memory.store's own memory_pins/memory_notes shape), keyed by whichever id
-- names the row, so a fresh field on the pydantic model above it never needs a schema migration.

-- Every Cell's latest reported ForageCapacity, keyed by cell_id; a fresh report replaces the row.
CREATE TABLE forage_ledger_capacities (
    cell_id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);

-- Every Warden's own local-pool usage report (reported, never granted -- codingrules 8.10), keyed
-- by warden_id; a fresh report replaces the row.
CREATE TABLE forage_ledger_local_reports (
    warden_id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);

-- Every live shared ForageGrant, keyed by grant_id; a fresh revision replaces the row, and a
-- revoked or expired grant is deleted from it entirely (freeing headroom).
CREATE TABLE forage_ledger_grants (
    grant_id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);

-- The Royal Reserve subtracted before any grant; a single row (id is always 1), replaced whenever
-- the Queen's own manifest-derived reserve changes.
CREATE TABLE forage_ledger_reserve (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    body TEXT NOT NULL
);
