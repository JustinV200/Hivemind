# hivemind.wardens.ticks

The ticks package holds `Warden`'s own tick handlers: what each `WardenAction` actually does,
split out of `warden.py` only to stay within codingrules 5.1's size limits (`dispatch.act` hands
every decided action to one of these; none of them is a general-purpose module on its own).

## Public API (roadmap steps 3.19, 7.8)

- `dispatch`: `act(warden, action, item, sub_bee, binding)`, the one call the Warden's tick makes
  once an item is decided; routes the action to the handler below that does it (the Warden's own
  former `_act`, moved here for `warden.py`'s size limit).
- `honey` (roadmap step 7.8): `handle_honey_item` relays the Honey Store's traffic. A sub-bee's
  `HoneyQuery` or `NectarDeposit` goes up to the Queen in a fresh envelope, payload unchanged, only
  when it names that sub-bee and its own task (the first-hop identity rule; a refused query is
  answered at once with an empty `HoneyResponse`). `HoneyRelay` remembers each forwarded query's
  asker and original envelope id (at most `MAX_PENDING_HONEY_QUERIES`, oldest dropped first) so
  the Queen's `HoneyResponse` is relayed back correlated to the sub-bee's own envelope; an
  unmatched response is logged, and a Queen `control.error` about a relayed deposit is logged at
  warning. A closed link never ends the Warden's tick: with no Queen to forward to, a query is
  answered at once (`QUEEN_UNREACHABLE_REASON`) and a chunk is logged and dropped. A bee the
  Warden started for itself (`SubBee.local`, roadmap 11.9a) has no task of the Queen's: its query is
  answered at once, empty (`LOCAL_RUN_REASON`), and its chunk dropped, so neither is forwarded.
- `assign`: spawn a sub-bee once its `TaskAssign` and `GrantIssued` have both arrived; park
  otherwise; retry a refused lease once before escalating `CELL_UNREACHABLE`. Every `TaskAssign`
  is a new attempt, so it first supersedes whatever the Warden still holds for the task (the FAILED
  row an escalated crash left, a bee the Queen retried or resumed): retired, its slot freed.
- `results`: run acceptance on a sub-bee's claimed `TaskResult`, on the Warden's own session. A
  device error while checking (`DEVICE_ERRORS`: a closed session, a refused request, a lost link)
  reports the task FAILED with the error's class and never ends the Warden's loop. A claim from a
  bee the Warden started for itself (`SubBee.local`, roadmap 11.9a) is kept on its row instead.
  Once the machine checks pass, a Warden built with a Nurse Bee (ADR-0061) asks it: PASS, or no
  Nurse, reports SUCCEEDED with the review; CHANGES_REQUESTED reports FAILED with it; a
  `JUDGE_RUBRIC` criterion holds only on a PASS.
- `trail_ship`: ship this Warden's own trail segment right before a `TaskResult` goes to the
  Queen, so a Cell paused or destroyed on that result never takes the task's rows with it;
  called by `results` and `alarms`.
- `alarms`: `RETRY`/`REBIND`/`ESCALATE`/`CANCEL_TASK` for an Alarm; `send_alarm_to_queen` for a
  Warden's own self-raised Alarm; `retire_sub_bee`, the one way a sub-bee leaves its Warden (its
  runtime stopped, its link closed, its slot freed), shared with `results`, `control`,
  `heartbeat`, `Warden.stop` and the quarantine path; a respawn keeps the slot for the fresh bee
  (`keep_slot`). `resume_from_handoff`: a bee stopped at a Handoff its own Warden ordered is
  followed by a fresh bee resuming the same attempt from that Handoff, in the same slot. A fresh
  bee on a new attempt number (a respawn, a rebind, a Rebind or Takeover's recovery) is reported
  to the Queen on a `TaskProgress(STARTED)`, so her next retry numbers past it. An Alarm from a bee
  the Warden started for itself is kept on its row, and such a bee takes no slot.
- `lease` (roadmap step 10.3): `open_lease`, `Warden.start`'s delegate, behind the
  `lease_creation` point. `release_lease` is the one way a lease is given back (the idle release,
  and the offline return that leases a device again); `report_cell_lost`/`clear_cell_lost` also
  wake the tick of a Warden with an `Outage` (roadmap 11.9).
- `questions`: forward a sub-bee's `Question` to the Queen and the Queen's `Answer` back; since
  roadmap step 10.3 only when the sub-bee holds `question:human` (the `question_routing` point).
- `control`: forward `TaskCancel`/`TaskPause`/`TaskResume`/`Intervene` from the Queen unchanged;
  a cancel marks the bee's row so any terminal state it reports next ends it, a cancel for a bee
  that has already ended retires it at once, and `RELEASE_LEASE` retires every sub-bee. A Queen
  pause (`TaskPause`, `Intervene(HANDOFF)`) is hers to resume, so it withdraws any successor the
  Warden had promised itself; the Warden's own Handoff, Rebind and Takeover levers each promise one
  (the same binding and attempt, the lever's slot, the Warden's own slot; the last two as a new
  attempt). A Queen cancel, pause or resume of a task also drops any hold a device Warden's outage
  had on it (her word wins, roadmap 11.9). `handle_source_evict` (roadmap step 8.3) gives back
  seats the Queen exported: it withdraws the
  named model source only when this Warden hosts it and its ceilings let it export seats, and
  ignores the order (logged) otherwise; `RELEASE_LEASE` also stops any model server the Warden
  started, since it would die with the lease.
- `nuc` (roadmap step 11.8, a sub-package): `handle_nuc_promote` accepts a `swarm.nuc_promote`
  from the Queen's link only and starts a job the Warden owns (`basket.jobs.start_job`, reaped by
  `Warden.stop`), because a model server takes seconds to turn healthy and no tick waits.
  `run_order` is the job: PROMOTE starts the named server (one the device's manifest declares)
  through `ModelServerHost.start(only=initial_models, deadline_s=...)` within the ceilings, failing
  closed without them, leasing the Cell again if the idle release gave it back; DEMOTE stops every
  server. `reply` builds and sends `swarm.nuc_promoted`, correlated to the order, with the rung
  the device is on afterwards. `lease.release_idle_lease` no longer releases a lease while a job is
  in flight or a model server runs.
- `heartbeat`: send the Warden's own `Heartbeat`, mirror a sub-bee's reports (retiring one whose
  Heartbeat says it has ended with nothing more to send, leaving its Handoff with the outage if the
  outage paused it, or starting its successor when the Warden's own context check had ordered it
  to hand off; a compact is ordered once per crossing, since a bee reports its context every model
  round), watch for a stall,
  and build the `HotStateSources` an awake episode reads -- including `wax(cells)` (roadmap step
  4.2a), WRITTEN Cell Wax for the given Cells, capped per Cell the same way
  `hivemind.queen.awake.episode.QueenSources.wax` is.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens -q
```
