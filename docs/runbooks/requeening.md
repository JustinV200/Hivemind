# Requeening a crashed Queen

Requeening is how a Hive comes back after its Queen's process died without its teardown: a crash,
a `kill -9`, a power cut, a laptop lid closed on `hive serve`. There is nothing to run by hand.
Every start of the Queen (`hive run`, `hive serve`) is a Requeening; a first start, or a start
after a clean stop, simply finds nothing to do. The decision behind it is
[ADR-0062](../adr/0062-requeening-recovery-sources-of-truth.md). This runbook says what it does,
what you will see, and what to check when a restart does not go the way you expect.

## What happens at a start

1. **Before the Queen's loop runs.** Her Virtual Cell listener binds the port it had last time
   (when `[virtual_cells] listen_port` is 0, the default, the OS-chosen port is remembered in the
   Hive's database), the backends are listed, and every live Virtual Cell's public key is read
   back from its `hivemind.cell_key` label, so a Cell that outlived her can prove itself again.
2. **The first half of Requeening.** The Queen reads back the Alarms and questions she handled in
   the last ten minutes and the wiring of every still-open question, however long ago it was
   asked, so nothing a Warden sends
   again is handled twice and an answer still reaches the Warden that asked. Any Hive Stand lease
   her dead process left open is given back: its scratch removed (kept if `[hive_stand]
   keep_scratch` is set), a `cell.released` recorded with `requeened: true`. Every task that was
   on a Warden that cannot come back is placed afresh (the Hive Stand's own, whose Warden died
   with her; a Virtual Cell's whose Cell is gone), resuming from its newest Handoff where one
   exists; its question, if it waited on one, is withdrawn and asked again by its fresh bee. A
   task on a Night Veil Cell is cancelled instead (the tier is teardown-only). A task on a Swarm
   device is left to the swarm gate, which holds it for `[swarm] unreachable_grace_s` as always.
3. **Waiting for live Wardens.** A Virtual Cell's Warden keeps working through her death, queues
   its results, and redials her with backoff (0.5 s doubling to 30 s). She waits up to `[queen]
   requeening_grace_s` (default 90 s) for each Warden that still holds a task. When it is back
   it replays what it queued, and she resends its live grants; a task she had paused for
   Clustering resumes from its newest Handoff.
4. **Settled.** When nobody she waits for still holds a task unattached, or the grace is over (and
   then the missing Wardens' tasks are placed afresh and their grants revoked), she records one
   `queen.requeened` and moves from `REQUEENING` to `RUNNING`. She then writes her first
   snapshot, and one every `[queen] snapshot_interval_s` (default 60 s) after that, so the next
   recovery reads only the trail since it.

## What you will see

```sh
hive trail tail -n 200 | grep -E "queen.requeened|task.unassigned|cell.released|warden.(spawned|reconnected)"
```

`queen.requeened` carries counts only, never an id:

| Field | Meaning |
|---|---|
| `given_back` | Tasks placed afresh because their Warden is gone. |
| `paused_replaced` | Clustering pauses whose Warden is gone, placed afresh. |
| `paused_resumed` | Clustering pauses resumed through a Warden that came back. |
| `night_veil_cancelled` | Orphans on a Night Veil Cell, cancelled. |
| `leases_released` | Hive Stand leases the dead process left open, given back. |
| `grants_released` / `grants_revoked` | Grants of ended tasks returned; grants of gone holders revoked `HOLDER_OFFLINE`. |
| `wardens_back` / `wardens_missing` | Wardens she waited for that came back, or did not. |
| `snapshot_at` | The snapshot recovery started from, or null on a Hive's first start. |

Each task given back has its own `task.unassigned` (and `task.question_withdrawn`), and each
lease its own `cell.released`.

## When something looks wrong

- **`hive run` says another Queen runs on the Hive.** Only one Queen may run on a Brood Chamber:
  her Requeening takes back the work of every Warden not attached to her, so a second one would
  take back the first one's live work. Every command that runs a Queen (`hive serve`, `hive run`,
  `hive tools submit`, `hive swarm run`, `hive llm eval`) holds the Hive's Queen lock
  (`<db>.queen.lock`, beside the database) for its whole life, and a second one is refused before
  it starts anything. The refusal names the holder's pid. Send the goal to a running `hive serve`
  with `hive run --remote`, or stop the other process first. The operating system lets the lock
  go when its holder dies, so a crashed Queen never blocks the next start.

- **`wardens_missing` is not 0.** A Virtual Cell's Warden did not redial inside the grace. Check
  that the Cell is still running (`hive cells list`, `docker ps`) and that it can reach the
  Queen's address: a manifest that names `[virtual_cells] advertise_url` must still be right, and
  a `listen_port` the OS had to change (the remembered one was taken; the log says
  `cell_gate.port_taken`) is a port no surviving Cell knows. Its tasks were already placed afresh;
  the Cell itself is left for the Undertaker or `hive abscond`.
- **A Cell from before this change never comes back.** A Cell provisioned before its key rode in a
  label (`hivemind.cell_key`) cannot prove itself to a restarted Queen; its tasks are placed
  afresh after the grace. Retire it with `hive abscond`.
- **`residual_paths` on a requeened `cell.released`.** The dead process had written outside the
  lease's scratch, and the records to undo that died with it. The paths are on the trail as
  `cell.touched_outside_scratch` with the lease's id: restore them by hand if they matter.
- **A command the dead Hive Stand lease started is still running (POSIX only).** Windows runs each
  command in a Job Object that dies with the process; on Linux and macOS a command's process group
  can outlive it. Its pid is not on the trail; find it by its working directory (the lease's
  scratch, which Requeening has removed) and stop it.
- **A task stays PAUSED.** A quarantine or an isolation holds it, and those holds outlive a restart
  on purpose (an isolation's `hive cells lift`, a quarantine's judge verdict). If its Warden is
  gone it stays paused until the hold is lifted and it is cancelled or placed by hand.

## The legs that need hardware

`packages/hivemind/tests/e2e/test_requeening.py` kills the Queen in-process with a task on the
Hive Stand and one on a Virtual Cell whose Warden is a real in-Cell Warden in the same event loop,
and checks both finish. Two legs of roadmap 13.1's exit run need hardware the build host does not
have, and are the operator's to run once:

1. **A container.** With Docker running, set `[virtual_cells] backend = "docker"`, run a goal whose
   task needs isolation (`hive run "..."` with a plan that sets `needs.isolation = "REQUIRED"`),
   and while it runs kill the `hive run` process (`taskkill /F /PID <pid>` on Windows, `kill -9`
   elsewhere). Start `hive serve` with the same manifest, which runs until stopped: the container
   is still up (`docker ps`), its Warden redials within a few seconds (`warden.spawned` for its
   Warden id), the task finishes (`hive tasks list`), and `queen.requeened` shows
   `wardens_back: 1`.
2. **A Nuc.** With a colonized device enrolled and promoted to a Nuc (`enrolling-a-device.md`),
   run a goal placed on it and kill the Queen the same way. The device's Warden keeps working
   offline; on restart the swarm gate holds its task while it redials (Requeening leaves device
   tasks alone), and the task finishes on the Nuc.
