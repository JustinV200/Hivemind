-- ADR-0068: whether a session was opened by the Hive Stand itself (a loopback login carrying this
-- boot's stand key). A Virtual Cell's guest reaches the loopback listener too, so the Entrance
-- Reducer ends every session that is not the Hive Stand's own: every remote one, and every
-- loopback one opened without the key. Rows written before this migration read as 0, so the next
-- reduction ends them too, which is the safe side.
ALTER TABLE entrance_sessions ADD COLUMN stand INTEGER NOT NULL DEFAULT 0 CHECK (stand IN (0, 1));
