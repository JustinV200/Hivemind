# ADR-0006: SQLite as the single Hive store

- Status: Accepted
- Date: 2026-09-08

## Context

Phase 2 gives the Hive its first durable state: the Brood Chamber (the task store, holding every
task the Queen, the central orchestrator, decomposes a goal into, with its state machine) and the
Pheromone Trail (the append-only audit log every state change leaves an event on). Later phases add
the memory tables (notes, pins, Cell Wax, episode records, Handoffs), the Honey Store (the cold
knowledge tier, with full-text and vector search), the Forage ledger (capacity and grants), the
lease table, the Swarm registry, the Comb Registry, the Capping table and the Entrance's own
tables; Appendix C of the coding rules lists them all with what survives a Queen crash. Every one
of those must survive a process restart on a single machine, the Hive Stand, and Requeening
(rebuilding a crashed Queen) reads them back. Supersedure (moving the Hive Stand to another
machine, coding rules 8.16) copies the stores to the new host, and the runbooks for backup and
restore snapshot them, so the fewer files there are the simpler both become. The README fixes the
engine: "SQLite for the Brood Chamber, the Pheromone Trail, and the Honey Store (with FTS5 and
`sqlite-vec`), one file per Hive." Coding rules section 2 names SQLite and numbered SQL migrations;
section 11 requires every blocking call to run under `asyncio.to_thread` inside the adapter that
owns it and names `sqlite3` as such a call; section 8.3 keeps the core pure and the effects at the
edges behind store Protocols; and Appendix C's third rule requires every state transition to be a
trail event written in the same transaction as the state change, which is only possible when the
state and the trail share a database. The Hive is single-operator (coding rules 8.15) and runs on
Windows 11, Ubuntu and Arch (section 2), so the engine must work identically on all three with no
service to install. Every dependency needs a one-line justification, and the standard library is
preferred when it is within a fifth as good.

## Decision

Every SQLite-backed subsystem of one Hive lives in one SQLite file, opened through
`hivemind.common.sqlite.connect(path)` with `check_same_thread=False`, `isolation_level=None` (so
the driver never opens an implicit transaction and every transaction is an explicit `BEGIN
IMMEDIATE` ... `COMMIT`, taken through the `hivemind.common.sqlite.transaction` context manager,
which rolls back and re-raises on any exception), `journal_mode=WAL` (readers never block the one
writer, and the file survives a crash mid-write), `foreign_keys=ON`, `busy_timeout=5000` and
`synchronous=NORMAL`. Access is through the standard library's `sqlite3`, not `aiosqlite`: an
async store runs each whole transaction as one synchronous function under `asyncio.to_thread`,
serialised by that store's own `asyncio.Lock`, so a transaction is atomic by construction (no
coroutine can interleave a statement into another store's open transaction) and needs no per-
connection worker thread. Two stores that write the same file hold separate connections, which WAL
makes safe; a state change and its trail event share one transaction because the writing store's
transaction function calls the trail's synchronous `insert_event(connection, event)` on its own
connection. Each subsystem owns a numbered migration series, `<subsystem>/migrations/NNNN_name.sql`
loaded by `hivemind.common.migrations.load_migrations` and applied by `apply_migrations`, which
records what ran in one `schema_migrations(subsystem, version, name, applied_at)` table keyed by
subsystem, so several series share the file without renumbering each other. Store Protocols
(`PheromoneTrail`, `TaskStore`, and their successors) are the swap point: nothing above a store
sees SQL. The move to a client-server engine such as Postgres is forced by exactly three things,
none of which Brood 1.0 has: more than one writer process on more than one machine at once (a
Supersedure hands the whole file over, it never shares it), an Observation Hive read load that a
single file's WAL readers cannot serve, or a single Hive's data outgrowing what one file and one
disk hold. Until one of those arrives, a subsystem that wants another store writes a second
implementation of its Protocol, and the migration mechanism grows a dialect, not a rewrite.

## Consequences

Positive: one file is one Hive, so backup, restore and Supersedure copy one thing; every
subsystem's state and the trail commit together, which makes Appendix C's same-transaction rule
mechanical; there is no service to install or secure on any of the three hosts; the standard
library carries the whole storage layer, so `pollen`-style minimal installs and `hive doctor`
diagnostics never depend on a driver package; WAL gives concurrent readers for free, which is what
the Observation Hive and `hive trail tail --follow` are; and `BEGIN IMMEDIATE` takes the write lock
up front, so a transaction never deadlocks upgrading a read lock mid-way. Running a transaction as
one synchronous function keeps the SQL and its rollback in one place a reader can follow top to
bottom, and the `asyncio.Lock` per store documents exactly where the serialisation is.

Negative: one writer at a time per file, so a burst of Wardens reporting through the Queen's
process queues on the locks rather than running in parallel; the thread pool behind
`asyncio.to_thread` is shared with every other blocking call in the process, so a slow migration
can delay an unrelated adapter; separate connections per store mean a cross-store invariant that
is not "state plus trail" (say, a lease row and a task row) still needs one store to own both
writes; FTS5 is built into Python's bundled SQLite on every supported host, but `sqlite-vec` is a
loadable extension that phase 7 must ship and load per platform; and the file's own limits (one
disk, one host, one writer) are the ceiling, so the phase 13 resilience work replicates by copying
the file, not by streaming a log. The ADR commits the storage layer to the standard library, so
the coding rules' toolchain row is amended to name `sqlite3` under `asyncio.to_thread` as the
access path, with `aiosqlite` no longer assumed.

## Alternatives considered

`aiosqlite`: a well-typed async wrapper, but it is a thread per connection running one statement
per `await`, so a multi-statement transaction is only atomic if every caller also holds a lock
around the whole sequence, which is the same lock this decision needs anyway, plus a dependency and
a thread the standard library does not cost.

Postgres from the start: real concurrency and a path to many writers, but a service to install,
secure and back up on Windows, Ubuntu and Arch for a single-operator Hive that has one writer, and
Supersedure would then move a database cluster instead of a file.

One SQLite file per subsystem: isolates each subsystem's schema, but the trail and the state it
audits could then never share a transaction, and a backup would have to copy several files at one
consistent instant, which SQLite cannot promise across files.

An ORM or query builder (SQLAlchemy, peewee): saves SQL in the stores, but hides the transaction
boundaries and the exact statements the append-only rule for the trail needs to be checkable by
reading one file, and adds a dependency for a handful of tables.

A single shared connection and lock for every store: one lock is simpler to reason about, but it
would serialise the Brood Chamber's reads behind the Honey Store's embedding writes; per-store
connections keep contention where the data actually conflicts.
