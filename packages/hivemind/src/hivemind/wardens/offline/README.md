# hivemind.wardens.offline

What a Warden does when it is cut off, and how it catches the Queen up. It applies to a Warden that
dials the Queen from another machine (a colonized device's, ADR-0052) and, for the pause and the
resume alone, to a device's Warden on the Hive Stand whose Cell went away (Level 0). The Hive
Stand's own Warden and every Warden on an in-process link never go OFFLINE. The decisions are in
`docs/adr/0053-nucs-warden-migration-and-offline-operation.md`; codingrules 8.8 and 15 are the
principle (a disconnected Warden keeps working within what it owns, queues what must be reported,
never grows, and is clustered past a limit).

## The link

`ReconnectingQueenLink` is a `Transport` the Warden holds instead of a bare client. While it is up,
every send is forwarded. When it is lost the link reports OFFLINE to its watchers, redials with
capped backoff (0.5 s doubling to 30 s), and catches the Queen up in a fixed order before it
reports ONLINE again:

```
 link lost ──► OFFLINE ──► dial ──► announce ──► send again ──► replay queue ──► ship trail ──► ONLINE
                 ▲  (backoff)   cell.ready        what the last    (in order,       warden.         │
                 │              capacity_report   link may have    one at a time)   reconnected     │
                 │                                swallowed                         + signed segment│
                 └───────────── a link that dies part-way is mended again from the dial ◄───────────┘
                                what was sent in the meantime is queued; the queue is drained a
                                second time under the lock that flips the link, then the held
                                questions go out, then ONLINE: nothing is left behind
```

`receive` never ends before `close`, so a Warden that reads the link waits through an outage
instead of spinning on an exhausted iterator. Once `close` has been called the link dials no more:
the recovery running in the Warden's receive task is told, and ends at its next attempt (closing a
connection it had just made) instead of redialing for a link nobody holds. The dial, the
announcement and the after-reconnect step are injected (`QueenDialer`, `Announcer`,
`AfterReconnect`), so one class serves any remote Warden and a test drives it with scripted ones.

A link whose far end vanished (a cable pulled) is not known to be down until the websocket's
keepalive gives up, 40 s after the last traffic, and every send in that time returns as if it had
arrived. Two things make that good, and both send the stretch before the loss again and let the
Queen's replay window drop what she already held (ADR-0055):

- **The trail** is not shipped from where the last sync left off: `TrailReconnect` rewinds the sync
  by `UNNOTICED` (twice the keepalive) first, and she drops the events she holds by event id.
- **Every other frame that must arrive** is remembered by the link in a bounded ring
  (`waggle.transport.recent`: 256 frames, 4 MiB, nothing from more than `UNNOTICED` before the
  loss) once its send returned, and after a redial, between the announcement and the queue, is sent
  again in the order it was first sent and under the id it had. Which frames is not a second list:
  it is the table below, read once more (`policy.must_arrive`: a frame the table would queue or
  hold). A request keeps its own id here, unlike in the queue: most of what the ring holds was
  heard and answered, and a fresh id would have the Queen answer it twice. What the queue replays
  and the questions held back are remembered the same way, since the new link may be dead too.

Nothing is sent again to a Queen who closed the link with a close frame (she read everything before
it, and a restarting Queen has no replay window left to drop a copy), and nothing that was last
sent more than a minute short of her window ago (`RESEND_HORIZON_S`, 540 s): past it a frame she
had handled and forgotten would be handled twice, so after a long outage the ring lets go. A result
sent in the unnoticed stretch of an outage that long is lost, as it was before; a Nuc whose task
finishes in the forty seconds after the cable is pulled, and which gets its link back within the
window, delivers it.

## What is sent while the link is down

One pure table (`policy.decide_offline`), tested row by row:

