# ADR-0062: Requeening reads each fact from its own source of truth

- Status: Proposed
- Date: 2026-10-27

## Context

The Queen is one process. When it dies without its teardown (a crash, a kill, a power cut), her
in-memory books die with it: which Wardens are attached, which provider is clustered, which tasks
she paused for Clustering, the wire ids of the questions she forwarded, and the replay window
that drops a frame she has already handled. The Hive Stand's own Warden runs in her process and
dies too, and so does a Level 0 device's Warden, which the swarm gate runs beside her. What
outlives her is on disk or elsewhere: the Brood Chamber, the Pheromone Trail, the Forage ledger
(ADR-0023), the Virtual Cells a backend still runs, and any Warden that runs in a Cell of its own
or on a colonized device.

Before this decision a restarted Queen read the stores back but reconciled almost nothing. A task
left RUNNING on the dead Hive Stand Warden stayed RUNNING for good; its lease's scratch stayed on
disk with no `cell.released`; a task she had paused for Clustering stayed PAUSED, because the
Clustering state that would resume it lived in memory; a Virtual Cell's Warden, whose link was a
bare socket, ended when her socket closed, and could not have come back anyway, because the
Cell's public key lived only in the readiness gate's memory. And an Alarm or a question a Warden
sent again after the restart (phase 11's wave 4G re-sends what a dead link may have swallowed) was
handled twice, because her replay window started empty.

Roadmap 13.1 asks for Requeening: rebuild from the Brood Chamber and trail, reconcile backends and
`RealCellSource`, reattach live Wardens, replay their outboxes, reconcile grants against the
ledger, release leases whose Warden is gone, reassign orphans, resume PAUSED tasks from Handoffs,
and record `queen.requeened`. Roadmap 13.2 asks for a periodic snapshot of the Queen's own state,
so recovery is bounded.

## Decision

**Every start is a Requeening.** `run_hive` starts the Queen in `REQUEENING` (Appendix C: "A new
Queen starts in `REQUEENING`") and runs `hivemind.queen.requeening` before her loop. A first start,
or a start after a clean stop, finds nothing to do and is `RUNNING` within one tick, so there is
one start path, not a crash path beside a normal one. Her tick runs throughout, so a Warden that
comes back is heard at once. A provider that goes down while she is `REQUEENING` clusters her as
it would at any other time: Appendix C gains `REQUEENING -> CLUSTERED`, recovery still settles,
and she stays `CLUSTERED` until the provider answers.

**Sources of truth, one per fact.**

| Fact | Source of truth | What Requeening does with it |
|---|---|---|
| Tasks, their placement, questions | Brood Chamber | Read; every task placed on a Warden that is not back is an orphan. |
| Virtual Cells | Backend labels (`list_cells`) | Already reconciled (5.6). The Cell's public key now rides in a label too (`hivemind.cell_key`), so the readiness gate is re-armed and the Cell's Warden can prove itself again. A Cell still running a task is walked back to GRANTED in its lifecycle (its backend can only say READY), so the release at its task's end has its edge. |
| The Hive Stand | Its Cell id (derived from the node id, `hive_stand_cell_id`) and the trail's `cell.leased` / `cell.released` | A lease opened and never released is an orphan once its Warden is gone: its scratch is removed and `cell.released` recorded with `requeened = true`. Only the Queen's own node's events about that Cell are read, and only a well-formed lease id names a directory, so a merged segment can never point the removal anywhere else. |
| Swarm devices | The swarm registry and the gate (ADR-0053) | Left alone. The gate already holds a lost device's tasks for its grace and decides; Requeening never moves a task on a device's Cell. |
| Wardens | Whatever reattaches | A Virtual Cell's Warden now redials with backoff like a device's (every tier, not only Night Veil), and attaches as a returning Warden. The Hive Stand's Warden of the dead process never comes back: its tasks are orphans at once. |
| Outboxes | The Wardens' own | Replayed by each Warden on reconnection (1.8); nothing to do on her side but hear them. |
| Grants | The Forage ledger | A grant whose task has ended is RELEASED; one whose holder is not back when the grace ends is revoked `HOLDER_OFFLINE`. A returning Warden is sent its live grants again (`queen.forage.resend`, already). |
| Clustering pauses | Nowhere durable | A PAUSED task held by neither a quarantine nor an isolation (both are trail-derived and stay as they are) was paused for Clustering. It resumes from its newest Handoff through its Warden when that Warden is back, and is otherwise re-placed with that Handoff as its resume hint. |
| Handled Alarms and questions | A durable handled table | See "the replay window" below. |

**One Queen per Brood Chamber.** Requeening takes back the work of every Warden not attached to
the Queen doing it, so it is only safe when no other Queen is alive on the same chamber. Before
this decision `hive run` (and `hive tools submit`, `hive swarm run`, `hive llm eval`) took no lock,
and beside a running `hive serve` made a second Queen: its Requeening would have taken back the
serve's running Hive Stand tasks, revoked their grants and removed a live lease's scratch.
`run_hive`, which every one of those commands and `hive serve` go through, now holds the Hive's
**Queen lock** (`<db>.queen.lock`, an operating-system lock like the serve lock of ADR-0041, so
the kernel lets it go with a dead process) from before anything starts until its teardown has
run. A second Queen is refused (`HiveBusyError`, naming the holder) before it attaches a Warden or
requeens anything; a goal for a running `hive serve` goes through `hive run --remote`.

**Waiting for live Wardens.** Requeening runs in two halves. The first, before her loop starts,
re-arms the gate, releases the Hive Stand's orphan leases, gives back the tasks of Wardens that
cannot return, releases ended grants and records what it found. The second runs on her tick and
finishes once no Warden she expects (the Warden of each live task on a Virtual Cell its backend
still lists) still holds a task without being attached, or once `[queen] requeening_grace_s`
(default 90 s, three of the redial's 30 s backoff caps) has passed. A Warden that came back,
finished what it held and left again (its Cell released) counts as back. It then gives back the
tasks of every expected Warden that did not return, revokes their grants, settles the Clustering
pauses, moves her to `RUNNING` and records `queen.requeened`. A Virtual Cell whose Warden never
came back is left to the Undertaker and `hive cells abscond`; Requeening does not tear Cells down.

**The Cells must find her again.** With `[virtual_cells] listen_port = 0` (the default) the OS
chose her listener's port, and a restarted Queen would be handed another one that no surviving
Cell knows. The port the OS chose is therefore remembered in her store, and a restarted listener
binds it first, falling back to a fresh one (and remembering that) only when it is taken. A port
the manifest names is bound as named. Her signing key was already stable across restarts (the
Hive key in the secret store, phase 5's open item 5).

**The replay window survives a restart.** The transport's window stays in memory; what survives is
the Queen's record of what she handled. Each Alarm and question she handles from a Warden is noted
in a durable table keyed by the sending Warden and the envelope id, at the moment she handled it
(never on receipt: a frame noted on receipt and lost in a crash before her tick would be dropped as
a replay and never handled). A question's row also keeps the wire ids she needs to forward its
answer. After a restart a frame whose pair is in the table is dropped as an expected replay, and the
question wiring of every still-open question is restored, whatever its age, so an answer to a
question a returning Warden asked still reaches it, even when the human answers long after the
restart. Plain rows older than the transport's replay window are pruned on each snapshot: nothing
older is ever sent again. A question's row is kept past the window for as long as its question is
still ASKED, and forgotten once it is answered or withdrawn. Results and Nectar need no row; both
are idempotent already.

**The Queen's own Handoff (13.2).** Every `[queen] snapshot_interval_s` (default 60 s) her tick
writes a `QueenSnapshot`: when it was taken, the trail cursor it folded up to, the Hive Stand leases
open at that cursor and the Wardens attached. Recovery folds the trail only from the newest
snapshot's cursor, so its trail read is bounded by the interval, not by the Hive's age. A snapshot
is her own bookkeeping, like a bee's Handoff, and records no trail event (one a minute would bury
the trail); `queen.requeened` names the snapshot recovery started from. One row is kept.

**`queen.requeened`.** A Queen-family lifecycle kind, classified in the Guard's catalogue as a
lifecycle notice. Its payload is counts and one timestamp only: tasks given back, PAUSED tasks
resumed and re-placed, grants released and revoked, leases released, Wardens reattached and
missing, Night Veil tasks cancelled, and the snapshot's `taken_at`. **Night Veil boundary:** it
never names a Cell, a task or a Warden, so nothing of a Night Veil Cell crosses into it. A task an
orphaned Warden leaves on a Night Veil Cell is cancelled (the tier is teardown-only, codingrules
8.7), its `task.cancelled` carrying the task id alone, as the skeleton allows; the Cell itself is
purged by the reconcile's existing sweep. Neither the snapshot nor the handled table holds anything
of a Night Veil Cell beyond envelope ids, which expire with the replay window.

## Consequences

- A kill of the Queen with work on the Hive Stand and on a Virtual Cell finishes every task:
  the Hive Stand's is placed afresh (resuming from its Handoff when it had one), and the Virtual
  Cell's carries on in its Cell and reports through the reattached Warden.
- Every Virtual Cell's Warden now outlives a lost link. A Cell whose Queen never comes back keeps
  redialing until `max_offline_s` clusters it fully and then idles; the Undertaker and `hive cells
  abscond` remove it as they remove any other.
- An orphan Hive Stand lease cannot be restored exactly: the restore records and started process
  ids lived in the dead process. Requeening removes its scratch and reports the paths it touched
  outside scratch (from `cell.touched_outside_scratch`) as residual. A command a Hive Stand lease
  started outlives a crash only on POSIX (Windows runs each in a Job Object that dies with the
  process); killing it needs its pid on the trail, which is left for later.
- Every start records one `queen.requeened`, including a clean one.
- A local `hive run` beside a running `hive serve` on the same Hive is refused, where it used to
  run a second Queen over the same chamber; `hive run --remote` is the way to reach a serve.
- A task a quarantine or an isolation holds stays PAUSED even when its Warden is gone: its hold is
  someone else's to lift, and placing it afresh would slip it. Once lifted, it is cancelled or
  placed by hand.
- A task whose `TaskAssign` the crash swallowed (the chamber read RUNNING, the Warden never heard
  of it) is not detected when that Warden comes back; it waits for the Warden's own liveness and
  the human. Reading a returning Warden's held tasks off its heartbeat would close this, and is
  left for later.
- Supersedure (13.2a) starts the new Queen through the same path from the copied stores.

## Alternatives considered

- **Persist the transport's replay window.** Rejected: noting a frame on receipt turns
  at-least-once into at-most-once across a crash. Dedupe belongs where the frame is handled.
- **Snapshot everything and replay nothing.** Rejected: a snapshot is minutes old by design; the
  Brood Chamber and the ledger are already durable and current, and copying them would create a
  second, staler truth.
- **Wait for no one: re-place every RUNNING task at once.** Rejected: a Virtual Cell's work
  survives the Queen, and re-placing it would run it twice.
- **Tear down a Virtual Cell whose Warden does not return.** Deferred: a Warden can return after
  the grace (a slow host), and the Undertaker already owns teardown of Cells nothing uses.
