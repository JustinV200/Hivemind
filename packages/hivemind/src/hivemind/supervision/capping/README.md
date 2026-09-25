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
  (`scratch_root`, `allowed_paths`, `is_path_allowed`, `note_touched_path`, `note_restore_path` --
  roadmap step 5.0a grows a `persist`/`approved_by`/`reason` keyword trio on the last one, so a
  later gate (5.0c/5.0d) can pass a persist decision through this same seam). `hivemind.cell.
  RealCellLease` satisfies it structurally; this package never imports that class.
- **Checks** (`checks/`): `Check` (protocol: `kind`, `async run(context) -> CheckResultRecord`),
  `CheckContext` (proposal, capabilities, lease, scratch_root, tier), `CheckResultRecord` (kind,
  outcome, reason, `judge_error` -- set only by `JudgeCheck` when its reviewer could not answer at
  all). This phase's deterministic rungs: `SchemaCheck`, `PathAllowlistCheck`,
  `CommandAllowlistCheck`, `DiffSizeCapCheck`, and `deterministic_checks()` for the composition
  root's `Mapping[CheckKind, Check]`. The independent-review rung: `JudgeCheck` (`CheckKind.
  JUDGE`; catches `JudgeAnswerError` from its reviewer and reports `FAILED` with `judge_error=True`
  instead of propagating -- 2026-09-21: a judge that cannot produce a verdict is a check outcome,
  not a bee crash), `JudgeReviewer` (the Protocol a model-backed implementation satisfies at the
  Warden layer, a later dispatch), `JudgeRequest`/`JudgeVerdict`/`JudgeOutcome`, `JudgeRubric`,
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
  through `FindingsSink`, records `capping.audited`, raises an `AUDIT_FAILED` `Alarm` on `REJECT`; a judge that cannot answer (`JudgeAnswerError`) makes the sample inconclusive, `capping.audited` with `judge_error` and no outcome, never a crash),
  `AuditRates` (per-tier sampled/failed counts and failure rate, the Guard Bee's future read
  model), `FindingsSink`/`InMemoryFindingsSink` (Nectar deposit seam; the Honey Store's own
  Nectar-backed sink lands in phase 7), `AuditDeps`, `AuditFinding`.
- **Errors** (`errors.py`): `CappingError` (root), `UnknownProposalError`,
  `InvalidProposalTransitionError`, `DiffApplyError`, `JudgeUnavailableError`, `JudgeAnswerError`
  (raised by a real `JudgeReviewer` that could not produce a verdict; caught by `JudgeCheck.run`,
  never left to reach `CappingGate` or the Worker).

## How this phase's checks map to a tier

`supervision/defaults/capping-tiers.toml` names, per `RiskTier`, the `CheckKind`s `CappingGate.run`
requires (`checks`) and the subset that still runs even when `checks` is empty (`floor`, which a
task's tempo may never remove, `tiers.checks_for`). A `CheckKind` the gate's own
`Mapping[CheckKind, Check]` does not implement fails the proposal closed, reason `"check
unavailable"` -- never a silently skipped rung. `judge` (bool) adds `CheckKind.JUDGE` to a tier's
real-time ladder without naming it in `checks`/`floor` by hand; `audit_rate` (0.0 to 1.0) is how
much of that tier's completed work `AuditSampler` samples for after-the-fact review when `judge`
is false. The wiring pass (roadmap step 4.10) turned `judge` on for `outside_scratch_write`,
`spend`, `device_command` and `irreversible` -- the tiers an independent review matters most for
-- and set their own `audit_rate` back to `0.0` (a live review already covers every proposal
there); `irreversible` also floors `JUDGE`, so no tempo ever drops it (codingrules 8.14).
`scratch_write` and `network_egress` stay audit-sampled only, as defence in depth, until a later
phase turns their own `judge` on too.

