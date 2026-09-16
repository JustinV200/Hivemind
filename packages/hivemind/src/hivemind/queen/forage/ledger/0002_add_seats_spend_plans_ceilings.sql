-- Roadmap step 4.8: four more tables the same "hosting decisions" phrase in Appendix C's Forage
-- ledger row names -- a shared source's declared seat capacity, spend recorded per goal, each
-- Cell's written HostingPlan and each Warden's set Ceilings -- following 0001's own shape (one
-- row per key; a plain column pair for the two numeric tables, one JSON body for the other two).

-- A shared source's (a server or a hosted provider) declared total seats, keyed by source_id; a
-- fresh declaration replaces the row. Read by ForageLedger.headroom to size shared_seats.
CREATE TABLE forage_ledger_seat_capacity (
    source_id TEXT PRIMARY KEY,
    seats_total INTEGER NOT NULL
);

-- Cumulative spend recorded against one goal (a TaskId), keyed by goal_id; ForageLedger.
-- record_spend replaces the row with the new running total.
CREATE TABLE forage_ledger_spend_by_goal (
    goal_id TEXT PRIMARY KEY,
    spend_usd REAL NOT NULL
);

-- Every Cell's latest written HostingPlan, keyed by cell_id; a fresh revision replaces the row.
CREATE TABLE forage_ledger_hosting_plans (
    cell_id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);

-- Every Warden's currently set Ceilings, keyed by warden_id (the holder); a change replaces the
-- row -- codingrules 8.10: "changing a ceiling is a Queen decision on the trail," recorded
-- separately by hivemind.queen.forage.ceilings, not by this table.
CREATE TABLE forage_ledger_ceilings (
    warden_id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);
