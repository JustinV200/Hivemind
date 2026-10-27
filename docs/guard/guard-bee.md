# The Guard Bee

Roadmap step 10.6, [ADR-0043](../adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md).
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
one attempt at one task: one episode), `task`, `cell`, `grant`, `device`, `address`, `tier`, `node`
(a Waggle peer). A key is read from the event (its subject and the ids its payload carries) or
joined through other events: a Capping proposal's task, Cell and tier from its `capping.proposed`,
a task's bee from `worker.spawned`, its Cell from `queen.assigned`, its grant from
`forage.granted`, a node's Cell from the `warden.spawned` or `warden.reconnected` that bound the
two. Rules count ids and kinds, never content: the trail carries none. Every rule counts only what
the Guard Bee can attribute ([who may speak for what](#who-may-speak-for-what)).

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
| `subject_forgery` | `guard_bee.subject_forged`: a record a Cell's node made about what another Cell owns | cell (the recorder's) | 1 h | 1 | `isolate_cell`, critical | no |
| `envelope_forgery` | `guard.envelope_refused` | cell | 1 h | 1 | `isolate_cell`, critical | no |
| `segment_forgery` | `guard.segment_refused` for `another_node`, `another_cell` or `bad_signature` | cell | 1 h | 1 | `isolate_cell`, critical | no |
| `relay_forgery` | `guard.relay_refused` for `another_cell` | cell | 1 h | 1 | `isolate_cell`, critical | no |
| `task_claim` | `guard.task_refused` for `another_cell` or `unplaced` | cell | 1 h | 1 | `isolate_cell`, high | yes |
| `segment_unmergeable` | `guard.segment_refused` for `format`, `corrupt` or `unsigned` | cell | 1 h | 1 | `isolate_cell`, high | no |
| `frame_ceiling_repeats` | `guard.frame_ceiling`: a connection closed for sending frames past its ceiling | node | 10 min | 3 | `isolate_cell`, high | no |
| `replay_refusal_burst` | `guard.replay_refused`: an envelope id repeated inside the replay window, outside the reconnection grace | node | 5 min | 5 | `isolate_cell`, high | no |
| `request_forgery` | `guard.entrance_login_failed` for `request_signature` or `request_replay` | hive | 10 min | 2 | `reduce_entrance`, high | no |

The Queen's two gates, the Cell gate (`hivemind.queen.cell_gate`, the listener every Virtual Cell's
Warden dials) and the swarm gate (`hivemind.queen.swarm_gate`, which attaches a colonized device's
Warden), serve a Warden's link through one shared path (`hivemind.queen.attach`) and so record the
next three the same way, about the Cell whose own proved link carried the frame:

- **`guard.envelope_refused`**: a frame on an attached link failed its signature (reason
  `invalid`, `missing` or `unknown_node`). The link is closed. A tampered link, or a Cell signing
  as a node it cannot prove.
- **`guard.segment_refused`**: a trail segment the Cell shipped was not merged. `another_node`: a
  chunk named a node or Warden its link never proved (the spec's receiver rule), or carried
  another node's segment, so nothing of it reached the trail. `another_cell`: a chunk named a Cell
  its link never proved; the Queen routes a segment by its Cell (a Night Veil Cell's to its
  ephemeral segment), so it would have reached the wrong side of the Night Veil boundary. A Night
  Veil Cell's own refusals are veiled with the rest of its record (codingrules 12): they reach
  its segment, never the durable trail the Guard Bee reads. `format` and `corrupt`: an unknown
  segment format, or bytes that do not match the size, digest or event count the chunk declared.
  `bad_signature` and `unsigned` (Waggle 1.13, ADR-0053): the final chunk of an export carries an
  Ed25519 signature by the sending node over the segment's identity and digest, and the Queen
  checks it before anything is merged. A Swarm node's segment must be signed by the key the
  registry holds for it: one signed with any other key, or whose signed fields were changed
  afterwards, is `bad_signature`, and one with no signature at all is `unsigned`. A Virtual Cell's
  segment need not be signed, but one that is is verified against the key the readiness gate
  registered for its Cell, so it can only be `bad_signature`. Nothing of a refused export is
  merged, and a partial one is dropped so a retry starts clean. The link stays up: an honest
  Cell's bad export is a fault to hear of, not a reason to cut its Warden off mid-task. The Cell's Warden is answered with a `control.error`
  whose code names the reason (`hivemind.queen.segment_refused.<reason>`), correlated to the
  refused chunk.
- **`guard.relay_refused`**: a snapshot relay request (`cell.snapshot_request`,
  `cell.rollback_request`) named a Cell other than the one its link proved (reason
  `another_cell`). It never reached the Queen's snapshotter and was answered with an error: no Cell
  snapshots or rolls back another.

The Queen records one more about a Warden's Cell herself, in her tick:

- **`guard.task_refused`**: the Queen refused a Warden's message about a task that is not placed
  on that Warden's Cell (a result, a progress report, an Alarm, a question or an answer; reason
  `another_cell` when the task is placed on another Cell, `unplaced` when it is placed nowhere),
  so it was never decided or applied: no Cell finishes, fails, blocks or answers for another
  Cell's task. The event names the sending Cell and Warden and the message's kind, never the
  task, so it never drags another Cell's record into the sender's. A stale report about a task
  that already ended stays a quiet no-op, as it always was.

