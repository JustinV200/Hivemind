# hivemind.supervision.capping

The Capping gate: nothing with a side effect outside a lease's scratch directory lands uncapped
(codingrules section 8.12). A bee proposes an action with its risk tier and the postconditions it
expects to hold; the gate checks the tier's ladder cheapest-first, applies the action, verifies
every postcondition, and rolls back on failure. The proposer never verifies its own work: `run` is
called by the proposing bee's Warden, never by the bee itself.

## Public API (roadmap step 3.17)

- **Proposal** (`proposal.py`): `Proposal` (id, task_id, cell_id, proposer, risk_tier, action,
  postconditions, tempo, spend_estimate_usd, clearance, reason, state), `MAX_POSTCONDITIONS`.
- **State machine** (`state.py`): `ProposalState` (`PROPOSED`, `CHECKING`, `CAPPED`, `APPLIED`,
  `VERIFIED`, `REJECTED`, `ROLLED_BACK`) with its `TRANSITIONS` table (`PROPOSED -> CHECKING ->
  CAPPED -> APPLIED -> VERIFIED`; `CHECKING -> REJECTED`; `APPLIED -> ROLLED_BACK`),
  `can_transition`/`assert_transition`/`is_terminal`.
- **Tiers** (`tiers.py`): `RiskTier` (mirrors `waggle.messages.capping.RiskTier`, with
  `from_wire`/`to_wire`), `TierSpec` (`checks`, `floor`, `snapshot_before`, `max_diff_bytes`),
  `TierTable` (`tiers: dict[RiskTier, TierSpec]`), `load_tiers(path) -> TierTable` (loads
  `docs/supervision/capping-tiers.toml`).
- **Lease view** (`lease_view.py`): `LeaseView`, the Protocol seam to a Real Cell's lease
  (`scratch_root`, `allowed_paths`, `is_path_allowed`, `note_touched_path`, `note_restore_path`).
  `hivemind.cell.RealCellLease` satisfies it structurally; this package never imports that class.
- **Checks** (`checks/`): `Check` (protocol: `kind`, `async run(context) -> CheckResultRecord`),
  `CheckContext` (proposal, capabilities, lease, scratch_root, tier), `CheckResultRecord` (kind,
  outcome, reason). This phase's rungs: `SchemaCheck`, `PathAllowlistCheck`,
  `CommandAllowlistCheck`, `DiffSizeCapCheck`, and `deterministic_checks()` for the composition
  root's `Mapping[CheckKind, Check]`.
- **Apply** (`apply.py`): `apply_action(session, lease, proposal, scratch_root) -> ApplyResult`
  (DIFF via `diff.apply_unified_diff`, COMMAND via `cell.run`), `ApplyResult`, `TouchedPath`.
- **Diff** (`diff.py`): `apply_unified_diff(prior, diff_text, *, path=...) -> bytes`, the pure
  function behind a DIFF action's apply step.
- **Postconditions** (`postconditions.py`): `check_postcondition(session, index, pc) ->
  PostconditionOutcome`. `FILE_EXISTS`/`FILE_ABSENT`/`COMMAND_EXITS_ZERO`/`TEST_PASSES` are
  checked for real; every other `PostconditionKind` reports `has_held=False`,
  `observed="unsupported in v0"`.
- **Gate** (`gate.py`): `CappingGate` (`propose`, `get`, `pending`, `run`), `GateDeps` (session,
  snapshotter, cell, tiers, trail, identity, clock, checks), `GateOutcome` (proposal_id, state,
  checks, postconditions, reason).
- **Errors** (`errors.py`): `CappingError` (root), `UnknownProposalError`,
  `InvalidProposalTransitionError`, `DiffApplyError`.

## How this phase's checks map to a tier

`docs/supervision/capping-tiers.toml` names, per `RiskTier`, the `CheckKind`s `CappingGate.run`
requires (`checks`) and the subset that still runs even when `checks` is empty (`floor`, which a
later phase's Tempo-driven shortening may never remove). A `CheckKind` the gate's own
`Mapping[CheckKind, Check]` does not implement fails the proposal closed, reason `"check
unavailable"` -- never a silently skipped rung.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/supervision/capping
```

Coverage floor is 95% (codingrules section 14.1, same floor as the rest of `supervision`):

```bash
COVERAGE_FILE=.coverage.capping uv run --frozen pytest -p no:cacheprovider \
    --cov=hivemind.supervision.capping --cov-report=term-missing \
    packages/hivemind/tests/unit/supervision/capping
```
