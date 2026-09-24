-- Add the taint label's state to the three tables that hold taintable items (roadmap 10.6d).
--
-- `taint_state` mirrors the marker inside `body` (the row's model_dump_json(), still the source of
-- truth every read decodes): NULL for an item never labelled, 'tainted' while it is refused, and
-- 'cleared' once a judge verdict cleared it. It exists so a read that could feed a prompt can leave
-- a tainted row out with a plain `taint_state IS NOT 'tainted'` clause, without decoding every body
-- first. Only hivemind.memory.store.sqlite.taint ever writes it, in the same transaction as the
-- body it mirrors and the memory.tainted or memory.taint_cleared event that justifies it.
ALTER TABLE memory_handoffs ADD COLUMN taint_state TEXT;
ALTER TABLE memory_episodes ADD COLUMN taint_state TEXT;
ALTER TABLE memory_bee_bread ADD COLUMN taint_state TEXT;

-- find_taintable's own scan over Handoffs: every checkpoint written at or after a scope's moment.
-- Episodes and Bee Bread entries already have an index on their own time column.
CREATE INDEX IF NOT EXISTS idx_memory_handoffs_written_at ON memory_handoffs (written_at);
