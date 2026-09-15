-- Create memory_bee_bread: the warm memory tier's one table (roadmap step 4.2).
--
-- Mirrors the other four memory tables' shape: indexed columns for the lookups
-- (hivemind.memory.bee_bread.index.BeeBread's by_id/by_task/between) plus `body`, the row's full
-- model_dump_json() text, the source of truth every read decodes back through
-- BeeBreadEntry.model_validate_json. `task_id` is nullable: not every entry concerns one task
-- (a TRANSCRIPT deposited outside any task, say).
CREATE TABLE IF NOT EXISTS memory_bee_bread (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    task_id TEXT,
    clearance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL
);

-- by_task's own lookup: every entry for one task, oldest first.
CREATE INDEX IF NOT EXISTS idx_memory_bee_bread_task_id ON memory_bee_bread (task_id, created_at);

-- between's own lookup: every entry in a time range, regardless of task.
CREATE INDEX IF NOT EXISTS idx_memory_bee_bread_created_at ON memory_bee_bread (created_at);

-- The clearance filter every lookup applies on top of by_task/between's own WHERE clause.
CREATE INDEX IF NOT EXISTS idx_memory_bee_bread_clearance ON memory_bee_bread (clearance);
