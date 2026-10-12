-- Roadmap step 9.2 (ADR-0046): the Comb Registry's one table. One row per (name, scope, cell_id,
-- version) of a promoted tool. A version is immutable evidence: the spec, the package hash and the
-- Quarantine Comb's passing report never change after promotion; only is_current, state,
-- supersedes_version and the retirement columns do (the triggers below enforce it, so even a
-- direct UPDATE cannot rewrite history).
--
-- cell_id is stored as '' (empty text) for HIVE scope, never NULL: SQLite treats NULLs as distinct
-- in a unique index, so a NULL cell_id would let the same HIVE (name, version) be inserted twice
-- and make the unique index useless. A CHECK ties the two together: '' exactly when scope = HIVE.
CREATE TABLE IF NOT EXISTS comb_tools (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id TEXT NOT NULL,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('HIVE', 'CELL')),
    cell_id TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    package_size INTEGER NOT NULL CHECK (package_size > 0),
    report_json TEXT NOT NULL,
    quarantine_path TEXT NOT NULL CHECK (quarantine_path IN ('SANDBOX_CELL', 'REAL_CELL')),
    reviewed INTEGER NOT NULL CHECK (reviewed IN (0, 1)),
    promoted_by TEXT NOT NULL,
    promoted_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('PROMOTED', 'RETIRED')),
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    supersedes_version TEXT,
    retired_at TEXT,
    retired_reason TEXT,
    reason TEXT NOT NULL,
    CHECK ((scope = 'HIVE' AND cell_id = '') OR (scope = 'CELL' AND cell_id <> '')),
    -- Only a PROMOTED version may be current: a retired tool is never what callers get.
    CHECK (is_current = 0 OR state = 'PROMOTED'),
    -- A retired row says when it was retired, and a promoted one never does.
    CHECK ((state = 'RETIRED') = (retired_at IS NOT NULL))
);

-- The identity of a version: the registry refuses a second row at the same (name, scope, cell_id,
-- version), which is what makes versions immutable.
CREATE UNIQUE INDEX IF NOT EXISTS uq_comb_tools_identity
    ON comb_tools (name, scope, cell_id, version);

-- At most one current version per tool, at the database level, so a flip that forgets to clear the
-- old one fails loudly instead of leaving two tools answering to one name.
CREATE UNIQUE INDEX IF NOT EXISTS uq_comb_tools_one_current
    ON comb_tools (name, scope, cell_id) WHERE is_current = 1;

-- `hive tools show` and the Warden's loader look a package up by its hash.
CREATE INDEX IF NOT EXISTS idx_comb_tools_package ON comb_tools (package_sha256);

-- A version's evidence is immutable: refuse any UPDATE that names one of these columns.
CREATE TRIGGER IF NOT EXISTS comb_tools_immutable_evidence
BEFORE UPDATE OF row_id, tool_id, name, version, scope, cell_id, spec_json, package_sha256,
    package_size, report_json, quarantine_path, reviewed, promoted_by, promoted_at, reason
ON comb_tools
BEGIN
    SELECT RAISE(ABORT, 'comb_tools evidence is immutable');
END;

-- A retired version stays retired: nothing resurrects it (a rollback targets a PROMOTED row).
CREATE TRIGGER IF NOT EXISTS comb_tools_retired_is_final
BEFORE UPDATE OF state ON comb_tools
WHEN OLD.state = 'RETIRED'
BEGIN
    SELECT RAISE(ABORT, 'a retired comb_tools version cannot change state');
END;

-- Retiring never deletes: the row is the history of what ran.
CREATE TRIGGER IF NOT EXISTS comb_tools_no_delete
BEFORE DELETE ON comb_tools
BEGIN
    SELECT RAISE(ABORT, 'comb_tools rows are never deleted; retire the version instead');
END;
