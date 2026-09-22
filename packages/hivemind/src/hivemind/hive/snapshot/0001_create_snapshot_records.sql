-- Roadmap step 5.10's own follow-up gap: the durable book of every live Cell snapshot, so
-- `hive cells rollback` in a separate CLI process can see what an earlier `hive cells snapshot`
-- recorded. One row per SnapshotRecord (see hivemind.hive.snapshot.ledger.SnapshotRecord).

CREATE TABLE snapshot_records (
    id TEXT PRIMARY KEY,
    cell_id TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    bytes_estimate INTEGER NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX idx_snapshot_records_cell_id ON snapshot_records (cell_id);
CREATE INDEX idx_snapshot_records_expires_at ON snapshot_records (expires_at);
