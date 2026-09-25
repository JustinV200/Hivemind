# hivemind.supervision

The one `Supervisor` protocol used at every level of the tree: human, the Queen, a Warden, a
sub-bee. This package holds the shared shape codingrules section 8.8 describes: children,
telemetry, inspection and intervention (`supervisor.py`); the `Alarm` an unresolved issue becomes,
mirrored kinds/severity and its state machine (`alarm.py`); one `alarm.*` Pheromone Trail event per
raise/handle/escalate/resolve step of an Alarm's own chain (`alarm_trail.py`); the bounded
`ContextTelemetry` every bee reports plus pure helpers over it (`telemetry.py`); the seven
intervention levers (`intervention.py`); the escalation policy loaded from TOML (`policy.py`); and
the deterministic `Attendant` every supervisor's inbox goes through (`attendant/`). It never
imports `hivemind.llm`:
both `hivemind.queen.autopilot` and `hivemind.wardens.autopilot` import this package, and
`lint-imports` forbids any path from either into `hivemind.llm`.

## Public API (roadmap step 3.13)

- **Errors** (`errors.py`): `SupervisionError` (root), `InvalidAlarmTransitionError`,
  `PolicyError`, `UnknownChildError`, `UnknownInterventionError` (roadmap step 10.6c: a wire
  action this Hive does not know, refused, never read as a silent cancel).
- **Supervisor** (`supervisor.py`): `Supervisor` (the protocol: `children()`, `telemetry(child)`,
  `inspect(child) -> CompactView`, `intervene(child, Intervention)`), `ChildRef` (`id`, `kind`,
  `task_id`, `state`), `ChildKind` (`WARDEN`, `WORKER`).
- **Alarm** (`alarm.py`): `AlarmKind` and `AlarmSeverity` (mirror `waggle.messages.supervision.
  AlarmKind` / `waggle.messages.labels.AlarmSeverity`), `AlarmState` (`RAISED`, `HANDLING`,
  `ESCALATED`, `RESOLVED`) with its `TRANSITIONS` table, `can_transition`/`assert_transition`, and
  `Alarm` (id, kind, severity, origin, attempts, context, detail, clearance, raised_at, state)
  with `from_wire`/`to_wire` against `waggle.messages.supervision.AlarmRaised`.
- **Alarm trail** (`alarm_trail.py`): `record_alarm_event(trail, identity, clock, alarm, kind,
  **payload)`, one `alarm.*` `hivemind.pheromone.AlarmEvent` per escalation-chain step; `identity`
  is a `hivemind.cell.CellIdentity` (the one Layer-2 sibling this package is documented to import).
- **Telemetry** (`telemetry.py`): `ContextTelemetry` (re-exported from waggle, not mirrored),
  `fraction_used`, `is_past_threshold`, `summarise` (a secret-free one-line render for logs).
- **Intervention** (`intervention.py`): `Compact`, `Checkpoint`, `Handoff`, `Rebind` (carries a
  `hivemind.forage.ModelSlot`), `Takeover`, `Cancel`, `Quarantine` (roadmap step 10.6c: the bee
  and/or its task, the suspect episode id and a reason), the `Intervention` discriminated union,
  and `to_wire`/`to_intervene`/`from_wire` against `waggle.messages.supervision.Intervene`
  (`to_intervene` also names the task and the Alarm an order answers). `from_wire` raises
  `UnknownInterventionError` for an action it has no lever for, so a newer peer's lever is never
  mistaken for a cancel (ADR-0043). A Worker's runtime refuses one it cannot pull and logs it;
  a `Quarantine` that reaches a bee's own runtime is carried out by its Warden, which only ever
  cancels the bee there.
- **Policy** (`policy.py`): `PolicyAction` (`QUARANTINE` since roadmap step 10.6c: a Warden's own
  row quarantines its sub-bee, the Queen's orders the task's Warden to), `PolicyRule` (`kind:
  AlarmKind | None`, `min_attempts >= 1`, `action`), `EscalationPolicy` (`rules`, `default`),
  `load_policy(path) -> EscalationPolicy` (TOML; see `docs/supervision/default-policy.toml`),
  pure `decide(policy, alarm) -> PolicyAction`. `PolicyAction` mirrors no wire enum, so every
  table that maps it (the Warden's and the Queen's autopilot) is walked member by member in
  `tests/unit/supervision/test_policy_tables.py`, and the shipped policy's `SECURITY` row (an
  `AlarmKind` since Waggle 1.9) always goes up: never retried, respawned or rebound.
- **Attendant** (`attendant/`): `InboxKind`, `InboxItem`, `WeightTable` (with
  `queen_default()`/`warden_default()`), `Priority`, `TieBreaker` (the model-tie-break seam),
  `Attendant` (`score(item, now)`, `async order(items)`), pure `score_item`.
- **Fake** (`fake.py`): `FakeSupervisor`, a scripted `Supervisor` recording every `intervene` call
  on `interventions`.

## Public API (roadmap step 3.17): Capping

`capping/` is the QA gate codingrules section 8.12 describes: nothing with a side effect outside
a lease's scratch directory lands uncapped. This face re-exports its three most commonly reached
names -- `CappingGate`, `Proposal`, `RiskTier` -- so `from hivemind.supervision import
CappingGate` works for a quick reach; everything else (`ProposalState` and its transition table,
`TierSpec`/`TierTable`/`load_tiers`, `LeaseView`, the check ladder, `apply_action`,
`apply_unified_diff`, `check_postcondition`, `GateDeps`/`GateOutcome`, and `capping`'s own error
tree) lives on `hivemind.supervision.capping`, documented in that sub-package's own README. It is
the one place under `supervision` that reads `hivemind.guard` (for `CapabilitySet`), because
checking a proposal's paths and commands against what its Warden actually holds is exactly
codingrules section 15's "least privilege is code, not policy."

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/supervision
```

Coverage floor is 95% (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.supervision uv run --frozen pytest -p no:cacheprovider \
    --cov=hivemind.supervision --cov-report=term-missing packages/hivemind/tests/unit/supervision
```
