# Supersedure: moving the Hive Stand

Supersedure moves the Hive Stand (the machine the Queen runs on, with all her stores) to another
machine in your Swarm without losing a task. It is the human's job and only the human's: you run
it at the Hive Stand, after a step-up and a typed phrase, and no Queen, Warden, Worker, autopilot
rule or tool can start it. The decision behind it is
[ADR-0065](../adr/0065-supersedure-moving-the-hive-stand.md). The one rule everything below
serves: two Queens never run at once.

## Before you start

The candidate must be a Swarm node that is:

- enrolled, approved and online right now;
- colonized (Level 1) or a Nuc, with `FULL` access (`hive swarm promote`, `hive swarm access`);
- on the same runtime version as the Hive Stand;
- holding free disk for the Hive's database and Basket blobs plus a quarter again, and enough free
  memory for the Royal Reserve;
- holding the Hive's secret store already: every secret the Hive Stand has, with the very same
  `hive.ed25519`. Copy `[hive] secrets_dir` across yourself, the way a backup restore needs it
  (docs/runbooks/backup.md). The keypair never travels over the network;
- within 5 s of the Hive Stand's clock;
- reachable by every other Swarm node and every Virtual Cell backend at the address it will serve
  on. Nothing on the Hive Stand can prove this, so the test reports it `UNVERIFIED`; check it by
  hand.

Run the candidate test first. It changes nothing:

```sh
hive doctor --supersedure node_01...
```

Each check prints `PASSED`, `FAILED` or `UNVERIFIED` with one line of why. The command exits 0
only when nothing failed. `hive supersede` runs the same test first and refuses on any failure.

## Moving

```sh
hive supersede node_01...
```

It logs the Hive Stand's console in with the operator password, asks you to type
`supersede my hive stand`, steps up, and orders the move on the loopback listener (the route is
never served remotely). It prints the `queen.supersedure_started` event and the grace deadline,
then returns. The move runs on:

1. **Freeze.** The Queen clusters every provider (`queen.clustered`, cause `supersedure`): every
   bee checkpoints and pauses, every lease and grant stays. She is now `SUPERSEDING`: she places,
   plans and resumes nothing.
2. **Copy.** The stores (every table, the Entrance and its enrolled devices included, and the
   Basket's blobs) go as one `hive backup` archive over the candidate's session and are restored
   there with `hive restore --sha256`, which checks every byte again.
3. **The candidate comes up held.** Her Queen starts from the copy in `REQUEENING`. Her own trail
   says she is a Supersedure's candidate, so she places nothing and records
   `queen.supersedure_ready`.
4. **Handover.** The old Queen sees the candidate ready, moves to `SUPERSEDED`, records
   `queen.superseded`, acknowledges the candidate (who records her own `queen.superseded` and
   lifts her hold), and only then sends a signed `QueenMoved` to every device. It carries the new
   address, the new listener's TLS pin and a grace window during which devices honour both
   addresses. The old Queen's loop ends, but her process does not: stop `hive serve` on the old
   machine once the new Queen has the Hive. Until you do, the old address still answers, so a
   candidate whose acknowledgement was lost stays held, and a device that falls back to the old
   address during the window reaches a listener with no Queen behind it.
5. **The new Queen finishes Requeening.** Wardens and devices redial her. Every task the old Hive
   Stand held is placed afresh from its newest Handoff; paused bees resume. One
   `queen.requeened` follows.

Watch it with `hive trail --kind queen.superseded` (or `queen.supersedure_aborted`) on either
machine. Each machine's trail ends up with exactly one outcome for this Supersedure.

## If the candidate is not ready in time

If the copy fails, or the candidate has not recorded `queen.supersedure_ready` by the grace
deadline, the old Queen rolls back on her own. The copy counts against the same deadline: a
candidate that stops answering mid-copy is rolled back as `NOT_READY` when the window ends. She records `queen.supersedure_aborted` (with the
cause: `COPY_FAILED` or `NOT_READY`), tells the candidate to discard its copy, returns to
`CLUSTERED`, resumes every provider the freeze clustered (one that was already down before the
move stays clustered until its health probe answers), and sends a `QueenMoved` pointing every device back
at herself, with a higher sequence than any notice before. Your goals carry on where they paused.

If the candidate was unreachable when the old Queen rolled back, it never heard the discard. Its
copy of the Hive must never run. Before you start `hive serve` there again, settle it on the
candidate:

```sh
hive supersede --settle aborted
```

## After a crash

A Queen that stops in the middle of a Supersedure (a crash, a power cut, a `kill`) will not start
again on its own. `hive serve` refuses with `hivemind.queen.supersedure_pending`, because she
cannot know whether the candidate took over after the window. Look at the candidate:

- If the candidate is serving and its trail has `queen.superseded` (or it is past the window and
  running), it has the Hive. On the old machine, run `hive supersede --settle superseded`. That
  machine never starts a Queen for this Hive again.
- If the candidate is not running the Hive, stop it if it is up, settle it with
  `hive supersede --settle aborted` there, then run `hive supersede --settle aborted` on the old
  machine and start `hive serve` as usual.

`--settle` runs with no Queen. It needs the operator password and the Queen lock, and it records
your word on that machine's own trail.

A candidate that comes up after the window with the old Queen unreachable takes the Hive on its
own (`queen.superseded` with `acknowledged: false`). That path exists for a dead old machine. If
the old machine was only cut off and has rolled back, settle the candidate as aborted before it
ever runs again.

## After the move

- The old machine's Queen is done. Its Warden stays. To use it as an ordinary Real Cell, enrol it
  into the Hive from the new Hive Stand (`hive swarm invite`, then `pollen enrol` on it); it joins
  in `WATCH`.
- A device that was offline for the whole grace window never saw the `QueenMoved` and still dials
  the old address. Mint it a new invite on the new Hive Stand.
- Phones and other Entrance clients keep their enrolment (it moved with the stores), but each one's
  saved Entrance address is yours to update.

## What is not built yet

The in-process proof (`packages/hivemind/tests/e2e/test_supersedure.py`) runs two Queens on temp
stores: one handover with a sampler that fails if both Queens are ever active, and one candidate
killed before it is ready, rolled back with the goal still finishing. On a real Hive the transport
to a candidate machine (streaming the archive over the candidate's session, running `hive restore`
and `hive serve` there, asking it about its secrets and clock) is not wired yet, so `hive doctor
--supersedure` and `hive supersede` answer "this Hive was started without Supersedure". The
roadmap's exit run (supersede onto an enrolled Nuc while a goal runs on the Hive Stand, in a
container and on the Nuc) needs that transport and the hardware.
