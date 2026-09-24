-- Label lowering as a judge-reviewed proposal (ADR-0034). Two parts: honey_nectar keeps the
-- labelling facts intake used to discard, plus the Ripener's own reading of the text, and
-- honey_lowerings holds one lowering proposal per Nectar, ever.
--
-- The facts are text label values ('C0', 'C1', 'C2'), each with a companion rank column
-- (HoneyClearance.rank: C0=0, C1=1, C2=2) for the same reason 0001 gives `clearance_rank`: the
-- lowering candidate scan (lowering_candidates) compares every fact against the row's current
-- label, and a numeric comparison needs neither a string ordering nor a CASE expression. Every new
-- column is nullable and has no default: a row written before this migration keeps NULL in all of
-- them, its facts genuinely unknown, and a NULL rank never satisfies a comparison, so such a row is
-- never a candidate (hivemind.honey_store.lowering.rules says the same in Python).

-- declared_clearance: the highest label any depositor declared (the manifest's default label when
-- none did), kept across dedupe merges. floor_clearance: the provenance floor intake raised the
-- label to (C2 for a Real Cell, a human or watch mode), kept the same way.
ALTER TABLE honey_nectar ADD COLUMN declared_clearance TEXT;
ALTER TABLE honey_nectar ADD COLUMN declared_clearance_rank INTEGER;
ALTER TABLE honey_nectar ADD COLUMN floor_clearance TEXT;
ALTER TABLE honey_nectar ADD COLUMN floor_clearance_rank INTEGER;

-- ripener_clearance: the Ripener's own label for the text, independent of the current one, written
-- in the same transaction that ripens the row (NULL for a heuristic summary). A reading below the
-- label never lowers it; it can only start a proposal. ripener_reason is the model's one-line
-- reason, staged here until a proposal is filed and copied onto it for the human, never shown to
-- the judge.
ALTER TABLE honey_nectar ADD COLUMN ripener_clearance TEXT;
ALTER TABLE honey_nectar ADD COLUMN ripener_clearance_rank INTEGER;
ALTER TABLE honey_nectar ADD COLUMN ripener_reason TEXT;

-- honey_lowerings: one proposal to lower one Nectar's label, and how it was decided. The UNIQUE
-- nectar_id is ADR-0034's "one proposal per Nectar, ever": a proposal is never re-filed after
-- either outcome, so the judge is asked once per Nectar. state follows the one transition table in
-- hivemind.honey_store.lowering.state (PROPOSED -> LOWERED | REJECTED, REJECTED -> LOWERED for the
-- human only). approver is JUDGE or HUMAN once decided; verdict_reasons is the judge's reasons as a
-- JSON array of short strings, stamped with the judge's own rubric_id; human_reason is the
-- operator's reason for a decision of their own. A PROPOSED row with a non-empty note waits for the
-- human (its text is over the judge's size bound, or the judge could not answer max_attempts
-- times), and the judge's queue passes it over. Nothing here is ever text of the deposit itself.
--
-- ON DELETE CASCADE mirrors honey_nectar_sources (0002): only a Night Veil Cell's EPHEMERAL rows are
-- ever deleted, and no such row is ever eligible, so in practice the cascade never fires; it keeps
-- the foreign key from ever blocking that teardown purge all the same.
CREATE TABLE IF NOT EXISTS honey_lowerings (
    id TEXT PRIMARY KEY,
    nectar_id TEXT NOT NULL UNIQUE REFERENCES honey_nectar (id) ON DELETE CASCADE,
    from_clearance TEXT NOT NULL,
    to_clearance TEXT NOT NULL,
    state TEXT NOT NULL,
    approver TEXT,
    ripener_reason TEXT NOT NULL DEFAULT '',
    verdict_reasons TEXT NOT NULL DEFAULT '[]',
    rubric_id TEXT,
    human_reason TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    proposed_at TEXT NOT NULL,
    decided_at TEXT
);

-- The judge's queue and every listing by state: "PROPOSED, oldest first" (pending_lowerings,
-- list_lowerings), with the id as a stable tie-break for two proposals filed in the same instant.
CREATE INDEX IF NOT EXISTS idx_honey_lowerings_state_proposed
    ON honey_lowerings (state, proposed_at, id);
