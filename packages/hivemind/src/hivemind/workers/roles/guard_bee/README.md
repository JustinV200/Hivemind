# hivemind.workers.roles.guard_bee

The Guard Bee (roadmap step 10.6, [ADR-0035](../../../../../../../docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md)):
the Hive's security watcher. It runs in the Queen's process on the Hive Stand, driven by her tick
beside the House Bee's sweep, and reads the central Pheromone Trail against deterministic rules
shipped as data. Every finding becomes a `GuardReport`, a `guard.alert` and a C2 deposit. It acts
alone only to narrow the whole Hive; anything aimed at one Cell or one bee is a request the Queen
decides on. The rules, one by one, are in [docs/guard/guard-bee.md](../../../../../../../docs/guard/guard-bee.md).

It never touches a Cell, never widens anything, and never awaits a model inside a round. It holds
the trail, a clock, the Queen's request door, a C2 sink and a judge (a slot resolver and the
Queen's call gate), and nothing else: no Cell, no session, no Waggle link, no grant. For that
reason it is not a `hivemind.workers.Worker`, which is handed a Cell and a session.

## Modules

| Module | What it holds |
|---|---|
| `rules.toml` | The shipped rules, read through `importlib.resources`. |
| `rules.py` | `GuardRule`, `GuardRules`, `Matcher`, `load_guard_rules`: the rule data, checked against the trail's kinds, with `[guard.bee.rules]` overrides applied. |
| `facts.py` | `TrailFact` (ids, kind, matched fields and a cost; never content) and `EpisodeIndex`, the joins between ids other events carry, and who owns each id: `attribute` says whether a fact counts (`Attribution`). |
| `watch.py` | `TrailWatch`: the windows, rebuilt on start and followed per node, every living Night Veil Cell's segment beside the durable trail, and only attributed facts counted (forged ones as `guard_bee.subject_forged`). |
| `evaluate.py` | `evaluate`, the pure decision: count, ratio and sequence rules, per key, past what was already reported. |
| `judge.py`, `lane.py` | The awake episode on the judge slot (`ModelGuardJudge`) and the lane it runs in beside the tick (`JudgeLane`). |
| `requests.py` | `RequestLedger`: the confidence floor, coalescing and the hourly cap. |
| `respond.py` | `FindingResponder`: build the report, act, deposit, record the alert. |
| `sink.py` | `GuardReportSink`, the C2 seam, and `InMemoryGuardReportSink` until phase 7. |
| `bee.py` | `GuardBee` (one round per due tick) and `build_guard_bee`. |

## One round

At most every `[guard.bee] interval_s` (5 s), when the Queen's tick asks
(`hivemind.queen.ticks.guard_bee.run_guard_bee`):

1. **Read** what is new on the trail (`TrailWatch.read`).
2. **Restore**, on the first round after a start, what was already reported and filed from the
   Guard Bee's own `guard.alert` events.
3. **Reap** the verdict of an awake episode that finished since the last round.
4. **Evaluate** every enabled rule. A finding already queued for an episode waits for it.
5. **Report** each finding on its rule's own verdict, or queue it for an episode when the rule
   asks for judgement and the lane has room.
6. **Start** the next queued episode, beside the tick.

A round that fails raises one `GuardBeeError`; the Queen's tick logs it and carries on, and the
next due round tries again.

## Rebuilt windows, not a store of its own

The windows are rebuilt from the trail on the first round after every start, kind by kind over
the longest window any rule counts that kind in (a day for the kinds the episode index joins
through). The trail is already the durable record of every event a rule counts, and of every
report filed: each `guard.alert` names the rule, the key, the moment the finding counted up to and
the ids at that moment, and what became of it. So a restart loses nothing a second store would
have kept:

- **A burst that straddles a restart is still found.** Its first half is still on the trail and
  back in the window when the second half arrives.
- **Nothing is filed twice.** The rebuild returns the Guard Bee's own alerts (only its own node's:
  an alert merged from another node cannot make it believe it filed something); they restore what
  each rule and key was reported through, and the request ledger's coalescing and cap.

A store of its own would only be a copy of the trail that could drift from it.

