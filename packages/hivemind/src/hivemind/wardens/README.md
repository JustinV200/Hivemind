# hivemind.wardens

The wardens package is the Warden: the per-Cell supervisor that spawns and supervises Workers on
exactly one Cell. It covers Warden state, its inbox, its Autopilot (which never awaits a model)
and Awake modes, spawning, its local model pool, offline handling and read-only Watch mode; a
Warden never provisions Cells itself.

## Public API (roadmap steps 3.18, 3.19)

- `Warden` (`warden.py`): the `waggle.loop.TickLoop` face. `start()` leases the Warden's Cell (or
  moves to `WATCH` on a refusal); `stop()` sets the stop flag first, then reaps its own heartbeat
  deadline, stops every sub-bee (cooperatively, via `wardens.spawn.stop_sub_bee`, falling back to
  a bounded cancel), reaps every receive task it owns and releases the lease -- never returning
  with a task it started still pending (codingrules section 11). One tick drains the queen link
  and every sub-bee link into `InboxItem`s, orders them with this Warden's own `Attendant`, dispatches each
  through `wardens.autopilot.decide` (falling back to `wardens.awake.decide_awake` for
  `NEEDS_JUDGEMENT`), and sends a `Heartbeat` once the interval elapses -- a send that finds the
  Queen link already closed is recoverable (`wardens.ticks.heartbeat.send_heartbeat` records
  `warden.offline` and moves on), never an exception out of this Warden's own loop or `stop()`.
  Also implements `hivemind.supervision.Supervisor` over its sub-bees.
- `WardenState`, `TRANSITIONS`, `assert_transition`, `can_transition`, `is_terminal`
  (`state.py`): the one Warden state machine (Appendix C).
- `WardenDeps` (`deps.py`): every collaborator one Warden is built with.
- `AcceptanceReport`, `run_acceptance` (`acceptance.py`): the Warden-side half of a task's
  acceptance criteria (roadmap 3.18) -- run on the Warden's own session, never the sub-bee's.
- `cell_request`, `forage_request`, `tool_request` (`requests.py`): the three requests a Warden
  sends the Queen when it needs something beyond its grant.
- `wardens.autopilot`: `WardenAction`, `SubBeeView`, `decide` -- the deterministic dispatch table;
  never imports `hivemind.llm`.
- `wardens.awake`: `WardenDecision`, `decide_awake` -- one stateless episode on `ModelSlot.WARDEN`.
- `wardens.spawn`: `SubBee`, `WardenCellContext`, `spawn_sub_bee` -- starting a new sub-bee.
- `wardens.inbox`: `to_inbox_item`, `warden_attendant` -- the Warden's own Attendant.
- `wardens.local_pool`: `LocalPool` -- a bare sub-bee-slot counter against a grant.
- `wardens.ticks`: `assign`, `results`, `alarms`, `questions`, `control`, `heartbeat` -- one of
  `Warden`'s own tick handlers each, split out only to stay within codingrules 5.1's size limits.
  `alarms.handle_alarm_action`/`rebind_sub_bee` also record `alarm.handled`/`alarm.escalated` on
  the Pheromone Trail (`hivemind.supervision.record_alarm_event`); `control.forward_control` turns
  a Queen-sent `Intervene(REBIND)` into a real respawn on the binding the Queen already resolved,
  rather than only relaying it to the sub-bee to checkpoint and stop.
- `wardens.offline`, `wardens.watch`: placeholders; populated in phase 11.

## How to test this

Every module is tested against fakes: `hivemind.cell.fake.FakeCellSource`, `waggle.transport.
memory.MemoryTransport.pair`, `hivemind.memory.InMemoryMemoryStore`, `hivemind.pheromone.trail.
memory.MemoryPheromoneTrail`, `waggle.clock.FakeClock`, the shipped `docs/supervision/
default-policy.toml` and `capping-tiers.toml`, and a scriptable `hivemind.llm.FakeLLMProvider` on
`ModelSlot.WARDEN`. `tests/builders/wardens.py`'s `make_warden_deps` builds all of that in one
call; `QueenEnd` wraps the Queen side of the link the same way `tests/builders/workers.py`'s
`WardenEnd` wraps a Warden's own link to a sub-bee, so a test drives a real `Warden` end to end
over real (in-memory) Waggle links, never by calling its private methods directly.

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens -q
```
