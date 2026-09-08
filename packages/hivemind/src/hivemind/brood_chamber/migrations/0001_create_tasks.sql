-- Create the Brood Chamber's two tables: the task graph and the questions asked against it.
--
-- Columns mirror Task's and Question's own fields that a store needs to filter or order by
-- without decoding `body` (the row's full model_dump_json() text, the source of truth every read
-- decodes back through Task.model_validate_json / Question.model_validate_json). `created_at`,
-- `updated_at` and `asked_at` are stored as datetime.isoformat()'s fixed-offset UTC strings,
-- which sort lexically the same as chronologically (ADR-0006, matching hivemind.pheromone's own
-- `at` column). `questions.task_id` references `tasks(id)` so a question can never outlive, or
-- name, a task the Brood Chamber does not also hold.
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    body TEXT NOT NULL
);

-- TaskFilter's two fields together: "every task under this goal in this status".
CREATE INDEX IF NOT EXISTS idx_tasks_goal_status ON tasks (goal_id, status);

-- list_tasks order: every list_tasks call orders by exactly this pair (created_at, id).
CREATE INDEX IF NOT EXISTS idx_tasks_created_at_id ON tasks (created_at, id);

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    status TEXT NOT NULL,
    asked_at TEXT NOT NULL,
    body TEXT NOT NULL
);

-- list_questions' two filters together: "every question on this task in this status", and the
-- prefix (task_id) alone answers "every question on this task" when status is unset.
CREATE INDEX IF NOT EXISTS idx_questions_task_status ON questions (task_id, status);
