# The Guard Bee

Roadmap step 10.6, [ADR-0035](../adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md).
Code: `hivemind.workers.roles.guard_bee` ([README](../../packages/hivemind/src/hivemind/workers/roles/guard_bee/README.md)).
Rules: [`rules.toml`](../../packages/hivemind/src/hivemind/workers/roles/guard_bee/rules.toml), read
through `importlib.resources`. Settings: `[guard]` and `[guard.bee]` in the Hive Manifest
([full example](../manifests/full.toml)).

The Guard Bee is the Hive's security watcher. It runs in the Queen's process on the Hive Stand, on
her tick, so no Cell action can take it down and it always reads the central Pheromone Trail. It
counts events on the trail against deterministic rules, and every rule that fires becomes a
report. It never touches a Cell, never widens anything, and never calls a model while it counts.

Every Hive the CLI composes runs one: `hive run` and `hive serve` both build it in
`hivemind.cli.compose.guard.with_guard`, from the Queen's own trail, clock, Guard policy, slot
resolver and call gate and the manifest's `[guard]` section, before the Queen exists. Its door is
the Queen herself (`Hive.guard_door`), reached through a relay the composition root binds the
moment she is built, before anything ticks. It is closed after she stops, so no judge episode
outlives her.

It acts alone only to narrow the whole Hive: it raises a Capping tier's sampled-audit rate, and it
orders the Hive Entrance reduced. Anything aimed at one Cell or one bee (quarantine a bee, isolate
a Cell, cut a Sting) is a request the Queen decides on.

## What a finding becomes

Every finding, whatever it recommends, becomes three things:

1. A **`GuardReport`** (`hivemind.guard.report`): the rule, the trail events it cites (at most 64,
   oldest first), the Cell, bees, tasks and grants it touches, the recommended action and a
   confidence (`low`, `medium`, `high` or `critical`).
2. A **C2 deposit** of that report, in its Cell's folder of the Honey browser (or `/hive`). Until
   phase 7's Nectar intake exists, deposits are kept in memory behind the `GuardReportSink` seam.
3. A **`guard.alert`** trail event: the report id, the rule, the action, the confidence, the key
   it counted, the measure against the threshold and the window, what became of the report,
   whether it was shown to the human, and the moment the finding counted up to. Ids, enums and
   numbers only.

A report at `critical` confidence also reaches the human. One that asks for nothing (a raise, a
reduction, an observation) is shown at once through the door's `report_to_human`: a CRITICAL
SECURITY Alarm naming the report, pushed to every enrolled device (`shown` on the alert). A
CRITICAL request is filed instead, and the Queen shows it when she decides it. Every Alarm about
one report carries the same id, so no report is ever shown twice.

Then, by action:

| Action | What happens | `disposition` on the alert |
|---|---|---|
| `raise_audit_rate` | `guard.audit_rate_raised`: the tier's rate in force, plus `audit_raise_step`, at most 1.0, for `audit_raise_hold_s`. | `raised`, or `at_ceiling` when the tier already samples everything |
| `reduce_entrance` | `guard.reduce_ordered`: the running Entrance reduces itself within a poll (remote listener stopped, remote sessions ended). | `reduce_ordered` |
| `quarantine_bee`, `isolate_cell`, `sting_cut` | A request through the Queen's door, when admitted. | `filed`, `coalesced`, `capped` or `below_floor` |
| `observe` | Nothing more: the alert is the record. | `observed` |

A request is admitted only when all three limits allow it:

- **The floor.** Its confidence is at least `[guard] request_confidence` (default `high`).
- **Coalescing.** No request was filed for the same rule and target (its Cell, else its first bee
  or task) in the last `[guard.bee] coalesce_window_s` (default 900 s).
- **The cap.** Fewer than `[guard] requests_per_hour` (default 6) were filed in the last hour.

A request held back is still a `guard.alert`. Only filed requests count toward coalescing and the
cap, so a burst of weak reports can never starve a strong one. A `critical` request is never held
back by coalescing or the cap: it is always filed. A recommendation whose target the finding does
not name (a quarantine when no bee is known) is reported as `observe`.

## The rules

Each rule counts events of some trail kinds, optionally only those whose payload fields hold given
values, grouped by one key, inside a sliding window. Three shapes:

- **count**: the events for one key reach `threshold` in the window (or their distinct values of
  another key do, with `distinct`; or their summed model spend does, with `measure = "cost_usd"`).
- **ratio**: the events it counts over the events it is `of` reach `ratio`, once the `of` side
  holds at least `threshold` events.
- **sequence**: a `first` event, then `threshold` events it counts after it, for one key.

Keys: `hive` (everything together), `bee` (a worker id; one is minted per spawn, so a bee id names
one attempt at one task: one episode), `task`, `cell`, `grant`, `device`, `address`, `tier`. A key
is read from the event (its subject and the ids its payload carries) or joined through other
events: a Capping proposal's task, Cell and tier from its `capping.proposed`, a task's bee from
`worker.spawned`, its Cell from `queen.assigned`, its grant from `forage.granted`. Rules count ids
and kinds, never content: the trail carries none.

