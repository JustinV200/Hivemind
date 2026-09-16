-- Create memory_cell_wax: Cell Wax, Queen-written cautions about one Cell (roadmap step 4.2a).
--
-- One row per note across its whole life (PROPOSED -> WRITTEN -> CLEARED | EXPIRED,
-- PROPOSED -> REJECTED, Appendix C); `state` is the column every list_wax/update_wax_state call
-- filters or moves. `cell_id` plus `state` is the lookup path hot-state assembly uses (every
-- WRITTEN note for a Cell in play); `expires_at` is the House Bee sweep's own scan for wax past
-- its deadline. `body` is the row's full model_dump_json() text, the source of truth every read
-- decodes back through CellWax.model_validate_json, matching every other memory table.
CREATE TABLE IF NOT EXISTS memory_cell_wax (
    id TEXT PRIMARY KEY,
    cell_id TEXT NOT NULL,
    state TEXT NOT NULL,
    severity TEXT NOT NULL,
    clearance TEXT NOT NULL,
    expires_at TEXT,
    proposed_at TEXT NOT NULL,
    body TEXT NOT NULL
);

-- list_wax's own lookup: every note for one Cell in a given set of states, within an allowance.
CREATE INDEX IF NOT EXISTS idx_memory_cell_wax_cell_state
    ON memory_cell_wax (cell_id, state, clearance);

-- The House Bee sweep's own scan: every WRITTEN note anywhere with an expiry, regardless of Cell.
CREATE INDEX IF NOT EXISTS idx_memory_cell_wax_state_expires_at
    ON memory_cell_wax (state, expires_at);
