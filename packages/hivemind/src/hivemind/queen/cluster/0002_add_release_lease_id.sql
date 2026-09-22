-- Roadmap step 5.13: `hive cells release <lease-id>` reuses the CLUSTER/WAKE order table (the
-- roadmap step's own "choose the smaller change") rather than a sibling table. RELEASE orders
-- name a lease instead of a provider; `provider` and `lease_id` are never both set on one row
-- (orders.py's own module docstring).

ALTER TABLE cluster_orders ADD COLUMN lease_id TEXT;