Each reason is recorded once per link (the Queen's task refusals once per Warden and message
kind), so no Cell can flood the trail. `subject_forgery`, `envelope_forgery`, `segment_forgery` and
`relay_forgery` are shipped dire patterns: the Queen isolates the Cell by rule. `task_claim` is
judged awake, since a Warden whose link dropped while its task was placed elsewhere can report it
late, honestly.

A frame that fails its signature before its link is attached proves no Cell (whoever dialled may
have named any Cell's id), so the gate closes that connection and records
**`guard.handshake_refused`** about the Hive, never about a Cell: the reason, the peer's address,
and what the frame claimed kept apart as `claimed_cell_id` and `claimed_node_id` (each only when
well formed). At most ten are recorded a minute; the knocks skipped in between are counted on the
next record (`suppressed`). No shipped rule acts on it, on purpose: a rule that isolated the
claimed Cell would let anyone frame an honest Cell by naming it.

The Guard Bee learns which node speaks for which Cell from the Queen's own `warden.spawned`, and,
since a Warden can come back on a link that proves another node (it moves onto its device when it
is colonized, ADR-0052), from `warden.reconnected` as well. Both are believed from the Hive Stand's
node alone, so a node cannot claim a Cell by recording either itself.

`request_forgery` is someone holding a session token without its device's key.

The last two rows count what the Hive Stand's own listeners (the Cell listener and the swarm
listener, through one `TransportRefusals`) record of a Waggle peer, ADR-0055. A connection that
sends more frames a minute than its ceiling is closed with code 4029 and recorded as
`guard.frame_ceiling`; the ceiling is lax on purpose (a hundred frames a second), so one close can
be a bug, and three in ten minutes from one node is a peer that means to flood. An envelope id the
listener already handled is dropped unhandled; a duplicate shortly after the node's Warden
reconnected is its outbox replaying and is silent, and any other is `guard.replay_refused`, so five
in five minutes is someone replaying a captured frame. Both name the node, and the rules count them
per node: the same refusals spread over several nodes are nobody's pattern. A refusal on a
connection that had named no node yet is recorded against the Hive and counted against no one. The
node's Cell is the one the Queen's `warden.spawned` or `warden.reconnected` bound it to, and then
the request names that Cell; a node nothing binds, such as a device whose Warden still runs on the
Hive Stand, is only observed. Only the Hive Stand's node can make these counts: a Cell's own node
writing them about another node names no Cell it owns, so it is not counted. Isolation keeps the
socket open and only a Sting Cut invalidates the node's keys, so repeated frame-ceiling closes are a
dire pattern for 13.4a to list; today the Queen decides the request like any other. A capability
report that changed without re-enrolment (13.4a) becomes a rule once something records it.

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

## Who may speak for what

