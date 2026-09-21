-- Roadmap step 5.0a: the Leavings ledger's one table -- every path a task was allowed to keep on
-- a Cell past its lease's release, and who allowed it. `prior` is the bytes the path held before
-- the write that produced this row (or NULL, meaning "did not exist before"), kept so `hive cells
-- leavings remove` can replay it; `hivemind.cell.leavings.model.Leaving`'s own docstring explains
-- why that field exists beyond the roadmap step's own listed fields.
--
-- Primary key (cell_id, path): at most one *active* (removed_at IS NULL) row per path per Cell.
-- SqliteLeavingsStore.record_leaving upserts (INSERT OR REPLACE) on this key rather than
-- rejecting a second write: an active row is replaced field for field except `prior`, which the
-- existing row's own value always wins, so `hive cells leavings remove` still replays what the
-- path held before any Leaving ever existed there, however many times it is left again before
-- being removed (coordinator review, roadmap step 5.0a bug fix -- the ordinary "same goal run
-- twice" case). A removed row is never reopened (Leaving.removed_at's own docstring), so a fresh
-- Leaving at a previously-removed path is a brand-new row with its own `prior`, and REPLACE loses
-- that removed row's own history outright; this schema accepts that for v0 -- the ledger's job is
-- "what is left now", not a full audit history of every path ever left.
CREATE TABLE IF NOT EXISTS cell_leavings (
    cell_id TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    task_id TEXT,
    lease_id TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    prior BLOB,
    left_at TEXT NOT NULL,
    removed_at TEXT,
    PRIMARY KEY (cell_id, path)
);

-- `hive cells leavings list <cell>`'s own two shapes: every row for a Cell, and (the default)
-- only its still-active ones.
CREATE INDEX IF NOT EXISTS idx_cell_leavings_cell_removed ON cell_leavings (cell_id, removed_at);
