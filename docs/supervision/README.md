# Supervision policy data

Data the Hive's supervisors (a human, the Queen, a Warden, or a sub-bee, all implementing the same
Supervisor protocol) load at runtime: `default-policy.toml` for escalation rules,
`capping-tiers.toml` for the Capping gate's risk tiers, and `judge-rubrics.toml` for what an
independent judge checks a proposal against, per risk tier. First populated alongside the
supervision package in phase 3; `judge-rubrics.toml` and the `judge`/`audit_rate` tier columns
added in roadmap step 4.10.

**Both tables live in
[`packages/hivemind/src/hivemind/supervision/defaults/`](../../packages/hivemind/src/hivemind/supervision/defaults/),
not in this directory.** They are read through `importlib.resources`, which addresses a package by
dotted name rather than by filesystem path, so a Hive resolves them identically from a checkout,
from an installed wheel, and from a manifest in a directory that is neither. A repository-relative
default could only ever have been correct for a manifest sitting at the root of this repository.
This page is the operator-facing description of what is in them and who reads them.

## `default-policy.toml`

Loaded by `hivemind.supervision.policy.load_policy` into an `EscalationPolicy`: a `default`
`PolicyAction` plus a list of `[[rules]]` tables, each an optional `kind` (an `AlarmKind` member
name; omitted means a wildcard row that matches any kind), a `min_attempts` (always >= 1) and an
`action`. `hivemind.supervision.policy.decide` picks, for an Alarm's own kind, the matching row
with the highest `min_attempts` the Alarm's `attempts` count clears; failing that, the same search
over the wildcard rows; failing that, `default`. The file's own header and per-rule comments record
why each threshold and action were chosen; `tests/unit/supervision/test_policy.py` loads it and
checks a table of `(kind, attempts) -> action` pairs, including the default case.

Roadmap step 10.6c adds the `SECURITY` kind (Waggle 1.9) and its one row: `ESCALATE` from the
first attempt, so a security Alarm is never retried, respawned or rebound at any level; a Warden
sends it to the Queen, and she sends it on to the human. It also adds `QUARANTINE` to the actions a
row may name: on a Warden's own row it quarantines that Warden's sub-bee through the one quarantine
path (`hivemind.wardens.quarantine`), and on the Queen's it orders the Warden of the Alarm's task
to, scoped by the Alarm's bee, task and trail event. The shipped table names it nowhere: a
quarantine is ordered on a Guard request, or by an operator's own row.
`tests/unit/supervision/test_policy_tables.py` walks every `AlarmKind` through the shipped table
and every `PolicyAction` through both autopilot tables, so a member without its row fails there
rather than as a `KeyError` in a tick.

The manifest's `[supervision] policy_file` (roadmap step 3.1) is unset by default, which is what
selects the shipped table; setting it to a path, resolved against the manifest's own directory,
overrides it with an operator's own. The Queen and each Warden load their own `EscalationPolicy`
once at start-up, either way.

## `capping-tiers.toml`

Loaded by `hivemind.supervision.capping.tiers.load_tiers` into a `TierTable`: one
`[tiers.<name>]` section per `RiskTier` (`read_only`, `scratch_write`, `outside_scratch_write`,
`network_egress`, `spend`, `device_command`, `irreversible` -- the lowercase manifest-key form of
each `RiskTier` member), each a `TierSpec` with these columns:

- `checks` -- the `CheckKind`s `hivemind.supervision.capping.gate.CappingGate` runs, cheapest
  first.
- `floor` -- the subset that still runs even when `checks` is empty, and that a task's tempo may
  never remove (codingrules section 8.14; `hivemind.supervision.capping.tiers.checks_for`).
- `snapshot_before` -- whether the gate snapshots the Cell before applying this tier's action.
- `max_diff_bytes` -- the largest inline diff this tier's `DiffSizeCapCheck` allows.
- `judge` (roadmap step 4.10) -- whether `CheckKind.JUDGE` belongs in this tier's real-time
  ladder, without needing to be named in `checks`/`floor` by hand.
- `audit_rate` (roadmap step 4.10) -- the fraction (0.0 to 1.0) of this tier's completed
  proposals `hivemind.supervision.capping.audit.sampler.AuditSampler` samples for after-the-fact judge
  review, for a tier `judge` does not gate live (codingrules section 8.12: "What cannot be gated
  is sampled").

v0 implements exactly three real-time checks (`SCHEMA`, `ALLOWLIST`, `SIZE_CAP`) plus, as of
roadmap step 4.10, an independent-review rung (`JUDGE`) that ships ready to wire in but that no
tier's shipped table turns on yet (`judge = false` everywhere; every tier above `read_only`
instead carries a nonzero `audit_rate`). A `CheckKind` a tier names that the gate's own check
registry does not implement fails the proposal closed, reason `"check unavailable"`, never a
silently skipped rung -- which is exactly why `judge` stays `false` until a Warden's composition
root actually wires a `JudgeReviewer` in. `tests/unit/supervision/capping/test_tiers.py` loads the
shipped file and checks each tier's expected checks, `judge` and `audit_rate`.

The manifest's `[supervision] capping_tiers_file` overrides this table the same way `policy_file`
overrides the escalation policy: unset means the shipped one.

## `judge-rubrics.toml`

Loaded by `hivemind.supervision.capping.checks.rubrics.load_judge_rubrics` into a mapping keyed by
`RiskTier`: one `[rubrics.<name>]` section per tier, each a `JudgeRubric` with a `rubric_id` (a
short, stable slug echoed on every verdict scored against it) and `text` (the rubric body, folded
into the judge's own prompt). `hivemind.supervision.capping.checks.judge.JudgeCheck` carries the
matching rubric, plus a proposal's content and postconditions, to a `JudgeReviewer` -- never the
proposer's transcript or hot state (codingrules section 8.12: "no shared context with the
proposing bee"). `tests/unit/supervision/capping/checks/test_rubrics.py` loads the shipped file and
checks every tier has a rubric.

The manifest may pin `ModelSlot.JUDGE` to a different `[llm.providers.*]` entry than
`ModelSlot.WORKER` in `[llm.slots]`, so the same blind spot in one provider does not correlate
between the bee that proposes and the bee that reviews it; that pin already works today and is not
part of this file.