A Virtual Cell's Warden ships its own trail segment, so a compromised Cell can record anything on
its own node, naming any bee, task or Cell: the injection correlation about another Cell's bee,
say, which would have that Cell isolated by rule. So the Guard Bee attributes every event before
a rule may count it.

It learns who owns what from the Queen's own records, and only those her own node recorded:
`warden.spawned` names each Warden's Cell and the node its link proved (the Cell gate's handshake,
a device's, or the Hive Stand's own process), `warden.reconnected` the same for a Warden that came
back on a link proving another node, `queen.assigned` every Cell a task was placed on, and
`forage.granted` a grant's task. A bee is owned through the `worker.spawned` its own Cell's node
recorded, and a Capping proposal through its `capping.proposed`, each only when the task it names
was placed on that Cell. Then an event counts when:

- the Hive Stand's node recorded it; or
- a Cell's node recorded it, and that Cell owns everything it names: the Cell itself, its Warden,
  the bees it spawned, the tasks placed on it, their grants, its proposals.

An event a Cell's node recorded about what another Cell owns is **forged**. It never counts as
what it says; instead it is evidence about the recording node's Cell, the derived kind
`guard_bee.subject_forged` that `subject_forgery` counts. The report names the framer and cites
the forged events, and never names the bee or task they were about, so the Queen isolates the
framer and never the Cell it framed. An event nothing can attribute yet (a node no Cell speaks
for, a bee no Cell's claim holds) is not counted either way. A bee two Cells both claim counts for
neither, since nothing on the trail says which claim is true. A Cell's event joins only through
its own Cell's claims, so a report built from it names that Cell's own tasks and Cell. Ownership is
kept for a week (a proposal for a day), and every node's Cell for as long as the Hive runs.

## Night Veil Cells

Everything about a living Night Veil Cell (its Warden's shipped segments, the Queen's own records
about it) lives only in that Cell's ephemeral segment on the Queen's side, and the Queen's trail,
read through the boundary, shows only the durable trail. So each round also reads every held
segment, with a cursor of its own per Cell, and keeps what it reads in memory only. Its alert about
such a Cell is recorded through the same boundary, so it stays in the segment and is purged with
the Cell; its request goes through the Queen's door as for any Cell, and she isolates or shows it
as usual. Once a segment is taken at teardown, every fact the Guard Bee read from it is forgotten.

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
to it: every `GrantIssued` she sends a Warden holds `audit_raises` (Waggle 1.10), the highest live
raise per tier, by tier name, with its expiry. The Warden keeps the highest live one per tier from
every grant it receives, and a raise applies there from its next grant. A tier the Warden does not
know is ignored. A Warden inside a Virtual Cell has the model-backed judge its Cell's slot table
binds, so a sample it draws is judged there, on a lane of the Cell's own Fanner. A table that
binds no judge records the sample as inconclusive (`capping.audited` with `judge_error`).

## Settings

| Key | Default | Meaning |
|---|---|---|
| `[guard] request_confidence` | `"high"` | The floor a request must reach to be filed. |
| `[guard] requests_per_hour` | `6` | The most requests filed in any hour. A `critical` request is always filed. |
| `[guard] dire_patterns` | `["injection_then_denial", "envelope_forgery", "segment_forgery", "relay_forgery", "subject_forgery"]` | The rules whose requests the Queen decides by rule, with no model ([isolation](isolation.md)). |
| `[guard.bee] interval_s` | `5.0` | Seconds between two readings of the trail. |
| `[guard.bee] coalesce_window_s` | `900.0` | One request per rule and target in this window (0 files every one). |
| `[guard.bee] judge_timeout_s` | `60.0` | The longest one awake episode may take. |
| `[guard.bee] audit_raise_step` | `0.25` | How far one raise lifts a tier's sampled-audit rate. |
| `[guard.bee] audit_raise_hold_s` | `86400.0` | How long a raise lasts. |
| `[guard.bee.rules.<key>]` | none | `enabled`, `window_s`, `threshold`, `ratio`, `confidence`, `action`, `judgement` for one shipped rule. What a rule counts cannot be overridden. |

An unknown rule key, or an override that leaves an invalid rule (a narrowing action with
judgement, a ratio on a count rule), is refused when the Guard Bee is built.
