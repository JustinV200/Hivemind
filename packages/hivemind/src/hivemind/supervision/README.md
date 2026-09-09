# hivemind.supervision

The one `Supervisor` protocol used at every level of the tree: human, the Queen, a Warden, a
sub-bee. This package holds the shared shape codingrules section 8.8 describes: children,
telemetry, inspection and intervention (`supervisor.py`); the `Alarm` an unresolved issue becomes,
mirrored kinds/severity and its state machine (`alarm.py`); the bounded `ContextTelemetry` every bee
reports plus pure helpers over it (`telemetry.py`); the six intervention levers
(`intervention.py`); the escalation policy loaded from TOML (`policy.py`); and the deterministic
`Attendant` every supervisor's inbox goes through (`attendant/`). It never imports `hivemind.llm`:
both `hivemind.queen.autopilot` and `hivemind.wardens.autopilot` import this package, and
`lint-imports` forbids any path from either into `hivemind.llm`.

## Public API (roadmap step 3.13)

- **Errors** (`errors.py`): `SupervisionError` (root), `InvalidAlarmTransitionError`,
  `PolicyError`, `UnknownChildError`.
- **Supervisor** (`supervisor.py`): `Supervisor` (the protocol: `children()`, `telemetry(child)`,
  `inspect(child) -> CompactView`, `intervene(child, Intervention)`), `ChildRef` (`id`, `kind`,
  `task_id`, `state`), `ChildKind` (`WARDEN`, `WORKER`).
- **Alarm** (`alarm.py`): `AlarmKind` and `AlarmSeverity` (mirror `waggle.messages.supervision.
  AlarmKind` / `waggle.messages.labels.AlarmSeverity`), `AlarmState` (`RAISED`, `HANDLING`,
  `ESCALATED`, `RESOLVED`) with its `TRANSITIONS` table, `can_transition`/`assert_transition`, and
  `Alarm` (id, kind, severity, origin, attempts, context, detail, clearance, raised_at, state)
  with `from_wire`/`to_wire` against `waggle.messages.supervision.AlarmRaised`.
- **Telemetry** (`telemetry.py`): `ContextTelemetry` (re-exported from waggle, not mirrored),
  `fraction_used`, `is_past_threshold`, `summarise` (a secret-free one-line render for logs).
- **Intervention** (`intervention.py`): `Compact`, `Checkpoint`, `Handoff`, `Rebind` (carries a
  `hivemind.forage.ModelSlot`), `Takeover`, `Cancel`, the `Intervention` discriminated union, and
  `to_wire`/`from_wire` against `waggle.messages.supervision.Intervene`.
- **Policy** (`policy.py`): `PolicyAction`, `PolicyRule` (`kind: AlarmKind | None`,
  `min_attempts >= 1`, `action`), `EscalationPolicy` (`rules`, `default`), `load_policy(path) ->
  EscalationPolicy` (TOML; see `docs/supervision/default-policy.toml`), pure `decide(policy,
  alarm) -> PolicyAction`.
- **Attendant** (`attendant/`): `InboxKind`, `InboxItem`, `WeightTable` (with
  `queen_default()`/`warden_default()`), `Priority`, `TieBreaker` (the model-tie-break seam),
  `Attendant` (`score(item, now)`, `async order(items)`), pure `score_item`.
- **Fake** (`fake.py`): `FakeSupervisor`, a scripted `Supervisor` recording every `intervene` call
  on `interventions`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/supervision
```

Coverage floor is 95% (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.supervision uv run --frozen pytest -p no:cacheprovider \
    --cov=hivemind.supervision --cov-report=term-missing packages/hivemind/tests/unit/supervision
```
