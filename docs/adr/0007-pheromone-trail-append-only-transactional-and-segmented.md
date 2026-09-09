# ADR-0007: The Pheromone Trail is append-only, transactional and segmented

- Status: Accepted
- Date: 2026-09-08

## Context

The Pheromone Trail is the Hive's audit record (coding rules section 12): every state-changing
action, a Virtual Cell provisioned, a Real Cell (a borrowed device) leased or released, a task
assigned, a tool promoted, a device enrolled, a path touched outside a lease's scratch directory,
writes a `PheromoneEvent` before the action counts as complete. Humans audit it, the Observation
Hive (the dashboard) reads it live, Requeening (rebuilding a crashed Queen, the central
orchestrator) and Supersedure (moving the Hive Stand) reconcile against it, and hot state (what a
model sees in an awake episode) is partly derived from it. Three forces shape it. First, an audit
log is only worth anything if nothing can rewrite it, so the store must be append-only in a way a
reader can verify by looking at one file, not by trusting a convention. Second, Appendix C's third
rule requires every transition to be a trail event written in the same transaction as the state
change, so the trail can never disagree with the Brood Chamber (the task store) about what
happened; ADR-0006 gives both one SQLite file, which makes that possible. Third, a Warden (the
per-Cell supervisor) that is offline keeps working within what it owns and writes its trail to a
local segment that merges into the Queen's trail on reconnection (coding rules 8.8 and 12), so the
trail is one logical log made of per-node segments, where a node is one process (ADR-0005). Two
further rules bound what an event may hold: thoughts are memory, not audit, so the trail records
that an episode happened and what it cost, never its text, and no event carries a prompt, a
completion, a page or a screenshot; and the Night Veil boundary (section 12) requires that a Night
Veil Cell's execution records live in an ephemeral segment purged at teardown, along with the VPN
gateway's and Tor daemons' own per-Cell connection and circuit logs, leaving only the lifecycle
skeleton on the Queen's trail. Ids are ULIDs from `waggle.ids` (ADR-0003) and every event carries
its node id, so ordering across nodes is by timestamp plus node id and is documented as
approximate.

## Decision

A `PheromoneEvent` is a frozen pydantic model with `extra="forbid"` and eight fields: `id`, an
`event_<ULID>` minted by the writer; `hive_id`; `node_id`, the process that recorded it; `at`, a
timezone-aware UTC datetime from the injected Clock; `actor`, the bee that caused it (a hive,
warden, worker or device id) or the literal `human` or `system`; `kind`, a `<family>.<name>`
string; `subject_id`, the id of the thing the event is about; and `payload`, a JSON object of ids,
counts, reasons and enum values. There is one subclass per family (`cell`, `task`, `alarm`,
`forage`, `memory`, `queen`, `warden`, `tool`, `swarm`, `capping`, `llm`), each declaring the
closed set of kinds it accepts, and the kind vocabulary is documented in the family module's
docstring: the strings are the stable audit vocabulary that the Observation Hive, runbooks and
humans read, so a kind is never renamed, only added. `llm.call` carries the normalised usage
(input, output and cached tokens, cost) together with the slot and provider, so cost is on the
trail whatever adapter served the call. The no-text rule is enforced at validation, not by review:
a payload may not contain a key named `prompt`, `completion`, `messages`, `history`,
`transcript`, `content`, `text`, `output` or `screenshot` at any depth, no string value may exceed
one thousand characters, and the whole payload may not exceed sixteen kibibytes. The SQLite store
keeps one table, `pheromone_events`, with the event's indexed columns and its JSON body, and the
module that implements it contains no `UPDATE` or `DELETE` statement at all; a unit test reads the
file and asserts that, so the append-only guarantee is checkable. Writes are transactional in two
ways: the trail's own `record` is one transaction, and the module exports a synchronous
`insert_event(connection, event)` that any other store calls inside its own transaction, which is
how the Brood Chamber writes a task row and its `task.*` event atomically. A duplicate id is a
`DuplicateEventError` on `record`, never a silent overwrite. Segments are keyed by `node_id`:
`export_segment(node_id, since)` returns a `TrailSegment` (the node id, an export time and the
node's events) that serialises to JSON and travels as a file or over Waggle; `merge_segment`
inserts every event whose id is unknown and ignores the rest, so a segment can be merged twice
without duplicates and two segments with interleaved timestamps merge into one log. Trail order
is `(at, node_id)` with ties kept in the order they were recorded (a stable sort in memory, the
row's insertion order in SQLite, and a merged segment inserted in its exporter's order), never
the event id, because ids minted within one millisecond carry random tails; every query returns
that order, and the documentation says it is approximate across nodes because their clocks are
not the same clock. The Night Veil boundary is
the one deletion in the package and lives in its own module, `retention.py`: a Night Veil Cell's
Warden runs inside the Cell, so its node's segment is the ephemeral segment, and at teardown
`NightVeilTeardownPurge` deletes that node's rows, calls every registered side-channel purger (the
protocol the VPN gateway and Tor daemon log owners implement in phases 5 and 11), and records one
`cell.purged` event on the Queen's trail carrying only the counts, so the purge itself is
auditable while nothing it purged is.

