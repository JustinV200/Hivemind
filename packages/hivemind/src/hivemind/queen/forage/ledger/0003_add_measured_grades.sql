-- Roadmap step 8.5 (ADR-0045): the grade the Hive's own evaluation harness (`hive llm eval
-- --update-map`) measured for one Forage map source, so routing's grade floors rest on evidence
-- instead of the manifest's hand-set figure. One row per source_id; a fresh measurement replaces
-- the row, and the manifest file itself is never written. `build_forage_map` applies these rows
-- over the manifest's grades when it builds the map.

-- grade is the measured 1 to 5 score; measured_at is an ISO-8601 UTC timestamp; evidence is a
-- short plain-text summary of the run that produced the grade (goals passed, score), kept so an
-- operator can see why a source was graded as it was.
CREATE TABLE forage_ledger_measured_grades (
    source_id TEXT PRIMARY KEY,
    grade INTEGER NOT NULL,
    measured_at TEXT NOT NULL,
    evidence TEXT NOT NULL
);
