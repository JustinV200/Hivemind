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
| `facts.py` | `TrailFact` (ids, kind, matched fields and a cost; never content) and `EpisodeIndex`, the joins between ids other events carry. |
| `watch.py` | `TrailWatch`: the windows, rebuilt on start and followed per node. |
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

Following is per node. A Virtual Cell Warden's segment is merged late, carrying times older than
events already read, so besides one read of everything since the newest time seen, the watch keeps
a read position per remote node and reads a node seen for the first time back over the whole
horizon.

The order of a report is fixed: act (file, raise or order), deposit, then record the alert that
marks the finding reported. A crash in between repeats an action rather than losing one.

## What it does with a finding

| Action | Who acts | What happens |
|---|---|---|
| `raise_audit_rate` | the Guard Bee | `guard.audit_rate_raised`: the tier's rate in force, raised by `audit_raise_step`, capped at 1.0, for `audit_raise_hold_s`. |
| `reduce_entrance` | the Guard Bee | `guard.reduce_ordered`: the Hive Entrance's `ReduceOrderFollower` reduces the Entrance within a poll. |
| `quarantine_bee`, `isolate_cell`, `sting_cut` | the Queen | Filed through her `GuardRequestDoor` when `RequestLedger` admits it; a `guard.alert` otherwise. |
| `observe` | nobody | The `guard.alert` is the record. |

A request is filed only at or above `[guard] request_confidence` (high), one per rule and target
(its Cell, else its first bee or task) per `[guard.bee] coalesce_window_s` (15 minutes), and at
most `[guard] requests_per_hour` (6) in any hour. One held back is still a `guard.alert`, recorded
as `below_floor`, `coalesced` or `capped`. A recommendation whose target the finding does not name
(a quarantine with no bee) falls back to `observe`.

### How a raised audit rate takes effect

`hivemind.wardens.spawn.audited_gate.AuditingCappingGate` samples each terminal proposal at the
higher of its tier table's rate and the live raise it reads back from its own trail
(`hivemind.supervision.capping.raised_audit_rate`), from the next proposal on. The Hive Stand's
Warden records to the Queen's own trail, so a raise takes effect there at once. A Virtual Cell's
Warden records to its own local segment and never sees the central trail, so on a Virtual Cell a
raise does not take effect yet: its gate samples at its tier table's rate. What will carry it is a
Queen-to-Warden Waggle message naming the live raises (an additive, minor-version capping message),
sent on attach and on every raise, which the Warden applies to what its gate reads, plus the same
raises in a Cell's provisioning environment.

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

`hivemind.cli.compose` does not build one yet. A composition root builds it from the Queen's own
parts and hands it to her:

```python
guard_bee = build_guard_bee(
    GuardBeeInputs(
        trail=queen_deps.trail,
        clock=queen_deps.clock,
        identity=CellIdentity(queen_deps.identity.hive_id, queen_deps.identity.node_id, "system"),
        door=queen_guard_request_door,  # The Queen's own GuardRequestDoor (roadmap 10.6a).
        guard=manifest.guard,
        tiers=load_tiers(capping_tiers_path),  # The same table her Wardens' gates use.
        policy=queen_deps.enforcer.policy,
        bound_for=queen_deps.bound_for,
        call_gate=queen_deps.call_gate,  # The Royal Reserve's seats.
        sink=nectar_sink,  # Phase 7; InMemoryGuardReportSink until then.
    )
)
queen = Queen(replace(queen_deps, guard_bee=guard_bee))
...
await guard_bee.aclose()  # After the Queen has stopped: cancels an episode in flight.
```

`tests/builders/guard_bee/queen.py` (`guard_bee_for_queen`) does exactly this for the tests.

## Seams

- **C2 deposits** (`GuardReportSink`): phase 7's Nectar intake. In memory until then.
- **The Queen's door** (`hivemind.guard.GuardRequestDoor`): the Queen's durable door and her
  decision are roadmap step 10.6a.
- **Node integrity**: Cell-gate signature failures, refused segment merges, Waggle replay
  rejection (11.3) and capability reports without re-enrolment (13.4a) become rules once each has
  a trail kind (see the header of `rules.toml`).

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/workers/roles/guard_bee \
    packages/hivemind/tests/unit/queen/ticks/guard_bee \
    packages/hivemind/tests/e2e/test_guard_bee_on_hive_stand.py
```

`tests/builders/guard_bee/` builds a Guard Bee over fakes (`make_guard_bee`, `restart_guard_bee`),
seeds the events its rules count as their producers write them (`TrailSeeder`), and seeds each
shipped rule's trail (`SHIPPED_RULE_SEEDERS`). `test_bee_entrance.py` reduces a real Entrance over
uvicorn; the e2e runs a real Drone on a real Hive Stand.
