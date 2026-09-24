-- Roadmap step 10.5 (docs/adr/0032, "A goal is durable before it is acknowledged"): the Queen's
-- own goal-request table. One row per request, committed together with its
-- queen.goal_request_received event before the Hive Entrance answers 202, so a crash after that
-- answer loses nothing. `body` is the row's full GoalRequest.model_dump_json(), the source of
-- truth every read decodes; the other columns mirror the fields a read filters or orders by.
-- Timestamps are datetime.isoformat() UTC strings, which sort lexically the same as in time
-- (ADR-0006, matching the Brood Chamber's own columns).
CREATE TABLE IF NOT EXISTS goal_requests (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,             -- GoalRequestState's value; the drain reads RECEIVED/PLANNING
    device_id TEXT,                  -- NULL for the operator's own local CLI
    goal_id TEXT,                    -- the planned goal's id, set once PLANNED
    received_at TEXT NOT NULL,
    finished_at TEXT,                -- set once every task of its goal was seen terminal
    body TEXT NOT NULL
);

-- The Queen's drain: "every request in this state, oldest first" (and the finished sweep's
-- "PLANNED and not finished"), on every tick.
CREATE INDEX IF NOT EXISTS idx_goal_requests_state ON goal_requests (state, received_at, id);

-- A device's own goals, for the Entrance's goals view and a revocation's --cancel-goals.
CREATE INDEX IF NOT EXISTS idx_goal_requests_device ON goal_requests (device_id, received_at, id);
