-- Roadmap step 4.9: the durable table `hive cluster`/`hive wake` write into and the running
-- Queen's own tick polls (docs/adr/0024-clustering-protocol.md). One row per operator order.

CREATE TABLE cluster_orders (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- 'CLUSTER' or 'WAKE'
    provider TEXT,                   -- NULL means "every provider" (see orders.py's OrderKind).
    requested_at TEXT NOT NULL,
    handled_at TEXT                  -- NULL while still pending; set once run_cluster_tick acts.
);

CREATE INDEX idx_cluster_orders_pending ON cluster_orders (handled_at, requested_at);
