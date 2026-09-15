# hivemind.supervision.capping

The Capping gate: nothing with a side effect outside a lease's scratch directory lands uncapped
(codingrules section 8.12). A bee proposes an action with its risk tier and the postconditions it
expects to hold; the gate checks the tier's ladder cheapest-first, applies the action, verifies
every postcondition, and rolls back on failure. The proposer never verifies its own work: `run` is
called by the proposing bee's Warden, never by the bee itself.

## Public API (roadmap steps 3.17 and 4.10)

- **Proposal** (`proposal.py`): `Proposal` (id, task_id, cell_id, proposer, risk_tier, action,
  postconditions, tempo, spend_estimate_usd, clearance, reason, state), `MAX_POSTCONDITIONS`.
- **State machine** (`state.py`): `ProposalState` (`PROPOSED`, `CHECKING`, `CAPPED`, `APPLIED`,
  `VERIFIED`, `REJECTED`, `ROLLED_BACK`) with its `TRANSITIONS` table (`PROPOSED -> CHECKING ->
  CAPPED -> APPLIED -> VERIFIED`; `CHECKING -> REJECTED`; `APPLIED -> ROLLED_BACK`),
  `can_transition`/`assert_transition`/`is_terminal`.
- **Tiers** (`tiers.py`): `RiskTier` (mirrors `waggle.messages.capping.RiskTier`, with
  `from_wire`/`to_wire`), `TierSpec` (`checks`, `floor`, `snapshot_before`, `max_diff_bytes`,
  `judge`, `audit_rate`), `TierTable` (`tiers: dict[RiskTier, TierSpec]`), `load_tiers(path) ->
  TierTable` (loads `supervision/defaults/capping-tiers.toml`). The same module also carries
  `checks_for(tier, tempo) -> tuple[CheckKind, ...]` (roadmap step 4.10), folding a task's tempo
  into its tier's ladder within the tier's floor (codingrules section 8.14): drops `JUDGE` for a
  low-bar or tight-latency-budget task unless it is a floor check, adds a second `JUDGE` pass for
  a `CRITICAL` bar; `SHORTEN_LATENCY_BUDGET_S` is the commented threshold for the latter. It lives
  beside `TierSpec` rather than its own module because the two are read together at exactly one
  call site and a split would have pushed this directory over codingrules 5.6's fan-out limit.
- **Lease view** (`lease_view.py`): `LeaseView`, the Protocol seam to a Real Cell's lease
  (`scratch_root`, `allowed_paths`, `is_path_allowed`, `note_touched_path`, `note_restore_path`).
  `hivemind.cell.RealCellLease` satisfies it structurally; this package never imports that class.
- **Checks** (`checks/`): `Check` (protocol: `kind`, `async run(context) -> CheckResultRecord`),
  `CheckContext` (proposal, capabilities, lease, scratch_root, tier), `CheckResultRecord` (kind,
  outcome, reason). This phase's deterministic rungs: `SchemaCheck`, `PathAllowlistCheck`,
  `CommandAllowlistCheck`, `DiffSizeCapCheck`, and `deterministic_checks()` for the composition
  root's `Mapping[CheckKind, Check]`. The independent-review rung: `JudgeCheck` (`CheckKind.
  JUDGE`), `JudgeReviewer` (the Protocol a model-backed implementation satisfies at the Warden
  layer, a later dispatch), `JudgeRequest`/`JudgeVerdict`/`JudgeOutcome`, `JudgeRubric`,
  `load_judge_rubrics()` (loads `supervision/defaults/judge-rubrics.toml`), `judge_checks(reviewer,
  rubrics)` (`deterministic_checks()`'s sibling registry: `{CheckKind.JUDGE: JudgeCheck(reviewer,
  rubrics)}`), and `FakeJudgeReviewer`, a scripted `JudgeReviewer` for tests.
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
  checks, postconditions, reason). `run` computes its required ladder through `tiers.checks_for`.
- **Audit** (`audit.py`, roadmap step 4.10): `AuditSampler` (`should_sample`, deterministic by
  default, an injectable `random.Random` in production), `audit_completed(deps, proposal, tier,
  rates)` (samples, reviews through the same `JudgeReviewer` seam, deposits an `AuditFinding`
  through `FindingsSink`, records `capping.audited`, raises an `AUDIT_FAILED` `Alarm` on `REJECT`),
  `AuditRates` (per-tier sampled/failed counts and failure rate, the Guard Bee's future read
  model), `FindingsSink`/`InMemoryFindingsSink` (Nectar deposit seam; the Honey Store's own
  Nectar-backed sink lands in phase 7), `AuditDeps`, `AuditFinding`.
- **Errors** (`errors.py`): `CappingError` (root), `UnknownProposalError`,
  `InvalidProposalTransitionError`, `DiffApplyError`, `JudgeUnavailableError`.

## How this phase's checks map to a tier

`supervision/defaults/capping-tiers.toml` names, per `RiskTier`, the `CheckKind`s `CappingGate.run`
requires (`checks`) and the subset that still runs even when `checks` is empty (`floor`, which a
task's tempo may never remove, `tiers.checks_for`). A `CheckKind` the gate's own
`Mapping[CheckKind, Check]` does not implement fails the proposal closed, reason `"check
unavailable"` -- never a silently skipped rung. `judge` (bool) adds `CheckKind.JUDGE` to a tier's
real-time ladder without naming it in `checks`/`floor` by hand; `audit_rate` (0.0 to 1.0) is how
much of that tier's completed work `AuditSampler` samples for after-the-fact review when `judge`
is false. v0's shipped table leaves every tier's `judge` false (no Warden has a `JudgeReviewer`
wired into its check registry yet) and gives every tier above `read_only` a nonzero `audit_rate`.

### Wiring the judge into a Warden (for the orchestrator; not built by this package)

`hivemind.supervision.capping` never imports `hivemind.llm` (codingrules section 4: this package
sits under both autopilot packages), so the model-backed `JudgeReviewer` -- the one that calls
`complete_structured` on `ModelSlot.JUDGE` -- is built at the Warden layer. Once it exists, a
composition root (`cli/compose/deps.py`) merges this package's own `judge_checks(reviewer,
rubrics) -> Mapping[CheckKind, Check]` (`{CheckKind.JUDGE: JudgeCheck(reviewer, rubrics)}`) into
the `Mapping[CheckKind, Check]` a Warden's `GateDeps.checks` is already built from
`deterministic_checks()`: `checks={**deterministic_checks(), **judge_checks(reviewer, rubrics)}`.
The manifest may pin `ModelSlot.JUDGE` to a different
`[llm.providers.*]` entry than `ModelSlot.WORKER` in `[llm.slots]` so the same blind spot in one
provider does not correlate between the bee that proposes and the bee that reviews; that pin
already works through the manifest today and needs no code in this package.

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