## Consequences

Positive: the trail cannot disagree with the state it audits, because they commit together, and a
reader can prove the store is append-only by reading one file and one test rather than auditing
every call site. Every family's vocabulary is closed and documented in one place, so the
Observation Hive and the runbooks can rely on kind strings the way they rely on an API. The no-text
rule is a validator, so a future subsystem cannot leak a prompt onto the trail by accident, and the
size caps keep the trail small enough to tail and to copy on Supersedure. Segments keyed by node id
mean an offline Warden's local trail is the same type as the Queen's, and merging is idempotent by
construction, which is exactly what reconnection after a flaky link needs. The Night Veil purge is
deterministic and complete on the trail side, records that it happened, and gives the side
channels a protocol to plug into rather than an informal checklist.

Negative: an append-only table with no deletes grows forever on the Queen's node; retention for
ordinary events (archiving old segments to a file, say) is a later runbook, not a phase 2 concern,
and until it exists the trail's size is bounded only by disk. Approximate cross-node ordering means
a reader reconstructing a causal chain across two nodes must use ids and correlation fields, never
timestamps alone. Enforcing the no-text rule by key name and size catches the obvious leaks and
none of the subtle ones: a payload can still smuggle text under an innocuous key up to the string
cap, so review remains responsible for what a new event kind puts in its payload. One class per
family rather than one per kind keeps the model small but means the payload's shape per kind is
documented, not typed; a kind that needs typed fields (as `llm.call` does) gets them on its family
class, and if that pattern spreads the families split into typed kinds. The purge deletes by node
id, so anything a Night Veil Warden's node recorded is purged, including any lifecycle event it
recorded itself; the surviving skeleton therefore has to be recorded by the Queen's node, which is
already how placement, grants and teardown are recorded. Side-channel purgers do not exist until
the daemons do, so in phase 2 the boundary is complete for the trail and declared, not exercised,
for the logs around it.

## Alternatives considered

Event sourcing, with the trail as the only store and every table a projection of it: elegant, but
it makes every read a replay or a maintained projection, and the Brood Chamber's state machine,
the Forage ledger and the Honey Store each have query shapes that a projection would just
re-create as tables; keeping the state in tables and the audit beside it, in one transaction,
gives the same guarantee with ordinary queries.

One JSONL file per node, like the outbox (ADR-0005): trivially append-only and easy to tail, but
it cannot share a transaction with a SQLite table, so the trail could disagree with the state
after a crash between the two writes, and queries by subject or kind would mean scanning files.

One table per event family or per kind: typed columns per kind, but a query for "everything about
this task" would span a dozen tables, merging a segment would touch all of them in one
transaction, and adding a kind would be a migration instead of a line.

A message broker or log service (Kafka, NATS JetStream): built for exactly this shape, but it is a
service to run on a single-operator Hive Stand on Windows, and the trail would then live outside
the one file that backup, restore and Supersedure copy.

Marking Night Veil rows with a flag and filtering them instead of deleting: no deletion in the
package at all, but the records would still exist on disk after teardown, which is precisely what
the no-retention guarantee forbids; a flag hides, a purge removes.

Enforcing the no-text rule by review alone: zero code, but section 12's rule is the kind that is
broken by a well-meaning new event kind six phases from now; a validator fails the test the day it
is written.
