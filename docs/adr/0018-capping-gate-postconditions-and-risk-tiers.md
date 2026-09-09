# ADR-0018: Capping gate, postconditions and risk tiers

- Status: Accepted
- Date: 2026-09-08

## Context

Codingrules section 8.12 requires that nothing with a side effect outside a lease's scratch
directory lands uncapped: a bee proposes an action with its risk tier and the postconditions it
expects to hold, the gate checks the tier's ladder, applies, verifies and rolls back on failure --
and the proposer never verifies its own work. Several forces shape how the v0 gate
(`hivemind.supervision.capping`) has to be built:

- The check ladder (`SCHEMA`, `LINT`, `TYPES`, `ALLOWLIST`, `SIZE_CAP`, `SANDBOX_TESTS`, `JUDGE`,
  `HUMAN`) has to be data an operator edits, and only three rungs (`SCHEMA`, `ALLOWLIST`,
  `SIZE_CAP`) have an implementation this phase; the rest join later without touching this
  package's code.
- Rollback needs two different mechanisms depending on what kind of Cell a proposal ran on: a
  Virtual Cell can be snapshotted whole and rolled back atomically, but a Real Cell (an existing,
  borrowed device) has no rollback mechanism the Hive controls at all -- `hivemind.cell.
  NoopSnapshotter` documents that gap rather than pretending it away.
- Capping needs to read and add to a Real Cell lease's bookkeeping (its scratch root, its allowed
  paths, and now a restore record for anything written outside scratch), but `cell` sits below
  `supervision` in the layer table (codingrules section 4) and a lease implementation is being
  extended by a concurrent roadmap dispatch at the same time this one runs.
- An unavailable check (a tier names `SANDBOX_TESTS` but the gate's registry has no implementation
  yet) has to fail the proposal, never silently skip the rung -- codingrules section 8.12's
  "nothing lands uncapped" would otherwise have a hole exactly where the ladder is incomplete.
- The Capping table itself (a SQLite store keyed by proposal) is a later phase's work; for now the
  gate needs somewhere to hold an in-flight proposal between `propose` and `run`.

## Decision

`docs/supervision/capping-tiers.toml` is loaded into a `TierTable`: one `TierSpec` per `RiskTier`,
naming the `CheckKind`s that tier requires (`checks`) and the subset that must run even when
`checks` is empty and that a later phase's Tempo-driven shortening may never remove (`floor`,
codingrules section 8.14). `CappingGate.run` computes the required set as `checks | floor`, always
walks it in `CheckKind`'s own declaration order (already cheapest-first), and looks each one up in
an injected `Mapping[CheckKind, Check]`; a `CheckKind` missing from that mapping rejects the
proposal with reason `"check unavailable"` rather than skipping it -- fail closed, so an
incomplete ladder can only ever be stricter than intended, never looser.

Rollback tries a snapshot first when the tier's `snapshot_before` is set, and falls back to
`REVERSE_DIFF` (writing each touched path's prior bytes back, or deleting a path that did not
exist before) on `SnapshotUnsupportedError`. `hivemind.cell.NoopSnapshotter` -- what every Real
Cell source hands out -- always raises that error from `rollback`, so a Real Cell's proposals
always end up on `REVERSE_DIFF`, and a Virtual Cell's backend-specific snapshotter (a later phase)
gets the cheaper whole-Cell restore for free once it exists, with no branch in the gate on which
kind of Cell it is running on.

The slice of a lease Capping needs is named as a `typing.Protocol`, `LeaseView`
(`scratch_root`, `allowed_paths`, `is_path_allowed`, `note_touched_path`, `note_restore_path`),
rather than importing `hivemind.cell.RealCellLease` directly. This keeps the two dispatches
building `cell`'s lease extension and `supervision.capping` independent of each other's exact
timing (codingrules section 8.1: "Protocols at every seam"), and it means `RealCellLease` starts
satisfying `LeaseView` the moment its `note_restore_path` method lands, with no import-side
coordination needed. `apply_action` calls `note_restore_path(path, prior)` then
`note_touched_path(path)` *before* writing, for an outside-scratch write, so a crash mid-apply
still leaves a restore record `release()` can replay (codingrules section 8.7's operator-approved
persistence path is unaffected: this ADR only covers the record capping itself writes before a
write it did not receive explicit approval to keep).