A rule fires once for what it has counted. Its next finding for the same key counts only events
after the moment the last one counted up to.

### A bee's own behaviour

| Rule | Counts | Key | Window | Fires at | Recommends | Judged |
|---|---|---|---|---|---|---|
| `injection_then_denial` | `guard.injection_suspected`, then a `guard.denied` or a `capping.rejected` whose failing check is `ALLOWLIST` | bee | 15 min | 1 after the flag | `quarantine_bee`, high | no |
| `injection_burst` | `guard.injection_suspected` | bee | 10 min | 3 | `quarantine_bee`, medium | yes |
| `denial_burst` | `guard.denied` of a worker | bee | 5 min | 5 | `quarantine_bee`, medium | yes |
| `network_attempts` | `guard.denied` of a worker for a `net:` capability | bee | 10 min | 3 | `quarantine_bee`, medium | yes |
| `outside_scratch_touches` | `cell.touched_outside_scratch` | cell | 10 min | 25 | `isolate_cell`, medium | yes |
| `outside_scratch_refusals` | `capping.rejected` at `ALLOWLIST` on `OUTSIDE_SCRATCH_WRITE` | bee | 10 min | 3 | `quarantine_bee`, medium | yes |

`injection_then_denial` is the correlation roadmap step 10.6b's scanner exists for: outside text
flagged as a likely injection, then a denial by the same bee in the same episode. A lease-boundary
refusal of a write outside scratch is recorded only as `capping.rejected` at `ALLOWLIST` (no
`guard.denied`), and it is exactly what an injected instruction tries, so it counts as a denial
here. A flag in one episode and a denial in another (the same task's next attempt, a new bee id)
never correlate.

### Spend

| Rule | Counts | Key | Window | Fires at | Recommends | Judged |
|---|---|---|---|---|---|---|
| `over_grant` | `alarm.raised` of kind `GRANT_EXCEEDED` | task | 1 h | 3 | `observe`, low | no |
| `grant_spend_surge` | model spend on `llm.call` under one grant | grant | 10 min | $5.00 | `quarantine_bee`, medium | yes |

The trail carries no grant's budget, so over-grant spend is read from the Hive's own over-grant
signal, the `GRANT_EXCEEDED` Alarm a Warden raises. That is capacity, not misbehaviour: an alert.

### Capping

| Rule | Counts | Key | Window | Fires at | Recommends | Judged |
|---|---|---|---|---|---|---|
| `capping_rejection_rate` | `capping.rejected` over `capping.proposed` | tier | 1 h | 50 %, of at least 10 | `raise_audit_rate`, medium | no |
| `capping_rollback_rate` | `capping.rolled_back` over `capping.applied` | tier | 1 h | 30 %, of at least 5 | `raise_audit_rate`, medium | no |
| `audit_failure_rate` | `capping.audited` REJECT over every audit verdict | tier | 24 h | 25 %, of at least 3 | `raise_audit_rate`, high | no |

### Node integrity

| Rule | Counts | Key | Window | Fires at | Recommends | Judged |
|---|---|---|---|---|---|---|
| `envelope_forgery` | `guard.envelope_refused` | cell | 1 h | 1 | `isolate_cell`, critical | no |
| `segment_forgery` | `guard.segment_refused` for `another_node` | cell | 1 h | 1 | `isolate_cell`, critical | no |
| `segment_unmergeable` | `guard.segment_refused` for `format` or `corrupt` | cell | 1 h | 1 | `isolate_cell`, high | no |
| `request_forgery` | `guard.entrance_login_failed` for `request_signature` or `request_replay` | hive | 10 min | 2 | `reduce_entrance`, high | no |

The Cell gate (`hivemind.queen.cell_gate`, the Queen's listener every Virtual Cell's Warden dials)
records the first three, about the Cell whose own proved link carried the frame:

- **`guard.envelope_refused`**: a frame on an attached link failed its signature (reason
  `invalid`, `missing` or `unknown_node`). The link is closed. A tampered link, or a Cell signing
  as a node it cannot prove.
- **`guard.segment_refused`**: a trail segment the Cell shipped was not merged. `another_node`: a
  chunk named a node or Warden its link never proved (the spec's receiver rule), or carried
  another node's segment, so nothing of it reached the trail. `format` and `corrupt`: an unknown
  segment format, or bytes that do not match the size, digest or event count the chunk declared.
  The link stays up: an honest Cell's bad export is a fault to hear of, not a reason to cut its
  Warden off mid-task.

Each reason is recorded once per link, so no Cell can flood the trail. `envelope_forgery` and
`segment_forgery` are shipped dire patterns: the Queen isolates the Cell by rule. A frame that
fails its signature before its link is attached proves no Cell (whoever dialled may have named any
Cell's id), so the gate closes that connection and records nothing against anyone.

`request_forgery` is someone holding a session token without its device's key. The node-integrity
failures later steps add become rules once each has a trail kind: Waggle replay refusal
(`guard.replay_refused`, 11.3b), repeated frame-ceiling closes (`guard.frame_ceiling`, 11.3a), a
merged segment whose events fail the node's key (11.9), and a capability report that changed
without re-enrolment (13.4a).

### The Hive Entrance

| Rule | Counts | Key | Window | Fires at | Recommends | Judged |
|---|---|---|---|---|---|---|
| `login_failure_burst` | `guard.entrance_login_failed` on the remote listener (proof, password, device, listener) | hive | 5 min | 20 | `reduce_entrance`, high | no |
| `lockouts_across_devices` | `guard.entrance_locked` for a lockout or a denial burst | distinct devices | 1 h | 2 | `reduce_entrance`, high | no |
| `invite_abuse` | `guard.entrance_redeem_failed` for an unknown, used or expired code | address | 10 min | 5 | `reduce_entrance`, high | no |
| `travel_lock_triggered` | `guard.entrance_travel_lock` | device | 1 h | 1 | `observe`, medium | no |

Reducing the Entrance is always safe without judgement: it closes the remote listener, and only
the operator, on loopback and after step-up, reopens it.

## Judgement

A rule with `judgement = true` hands each finding to one awake episode on the judge slot before
it is reported. The episode is shown the report's facts only (the rule, the counts, the ids, the
rule's own verdict and the actions its targets allow) and may raise or lower the confidence and
change the action to `observe` or to a request the targets allow. A rule that narrows the whole
Hive never takes judgement.

Episodes run one at a time beside the Queen's tick, never inside it, each bounded by
`[guard.bee] judge_timeout_s`, and are charged to the Royal Reserve (the Forage the Queen holds
back for herself before any grant), as the House Bee's are: every call goes through the Queen's
own call gate, under no grant, and is recorded as an `llm.call` on the judge slot with no
`grant_id`, on the Queen's node. No task's grant pays for the Hive's own security watcher. When the judge slot is unbound, the
model fails or times out, or the Guard policy's `guard_bee` role does not hold `llm:judge`, the
rule's own verdict stands. The alert records whether a report was judged and, if so, what the rule
alone said.

## Restarts

The Guard Bee keeps no store of its own. On its first round after a start it rebuilds its windows
from the trail, and restores what it already reported and filed from its own `guard.alert`
events. A burst that straddles a restart is found (its first half is still on the trail), and
nothing is filed twice. The trail is the durable record; a second store would be a copy of it that
could drift.

## Events that arrive late

A Virtual Cell's Warden records into its own trail segment and ships it to the Queen with each
heartbeat (every 15 s by default). Its events therefore reach the central trail up to one
heartbeat after they happen, stamped with the time they happened. Each round reads again from
`LATE_LAG_S` (120 s) before the newest time it has seen, keeping a read position per node, so a
segment that lands late, even a node's first, is still counted, and an event already counted is
never counted twice. A finding on a Virtual Cell comes up to one heartbeat and one round after
the events behind it.

## Where a raised audit rate takes effect

Every Warden's auditing Capping gate samples a terminal proposal at the highest of its tier
table's rate, the live raise it reads from its own trail, and the live raises the Queen carried to
it. The Hive Stand's Warden records to the Queen's trail, so a raise applies there from the next
proposal. A Virtual Cell's Warden records to its own local segment, so the Queen carries the raises
to it: every `GrantIssued` she sends a Warden holds `audit_raises` (Waggle 1.8), the highest live
raise per tier, by tier name, with its expiry. The Warden keeps the highest live one per tier from
every grant it receives, and a raise applies there from its next grant. A tier the Warden does not
know is ignored. A Warden inside a Virtual Cell has no model-backed judge of its own yet, so a
sample it draws is recorded as inconclusive (`capping.audited` with `judge_error`): the raise
decides how much is sampled there, and nothing is judged until an in-Cell judge exists.

## Settings

| Key | Default | Meaning |
|---|---|---|
| `[guard] request_confidence` | `"high"` | The floor a request must reach to be filed. |
| `[guard] requests_per_hour` | `6` | The most requests filed in any hour. A `critical` request is always filed. |
| `[guard] dire_patterns` | `["injection_then_denial", "envelope_forgery", "segment_forgery"]` | The rules whose requests the Queen decides by rule, with no model ([isolation](isolation.md)). |
| `[guard.bee] interval_s` | `5.0` | Seconds between two readings of the trail. |
| `[guard.bee] coalesce_window_s` | `900.0` | One request per rule and target in this window (0 files every one). |
| `[guard.bee] judge_timeout_s` | `60.0` | The longest one awake episode may take. |
| `[guard.bee] audit_raise_step` | `0.25` | How far one raise lifts a tier's sampled-audit rate. |
| `[guard.bee] audit_raise_hold_s` | `86400.0` | How long a raise lasts. |
| `[guard.bee.rules.<key>]` | none | `enabled`, `window_s`, `threshold`, `ratio`, `confidence`, `action`, `judgement` for one shipped rule. What a rule counts cannot be overridden. |

An unknown rule key, or an override that leaves an invalid rule (a narrowing action with
judgement, a ratio on a count rule), is refused when the Guard Bee is built.