| Message | While cut off |
|---|---|
| `task.result`, `task.progress` at a checkpoint or a pause, `supervision.alarm_raised`, `forage.request`, `cell.lease_released`, `honey.nectar_deposit` | QUEUE: durably, in order, sent after the reconnection |
| `supervision.question` | HOLD: in memory, sent after the queue; its sub-bee stays blocked |
| `cell.request` | REFUSE: `OfflineRefusedError` (a `ConnectionLostError`), because an offline Warden cannot grow |
| `supervision.heartbeat`, `cell.heartbeat`, `task.progress` at any other stage | DROP: stale when the link returns |
| `swarm.nuc_promoted` | DROP: the answer to an order the Queen only waits a bounded time for |
| anything the table does not name | REFUSE, as on a dead link |

A queued *event* is replayed under the id it was queued with, so the Queen's replay window drops it
if it had in fact arrived and only its acknowledgement was lost. (`must_arrive` is true of exactly
the QUEUE and HOLD rows of this table: those are the frames sent again after a loss nobody noticed.) A queued *request* (the Warden is
owed an answer, and one to a request the window dropped would never come) goes out in a fresh
envelope, a new id and time, and the Queen deduplicates it by the payload's own durable id.

## The freeze and the way back

`DeviceDeps.outage` (an `Outage`) and, for a Warden that dials the Queen, `DeviceDeps.remote` (the
link and `max_offline_s`) switch this on. Each tick `reconcile` compares the Warden with its link and
its Cell:

- The link goes down: `enter_offline` moves the Warden to OFFLINE (`warden.offline`), leaves every
  shared grant as it is (an expiry while cut off is not a revocation), and tells each sub-bee whose
  model binding is not entirely local to stop at a Handoff. A Nuc's bees keep running. The task of
  each bee it stops files a BINDING `ForageRequest` (`wait.ask_for_binding`: the slot it ran on and
  the grade its tempo asks of a model), which the link queues with the results; the Queen answers
  it on the way back, and the Warden starts the task again whatever she says.
- Past `[supervision] max_offline_s`: `cluster_fully` pauses everything left and joins the existing
  Clustering bookkeeping (CLUSTERED, `warden.clustered`). Those tasks are the Queen's to resume with
  a fresh `task.assign`.
