-- Night Veil checkpoints (hivemind.pheromone.retention.checkpoint): one row per living Night Veil
-- Cell, holding its per-tier Capping counts, the moment of the newest counted Capping event and
-- the ids filed under it, so a restarted Queen can still summarise and purge it. Numbers and ids
-- only (codingrules section 12); the purge deletes the row when it takes the Cell.
CREATE TABLE IF NOT EXISTS night_veil_checkpoints (
    cell_id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);