Following is per node. A Virtual Cell Warden's segment is merged late (it ships on every
heartbeat, 15 s by default), carrying times older than events already read, so besides one read of
everything since `LATE_LAG_S` (120 s) before the newest time seen, the watch keeps a read position
per remote node and reads a node seen for the first time back over the whole horizon. The lag is
what hears a node's first segment at all: landing after the Hive Stand has moved on, it may hold
nothing newer than what was already read. A finding on a Virtual Cell therefore comes up to one
heartbeat and one round after the events behind it.

Attribution. A Virtual Cell's Warden ships its own trail segment, so a compromised Cell can
record anything on its own node. The index learns who owns what from the Queen's records alone
(`warden.spawned` names each Warden's Cell and the node its link proved; `queen.assigned` each Cell
a task was placed on; `forage.granted` a grant's task) and a bee or proposal through the claim its
own Cell's node made. `facts_of` returns only facts the Hive Stand recorded or whose node's Cell
owns all they name; one naming what another Cell owns is returned instead under the derived kind
`guard_bee.subject_forged`, about the recording node's Cell alone, which `subject_forgery` counts.

Night Veil. The Queen's trail, read through the boundary, shows only the durable trail, so each
round also follows every held Night Veil segment (`segments_of(trail)`, a cursor per Cell), keeps
what it reads in memory only, and forgets it once the segment is taken at teardown. Alerts go
through the same boundary, so an alert about a Night Veil Cell stays in its segment.

The order of a report is fixed: act (file, raise or order), show it to the human if it is
CRITICAL, deposit, then record the alert that marks the finding reported. A crash in between
repeats an action rather than losing one; the door shows one report at most once.

## What it does with a finding

| Action | Who acts | What happens |
|---|---|---|
| `raise_audit_rate` | the Guard Bee | `guard.audit_rate_raised`: the tier's rate in force, raised by `audit_raise_step`, capped at 1.0, for `audit_raise_hold_s`. |
| `reduce_entrance` | the Guard Bee | `guard.reduce_ordered`: the Hive Entrance's `ReduceOrderFollower` reduces the Entrance within a poll. |
| `quarantine_bee`, `isolate_cell`, `sting_cut` | the Queen | Filed through her `GuardRequestDoor` when `RequestLedger` admits it; a `guard.alert` otherwise. |
| `observe` | nobody | The `guard.alert` is the record. |

A request is filed only at or above `[guard] request_confidence` (high), one per rule and target
(its Cell, else its first bee or task) per `[guard.bee] coalesce_window_s` (15 minutes), and at
most `[guard] requests_per_hour` (6) in any hour. A CRITICAL request is always filed: neither
coalescing nor the cap holds it back. One held back is still a `guard.alert`, recorded as
`below_floor`, `coalesced` or `capped`. A recommendation whose target the finding does not name
(a quarantine with no bee) falls back to `observe`.

A CRITICAL report that is not a request (a raise, a reduction, an observation) is shown to the
human at once through the door's `report_to_human`, a CRITICAL SECURITY Alarm naming it; the alert
records `shown`. A CRITICAL request is filed instead, and the Queen shows it when she decides.

### How a raised audit rate takes effect

`hivemind.wardens.spawn.audited_gate.AuditingCappingGate` samples each terminal proposal at the
highest of its tier table's rate, the live raise it reads back from its own trail
(`hivemind.supervision.capping.raised_audit_rate`) and the live raise the Queen carried to its
Warden, from the next proposal on. The Hive Stand's Warden records to the Queen's own trail, so a
raise takes effect there at once. A Virtual Cell's Warden records to its own local segment and never
sees the central trail, so the Queen carries the raises to it: every `GrantIssued` holds
`audit_raises` (Waggle 1.8), the highest live raise per tier (`live_audit_raises`), by tier name,
with its expiry. The Warden keeps the highest live one per tier (`CarriedAuditRaises`), ignoring a
tier it does not know, so a raise applies there from its next grant. An in-Cell Warden has no
model-backed judge yet, so each sample it draws is recorded as inconclusive (`judge_error`).

## Awake episodes

A rule with `judgement = true` hands each finding to one awake episode on `ModelSlot.JUDGE`, with
the `guard_review` prompt and the report's facts only (ids, counts, the rule's verdict and the
actions its targets allow), never the conversation or any content. The episode may raise or lower
the confidence and change the action, within what the finding's targets allow; a rule that
narrows the whole Hive never takes judgement. Episodes run one at a time in `JudgeLane`, beside
the Queen's tick, so a slow model never delays her heartbeat; each is bounded by `[guard.bee]
judge_timeout_s`. The call goes through the Queen's own call gate with no grant, so it is charged
to the Royal Reserve's seats, as the House Bee's compaction is. When the model fails, times out,
answers out of bounds or the slot is unbound, the rule's own verdict stands. The lane is built only
while the Guard policy's `guard_bee` role holds `llm:judge`.

## Wiring it in (the composition root)

`hivemind.cli.compose.guard.with_guard` builds it for every Hive `hive run` and `hive serve`
compose, from the Queen's own parts, and hands it to her on `QueenDeps.guard_bee`:

```python
queen_deps, door = with_guard(manifest, queen_deps, lifecycle, warden_deps.tiers)
queen = Queen(queen_deps)
door.bind(queen)  # Before anything ticks: she is the Guard Bee's door (Hive.guard_door).
...
await close_guard_bee(queen_deps)  # After the Queen has stopped: cancels an episode in flight.
```

The Guard Bee rides on the Queen's own deps, so it is built before she is; its door is a
`GuardDoorRelay` the composition root binds to her the moment she exists, which forwards both
halves of the door and refuses a call made before it was bound. Its inputs are the Queen's trail,
clock, node, Guard policy, slot resolver and call gate (the Royal Reserve), the manifest's
`[guard]` section and the same tier table her Wardens' gates use. `tests/builders/guard_bee/queen.py`
(`guard_bee_for_queen`) builds the same from a test's Queen.

## Seams

- **C2 deposits** (`GuardReportSink`): phase 7's Nectar intake. In memory until then.
- **Node integrity**: the Cell gate records a frame that failed its signature on a Cell's own link
  (`guard.envelope_refused`) and a trail segment it would not merge (`guard.segment_refused`),
  in `hivemind.queen.cell_gate.refusals`; three rules count them, and `subject_forgery` counts a
  Cell's node recording what another Cell owns. Waggle replay refusal (11.3b),
  frame-ceiling closes (11.3a), a merged segment whose events fail the node's key (11.9) and
  capability reports without re-enrolment (13.4a) become rules once each has a trail kind (see the
  header of `rules.toml`).

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/workers/roles/guard_bee \
    packages/hivemind/tests/unit/queen/ticks/guard_bee \
    packages/hivemind/tests/unit/queen/cell_gate \
    packages/hivemind/tests/unit/cli/compose/test_guard_bee.py \
    packages/hivemind/tests/e2e/test_guard_bee_wiring.py \
    packages/hivemind/tests/e2e/test_guard_bee_on_hive_stand.py \
    packages/hivemind/tests/e2e/test_guard_bee_on_virtual_cell.py \
    packages/hivemind/tests/e2e/test_guard_bee_framing.py \
    packages/hivemind/tests/e2e/test_guard_bee_on_night_veil.py \
    packages/hivemind/tests/e2e/test_audit_raise_on_virtual_cell.py
```

`tests/builders/guard_bee/` builds a Guard Bee over fakes (`make_guard_bee`, `restart_guard_bee`),
seeds the events its rules count as their producers write them (`TrailSeeder`), and seeds each
shipped rule's trail (`SHIPPED_RULE_SEEDERS`). `test_bee_entrance.py` reduces a real Entrance over
uvicorn, once per Entrance door rule. `test_listener.py` sends a forged segment over a real socket
and the running Queen isolates the Cell on the Guard Bee's request. The e2e tests compose the Hive
as `hive run` does: a lured Drone on the Hive Stand (the fallback), in a Virtual Cell and in a
Night Veil Cell (isolation by rule; the Night Veil alert purged with the Cell), one Virtual Cell
framing another's bee (the framer isolated, the framed never), the judge's calls on the Royal
Reserve, and a raise sampling every proposal inside a Virtual Cell. `test_attribution.py` covers
attribution over one Guard Bee round, `test_watch_veiled.py` the Night Veil segments.