- The link returns: `leave_offline` goes to WATCH if no sub-bee runs (Appendix C's `OFFLINE -> WATCH`)
  or ACTIVE if some do. After `RESUME_SETTLE_S`, so that the Queen speaks first, each paused task
  starts again through the ordinary `handle_assign` from the Handoff its bee wrote, on the same
  attempt. A cancel, a pause or a new attempt from the Queen drops the Warden's own hold.
- A Level 0 device Warden: the swarm gate's `cell_lost` pauses its bees the same way (no OFFLINE).
  A bee blocked in a command cannot hear the order to stop; the command dies with the link, and
  that failure is the cut's doing, so `ticks.lease.wait_out_the_cell` retires the bee and keeps
  the task held (`alarm.handled` with `HOLD_FOR_CELL`) instead of retrying it on a session that is
  gone. `cell_found` waits `RESUME_SETTLE_S`, so that the report the device's dead-man switch
  queued is heard first, then gives the old lease back, leases the device again (a refusal is
  asked again after `RESUME_RETRY_S`) and resumes.

## Why the link went: the diagnostic Drone (roadmap 11.9a)

Retrying with backoff is the floor, and a Warden that can think does more. A Nuc (a colonized
device whose Warden also serves a model of its own) sends one short-lived Drone from its own local
pool with the objective "diagnose and, if possible, restore the link to the Queen at <host>"
(`docs/adr/0056-active-reconnection-diagnostic-drones.md`). `diagnose/` is that search, driven from
`reconcile` on every tick like the rest of this package:

```
 link lost ──► IDLE ──► RUNNING ──► (claim) ──► IDLE ──► ... another Drone, told what the first said
                          │                       │
                          │                       └─► FINISH (attempts spent, or time)
                          └─► VERIFYING (a cause was named; wait up to 45 s for the link) ──► FINISH
 FINISH: record warden.diagnosed, deposit the findings as Nectar (queued while the link is down)
```

- **Who and on what.** Only a Warden that holds a model of its own and ceilings from the Queen: a
  `[llm.slots]` key that serves the WORKER slot, whose whole fallback chain runs on this machine
  and whose model one of its servers is serving right now. A Level 1 Warden with no server sends no
  Drone and records nothing. The Drone is a `TaskAssign` and a `GrantIssued` minted on the Warden
  (`RequestOrigin.WARDEN`, started by `wardens.spawn.spawn_local`), never sent anywhere; it takes no
  slot of the Queen's grant and is bounded by the Cell's `Ceilings.max_sub_bees` instead.
- **What it may do.** Its goal ceiling leaves it commands, files and its one model slot, and its
  tools (`tool:*`, which its role lists that way): it holds no `question:human`, no `net`, no write
  outside scratch, and `tool:*` does not cover `tool:request`, so it can ask nobody, reach no
  network of its own, keep nothing and request no tool. The Warden answers its Honey queries empty
  and drops its deposits (`ticks/honey.py`). The Cell's access level and the Capping gate bound the
  rest exactly as for a connected bee. It never touches the Royal Jelly Lab or the Quarantine Comb.
  Its command tool says only whether a command worked, and three failures stop a Drone, so its
  objective tells it to run each check so that the output lands in a file and the command exits 0.
- **The budget.** `[supervision] reconnect_budget` (`attempts`, `wall_clock_s`) is carved out of
  `max_offline_s`, never added to it: the search is over by `wall_clock_s` after the link went, and
  Clustering keeps its deadline as the backstop. Another Drone is sent only after one that named no
  cause. Neither deadline is announced by a frame, so `reconcile` leaves the tick a wake for each
  (`Outage.wake_in`, raced by `wake_future`): a Warden with nothing else to hear would otherwise
  sleep through them to its next heartbeat, and the verdict and the Clustering would come that late.
- **How it ends.** The link, not the Drone's word, decides: FIXED when the link is back and a cause
  was named, DIAGNOSED when a cause is known and the link is still down 45 s later, NO_CAUSE
  otherwise. A Drone that fixed the link is given 30 s after it returns to write up what it did.
  The Drone's closing text opens with one line, `CAUSE: <the cause>` or `CAUSE: none found`.
- **What it leaves.** `warden.diagnosed` on the Warden's trail (outcome, a stable reason, counts)
  and one Nectar document, C2, deposited by the Warden itself for no task.
- **What the Queen never sees.** The Drone's claim and Alarm stay on its row (`SubBee.local`); the
  Warden's heartbeat lists no such child; its task id and grant are not hers.

## Public API (roadmap steps 11.6a, 11.9, 11.9a)

- `ReconnectingQueenLink`, `LinkPhase`; `Reconnection`, `QueenDialer`, `Announcer`, `AfterReconnect`,
  `TrailReconnect`, `WardenRecord`.
- `OfflineAction`, `OfflineRefusedError`, `decide_offline`, `must_arrive`, `owes_reply`;
  `OfflineOutbox`, `open_outbox`.
- `Outage`, `HeldTask`, `RemoteDeps`, `ReconnectBudget`, `QueenLinkView`; `is_link_down`,
  `outage_of`, `remote_of`, `wake_future`.
- `diagnose` (a sub-package): `investigate` (the tick's call), `Verdict`, `Investigation`, the Drone's
  task and grant (`local_assignment`) and the record it leaves (`record_verdict`,
  `deposit_findings`).
- `reconcile`, `enter_offline`, `cluster_fully`, `cut_cell`, `leave_offline`, `RESUME_SETTLE_S`,
  `RESUME_RETRY_S`; `wait.ask_for_binding`, the request a held task files (imported from its
  module, not from this face).

The tick handlers in `hivemind.wardens.ticks` never import this package (it imports them); they
reach an outage through `hivemind.wardens.deps.device_outage`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/wardens/offline -q
```

`test_link_socket.py` runs the link against a real `WebSocketServer` that goes away and comes
back; the other link tests script the Queen in memory (`builders/offline.py`), and
`test_link_resend.py` has that Queen pull the cable without a sound (`ScriptedQueen.silence`) and
states what the link then sends again, in what order and under which ids; the Warden-level
tests run a real Warden on a device's deps (`builders/offline_warden.py`). The search for why the
link went is tested in `diagnose/`: the pure judgement as tables, and whole searches against a Nuc
whose Drone is a script (`builders/diagnose.py`).