Roadmap step 10.3 (ADR-0039): the ALLOWLIST rung now requires `cell:outside_scratch:<path>` as
well as `fs:write:<path>` for a write outside scratch, and a third composed check,
`NetworkAllowlistCheck`, requires `net:<host>` for a network step -- one `"<METHOD> <url>"` step in
an `ACTION_SEQUENCE`, the HTTP tool's shape, which `SchemaCheck` now passes on the
`network_egress` tier (and only there) and `apply_action` applies as a no-op, since the gate's
verdict is the authorisation and the tool itself sends the request. A capability refusal names
the missing capability on `CheckResultRecord.denied_capability`, which the Worker's own
`hivemind.workers.tools.proposals.cap` records as the Guard's `guard.denied`.

### Wiring the judge into a Warden

`hivemind.supervision.capping` never imports `hivemind.llm` (codingrules section 4: this package
sits under both autopilot packages), so the model-backed `JudgeReviewer` is built at the Warden
layer: `hivemind.wardens.judge.ModelJudgeReviewer` runs `complete_structured` on `ModelSlot.JUDGE`
through the Warden's own `CallGate`, from a prompt built out of the tier's rubric text and the
`JudgeRequest` alone (`hivemind.llm.prompts.judge_review`). The composition root
(`cli/compose/deps.py::build_warden_deps`) merges this package's own `judge_checks(reviewer,
rubrics) -> Mapping[CheckKind, Check]` (`{CheckKind.JUDGE: JudgeCheck(reviewer, rubrics)}`) into
the `Mapping[CheckKind, Check]` a Warden's `GateDeps.checks` is built from:
`checks={**deterministic_checks(), **judge_checks(reviewer, rubrics)}`; the same `reviewer` and
`rubrics` also land on `WardenDeps.judge_reviewer`/`.judge_rubrics`, additive fields
`hivemind.wardens.spawn.audited_gate.AuditingCappingGate` reads for its own after-the-fact
sampling (the `AuditSampler`/`FindingsSink`/`AuditRates` half of this wiring: that class
subclasses `CappingGate` and calls `audit_completed` once a proposal reaches a terminal state,
since this package's own `gate.py` is where a `Proposal` actually becomes `VERIFIED`/
`ROLLED_BACK` and lives outside `hivemind.wardens`'s own file list to edit directly). The manifest
may pin `ModelSlot.JUDGE` to a different `[llm.providers.*]` entry than `ModelSlot.WORKER` in
`[llm.slots]` so the same blind spot in one provider does not correlate between the bee that
proposes and the bee that reviews; that pin already works through the manifest and needs no code
in this package. Every test that runs the gate at a `judge = true` tier registers a
`hivemind.supervision.capping.checks.fake.FakeJudgeReviewer` (a scripted FIFO queue) or the
test-only `RepeatingJudgeReviewer` (`tests/builders/capping.py`, which never runs dry, for a
default `checks` mapping shared across many proposals in one test run).

A judge that cannot answer at all -- 2026-09-21: `ModelJudgeReviewer`'s `complete_structured` call
exhausted every rung and fallback binding on unparseable output, nine attempts, and the raised
`hivemind.llm.errors.MalformedOutputError` used to propagate straight through `JudgeCheck.run`,
`CappingGate.run` and into the Worker's own run loop as a crash -- is translated by
`ModelJudgeReviewer.review` into `JudgeAnswerError` (this package's own error, since it never
imports `hivemind.llm`) and caught by `JudgeCheck.run`, which reports `FAILED` with
`judge_error=True` instead. `ProviderUnavailableError`/`RateLimitedError` are never translated:
those are outages `hivemind.queen.cluster`'s Clustering rung pauses and resumes the whole Hive
for, not a single proposal's rejection, so they propagate unchanged. The `capping.checked` trail
event for that `CheckKind.JUDGE` rung carries `judge_error: true` in its payload, distinct from an
ordinary JUDGE rejection, whose event carries no such key.

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
