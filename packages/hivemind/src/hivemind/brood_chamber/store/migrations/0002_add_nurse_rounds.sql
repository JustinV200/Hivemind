-- Mirror Task.nurse_rounds as a column: the Nurse Bee rejections a task was sent back for.
--
-- ADR-0061: the Queen counts a task's Nurse rounds in the Brood Chamber, so her round cap holds
-- across a restart. `body` stays the source of truth every read decodes; this column mirrors it,
-- like `status`, so a store query can read the count without decoding a body. Every row written
-- before this migration had no Nurse round, so the default 0 is exactly right for it.
ALTER TABLE tasks ADD COLUMN nurse_rounds INTEGER NOT NULL DEFAULT 0;