`CappingGate.propose` stores a `Proposal` in a plain in-memory `dict` keyed by its id, and every
transition is also a `capping.*` Pheromone Trail event; the trail, not the in-memory table, is
this phase's durable record (Appendix C: "Proposals and verdicts... In-flight proposals are
re-checked, never auto-applied, after a restart" already anticipates the SQLite capping table a
later phase adds). Every event's payload carries only ids, tier and outcome enum values and
counts, never the human-readable `reason` a `GateOutcome` returns to its caller -- codingrules
section 12's "no event ever carries text" rule applies to `capping.*` exactly as it does to every
other family.

## Consequences

- Adding a check to a tier, or adding SANDBOX_TESTS/JUDGE/HUMAN, is a `capping-tiers.toml` edit
  plus a new `Check` implementation registered in the gate's `Mapping[CheckKind, Check]`; the
  state machine, the trail events and `CappingGate.run` itself never change.
- A Real Cell's proposals are always restorable file by file (`REVERSE_DIFF`); a Virtual Cell's
  proposals get whole-Cell rollback automatically once a real `Snapshotter` is wired in, with the
  gate's own code unaware of which is happening.
- `hivemind.supervision.capping` reads `hivemind.guard` (for `CapabilitySet`) to enforce least
  privilege on a proposal's paths and commands -- the one Layer-2 edge this package needs guard
  for. The current import-linter layer contract in the root `pyproject.toml` lists `guard` and
  `supervision` as independent siblings within Layer 2's upper rank, which forbids this edge; this
  ADR records the edge as intentional and needed, and recommends the same fix already applied to
  `cell` for the identical reason -- splitting `guard` into its own rank, above `cell` and below
  `honey_store | supervision | brood_chamber | memory` (their sibling rank stays exactly as
  it is otherwise) -- as a follow-up outside this dispatch's owned files.
- An in-memory proposal table means a process restart forgets which proposals were mid-flight;
  the trail still records everything that happened to them, and the later SQLite capping table
  (Appendix C) is what will let a restart re-check rather than lose an in-flight proposal.

## Alternatives considered

- **Hard-code the check ladder per tier in Python** instead of `capping-tiers.toml`: rejected
  because codingrules section 8.12 requires "tiers are data"; an operator loosening or tightening
  a tier would otherwise need a code change and a redeploy.
- **Skip an unavailable check** rather than rejecting the proposal: rejected because it would make
  "nothing lands uncapped" false exactly where the check ladder is still incomplete -- the
  scenario this v0 gate, shipping with only three of eight rungs implemented, is guaranteed to hit
  constantly.
- **Import `hivemind.cell.RealCellLease` directly** instead of a `LeaseView` Protocol: rejected
  because it would couple this dispatch to the exact shape and timing of a concurrent dispatch's
  lease work, and because a Protocol seam is the pattern this codebase already uses everywhere a
  lower layer's concrete type would otherwise leak into a higher one uninvited.
- **One rollback code path per Cell kind** (an `if cell.kind == CellKind.REAL` branch): rejected
  outright by codingrules section 8.7 ("branch on capabilities, never on kind"); catching
  `SnapshotUnsupportedError` from a uniform `Snapshotter` call achieves the same result without
  the branch.
- **A SQLite capping table now**, ahead of schedule: rejected as premature for this step; the
  trail already gives full auditability, and the roadmap places the durable capping store in a
  later phase once the Warden and acceptance-checking machinery around it exist to use it.
