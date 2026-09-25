-- Roadmap step 10.5 (docs/adr/0040, "The chat is the human end of the Queen's inbox"): the one
-- append-only chat log, in the Queen's own tables. `seq` is the log's own position, assigned on
-- append as one more than the last (lines are never deleted, so a position is never reused): the
-- cursor /v1/chat pages by and its stream follows. `body` is the line's full
-- ChatEntry.model_dump_json(), the source of truth every read decodes; the other columns mirror
-- the fields a read filters or orders by. Every line is C2 (a human's words, and what is read
-- beside them).
CREATE TABLE IF NOT EXISTS chat_entries (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    at TEXT NOT NULL,                -- datetime.isoformat() UTC; sorts like time (ADR-0006)
    author TEXT NOT NULL,            -- ChatAuthor's value: 'human' or 'queen'
    kind TEXT NOT NULL,              -- ChatKind's value
    handled_at TEXT,                 -- a human message, once the Queen decided on it
    body TEXT NOT NULL
);

-- The Queen's tick: "human messages not yet handled, oldest first", on every tick.
CREATE INDEX IF NOT EXISTS idx_chat_entries_waiting ON chat_entries (author, handled_at, seq);

-- The Entrance's reads by time ("since"), beside the primary key's reads by position.
CREATE INDEX IF NOT EXISTS idx_chat_entries_at ON chat_entries (at, seq);
