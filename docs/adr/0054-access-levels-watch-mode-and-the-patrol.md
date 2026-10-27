# ADR-0054: An idle Real Cell is watched read-only at every access level, and an hourly Patrol decides whether anything is worth saying

- Status: Accepted
- Date: 2026-10-13

## Context

A Real Cell (an existing device the Hive borrows for a task and leaves exactly as found) stays
enrolled between tasks, so its Warden (the always-on supervisor of one Cell, a unit of compute) is
long-lived and, most of the time, has nothing to supervise. Codingrules 8.7 and 15 give that idle
time a purpose and a fence: the Warden watches, it does not touch, it never captures the screen or
the input, it keeps what it sees only for a retention window, and it labels everything it sees `C2`
(Royal: personal or sensitive), because it describes the operator's own machine. Roadmap step 11.10
builds the mode and its hourly review, the Patrol. The fence is easy to state and easy to erode, so
the points that decide it are recorded here before the first probe exists. What exactly may be
observed, and does a wider access level (`READ_ONLY`, `SCRATCH` or `FULL`: how much of a Real Cell
the Hive may touch) widen it. Where does an observation come from on a Cell whose Warden runs on the
Hive Stand, the machine the Queen (the central orchestrator) runs on, and the device holds only the
Pollen Packet (ADR-0051), the thin gateway installed on it, which must not run commands a request
could shape. How long are observations kept and how often are they judged. What a judgement may
produce. And how a Warden that was watching behaves when its link to the Queen drops and returns.

## Decision

**Watching is read-only by construction.** A Real Cell's Warden with no active sub-bees (the Workers
it spawns) enters `WATCH` and releases its lease (the Hive's temporary hold on the device, with its
scratch directory), because a device keeps no Hive scratch between tasks. Observations come from a
`WatchProbe` (`wardens/watch/probe.py`), a protocol that returns `waggle.messages.swarm.Observation`
values. `SessionWatchProbe` runs fixed read-only commands through the Warden's own session, which is
the right probe wherever the Warden is local to its Cell: the Hive Stand's own Cell, and a colonized
device's Warden. `swarm.watch.PollenWatchProbe` serves a Level 0 device (one with only the packet on
it, ADR-0052), whose Warden is on the Hive Stand: it sends the packet a `swarm.observe`, and the
packet answers from fixed collectors, without a lease and without running any command a request
could shape. In both cases the commands or collectors are a fixed list in code, never text from a
request or a model.

**What may be observed is the same at every access level.** The set is exactly
`guard.watch.WATCH_OBSERVATIONS`, which maps every `AccessLevel` to the `READ_ONLY` set: the process
list; resource use, which includes network counters; logs under the roots the watcher may read; and
file-change events under those roots. A higher level never widens it, because acting is not
watching. Screen capture and input capture are never watch mode: they are a separate grant
(`exoskeleton:real_display`, which lets the Exoskeleton, the Hive's virtual display, input and
audio, drive the operator's real screen) that no level issues implicitly. Watching is bounded by the
capability `watch:<cell_id>`, so a Warden may watch its own Cell and no other, and the packet checks
it again on every `swarm.observe`.

| Access level | What a bee on the Cell may do | What watch mode observes |
|---|---|---|
| `READ_ONLY` | read files anywhere; nothing else | the four observations |
| `SCRATCH` | the above, write inside the lease's scratch, run programs | the same four |
| `FULL` | the above, write anywhere, use the network and devices, touch paths outside scratch, drive the lease's own display and sound | the same four |

**Observations are Bee Bread, labelled `C2`, and they expire.** Each sample is stored in Bee Bread,
the warm memory tier, with the clearance label `C2` by provenance (codingrules 8.9). The Warden
samples every `[supervision.watch] interval_s` (default 300 s) and keeps a sample for `retention_s`
(default 86,400 s, 24 hours). A Night Veil bee (one working in the strictest tier, which only
Virtual Cells can have) never sees them, because `memory.assemble` filters by clearance.

**The Patrol is one awake episode an hour, not a model call per sample.** Every `patrol_interval_s`
(default 3,600 s) the Warden runs one awake episode (a model call assembled fresh from stored state
and discarded after one decision) on `ModelSlot.WARDEN` (the named place the Warden's model calls
resolve to) over the observations since the last Patrol. The episode is assembled, as every awake
episode is, from state by `memory.assemble`. It receives the signals of a pure pre-filter
(`wardens/watch/signals.py`) labelled as facts: new processes over a CPU share (`cpu_share_alarm`,
default 0.5), disk past a fill ratio (`disk_fill_alarm`, default 0.95) and a restart loop. The
episode decides exactly one of four things. `ALARM` raises an Alarm (an issue a bee cannot resolve,
escalated up the chain) of the new kind `AlarmKind.PATROL_ANOMALY` (Waggle 1.13), which climbs the
ordinary chain and is posted to the human only by the Queen. `NECTAR` deposits a summary as Nectar
(the Hive's intake for raw findings), labelled `C2`. `WAX` sends a `cell.wax_proposed` with origin
`PATROL`: a proposal for Cell Wax, a caution about one Cell, which only the Queen writes or rejects
(a disk nearly full, a service that keeps restarting). `NOTHING` records that nothing was notable
and discards the observations the Patrol covered. Whatever it decides, every Patrol records
`warden.patrol` with its outcome and counts. A bee assigned to the Cell moves the Warden back to
`ACTIVE`, and the same mode applies to the Hive Stand's own Cell when it is idle.

**A watching Warden that goes offline returns to watching.** The Warden's state machine gains one
edge, `OFFLINE -> WATCH`, for a link that comes back when no sub-bee is running, so a Warden that
was idle when the link dropped does not have to pass through `ACTIVE` to resume.

## Consequences

Positive: the Hive learns a device's habits without touching it. The exit criterion can be checked
mechanically, as a file-tree snapshot taken before and after an idle night, because nothing in watch
mode writes. The breadth of observation does not grow with trust, so there is one audit story for
all three levels, and a read-only device, which runs no command at all, can still be watched through
the packet's fixed collectors. The cost of judgement is one bounded episode an hour per Cell, with
the arithmetic done by a pure pre-filter so the model is given numbers instead of being asked to
find them. Anomalies surface as ordinary Alarms, a persistent concern as Cell Wax, and a quiet hour
leaves nothing behind. The mode is visible in the Observation Hive (the Hive's dashboard) as the
Cell's mode.

Negative: the Patrol is an awake episode, so it needs a reachable model on the Warden slot; without
one the Warden keeps sampling but cannot judge, and the observations age out under the retention
window. A sample is a point in time, so a burst shorter than the sampling interval can go unseen. A
day of `C2` observations about the operator's machine sits in Bee Bread on the Hive Stand, which is
sensitive in itself; it is protected by the clearance filter and the window, not by its absence. Log
lines, file names and process names are text written by whatever runs on the device, and like any
retrieved content (codingrules 15) they reach the Patrol's prompt delimited and labelled as data,
with a closed set of four outcomes none of which executes anything. A Level 0 device costs one
`swarm.observe` round trip per interval, bounded but not free. Releasing the lease while watching
means the first task after a quiet spell pays for opening a new one.

## Alternatives considered

Letting a wider access level watch more: it would turn trust in acting into permission to read, and
leave three audit stories where one will do. Capturing the screen or the keyboard in watch mode: it
is a different act with a different risk, and it stays a separate, explicit grant. Streaming every
sample to a model: it spends tokens on hours when nothing happens; the pre-filter and the hourly
episode bound the cost. Letting the pre-filter raise Alarms with no model: fixed thresholds alone
would be noisy and brittle, so the pre-filter's output is given to the episode as facts and the
model judges. Running commands on a Level 0 device for each observation through a lease: the
commands are the thing a request could shape, a read-only device has no `exec` to run them, and the
lease would leave scratch behind; fixed collectors in the packet avoid all three. Keeping
observations indefinitely or in Honey: they are raw, personal and mostly uninteresting, so a short
window in Bee Bread is the right life for them. Holding the lease open while watching: a lease owns
scratch and processes, and a device is meant to carry none of the Hive between tasks.

## Amendment (2026-10-14): who names the directories a device's Warden reads

The Decision says logs and file changes are read "under the roots the watcher may read" and does
not say who names them. On the Hive Stand's own Cell the manifest does (`[supervision.watch]
roots`). A device's Warden was built with none, because the Hive's manifest knows nothing of
another machine's directories, so on a device only the process list and resource use were ever
observed: a log line or a changed file there never reached an observation.

**The operator names them, per device, after enrolment.** `hive swarm watch NODE DIRECTORY...`
stores the list on the node (`SwarmNode.watch_roots`), `--clear` empties it, and naming none shows
what is set. None is the default, so a device is watched for its processes and resource use until
the operator chooses a directory, and the Hive never guesses where a log lives. `checked_roots`
judges the list before it is stored: every directory absolute in the device's own spelling (POSIX
or Windows, told by the text and not by the Hive Stand's system), at most the sixteen of at most
256 characters that `swarm.observe` carries, readable under the node's granted capabilities, in
normal form, each once and none inside another, since the packet reads a whole tree. The Warden's
build filters the stored list through the same capabilities once more (`readable_roots`), in case
the access level was narrowed since, and `swarm.watch_changed` records the new list: a grant to
read a directory belongs in the audit trail, and it is the one swarm event that carries paths. The
Queen's reconcile pass treats the list as it treats the access level and the tier: a Warden keeps
the settings it was built with, so a change rebuilds the device's Level 0 Warden.

**The packet stays the authority.** The Hive's list is a request and grants nothing. The packet
answers for a root only if its own `fs:read` capability covers it, and never follows a symbolic
link out of a directory it walks, so a link inside a watched directory that leads elsewhere is not
read. A root outside the grant is refused at the desk and again at the packet.

**Why not the other places.** Enrolment's capabilities say what the Hive may read (`fs:read:**` at
every level), not where the logs are; putting directories in the invite would need the device's
layout before the device exists and a new invite for every change. `hive swarm access` sets what a
bee may do, and this ADR holds that a wider level never widens watching, so tying directories to
it would undo that. A device manifest suits a Warden that is local to its files, which would name
them in its own `[supervision.watch] roots`; but a Level 0 device has no manifest, and the Warden
`cli/in_cell` builds for a colonized device carries no watch settings today, so it does not watch
at all. Wiring that, and where it takes its roots, is left to the step that makes it watch. The
next amendment is that step.

## Amendment (2026-10-14): a colonized device's Warden watches

The first amendment ended on a gap: the Warden `cli/in_cell` builds for a colonized device (Level 1
and 2) carried no watch settings, so it did not watch at all. Step 11.10 applies to every Real Cell,
so a device stopped being watched at the moment its Warden moved onto it, which is the opposite of
what moving it should do. The Warden on a device is local to the machine, so it looks the way the
Hive Stand's own does: fixed read-only scripts through a session (`SessionWatchProbe`), with the
same Patrol, the same C2 observations in its own Bee Bread (the device's SQLite file) and the same
`warden.patrol` records, which ride the trail it ships. Four things had to be decided.

**It looks through a session that holds no lease.** The Hive Stand's Warden holds one lease for its
whole life and lends the probe that lease's session. A device's Warden gives its lease back whenever
it has nothing to supervise, so the machine keeps no scratch between tasks (a scenario asserts it),
and a lease opened for each look would put an open and a release on its trail at every beat for as
long as the device is idle. It looks through an `ObserverSession` (`cell/local/observer.py`), a
`CellSession` that belongs to no lease: it runs a command through the same child-process machinery
as every session and refuses everything else (write, read or delete a file, start a background
process), so the probe cannot write to the device because the session it holds cannot. It stands in
the Warden's own state directory and never creates a scratch of its own.

**The numbers are the device manifest's.** `[supervision.watch]` in `warden.toml`
(`DeviceWatchSection`) holds the five numbers the Hive's table has (sample interval, retention,
Patrol interval, the CPU and disk thresholds, `WatchTuning`) and the Hive's shipped values when it is
absent. `roots` is not a key there and is refused with the reason: the Hive Stand's directories are
not the device's, and the operator names a device's with `hive swarm watch`. The Patrol is an awake
episode on the Warden's own `warden` slot binding, assembled from its own Bee Bread like any other,
and nothing it does writes to the device: the observations and records go to the state directory.

**The directories travel as the other terms do.** Not by the device manifest, which an operator
writes once at install time and the Hive Stand's operator cannot reach, and not by a message to the
Warden, which has none for any term. They ride the path the access level and the tier already take.
`hive swarm watch` stores the list on the node; the Queen's reconcile pass sends the packet
`swarm.node_update` with it (`NodeUpdate.watch_roots`, only the directories the node's capabilities
cover, `readable_roots`), and the packet keeps it in its state; the packet starts the Warden with it
in `HIVEMIND_WATCH_ROOTS`, a JSON array; the Warden says which list it runs with in its `cell.ready`
(`CellReady.watch_roots`); the gate's desk keeps that with the access level and the tier it
reported; and `terms_drifted` compares all three with the registry's. A difference starts the terms
renewal (ADR-0053): the packet is told, the bees pause at Handoffs, the packet stops the Warden and
starts it, and the new Warden's `cell.ready` is checked against the terms; one that comes up under
the old list is never trusted, and the device falls back to Level 0, where the Hive Stand's Warden
asks the packet for exactly the registry's list. Naming a directory therefore costs what a new
access level costs: a restart, with any task paused and resumed from its Handoff. That is the price
of one way for a term to reach a Warden, and a restart is the lightest way the pieces allow.

**It is held to the access level at both ends.** The Queen sends only what the node's capabilities
let the Hive read, and the Warden filters what it was handed through the same rule
(`readable_under` over `granted_capabilities`) before it reads. The Warden reports back what it was
handed, not what it kept, and the Queen compares that with what she sends, not with the operator's
whole list. A difference between the two ends' rules (an old stored capability set) then makes the
Warden read less than was asked and leaves a visible discrepancy, where comparing what was kept
would have restarted the device for ever; and a directory the capabilities do not cover is no
difference at all, since it is never sent.

Two limits are the design's. A Warden on a device wakes every fifteen seconds (its heartbeat), and
watching runs on its tick, so a sample and a Patrol cannot come closer than a beat whatever
`[supervision.watch]` asks; the first sample of a Warden that has just started has no CPU baseline,
so a process's share is measured from its second. And a quiet Patrol discards what it covered, so
the observations that hold a log line are on the device only until the next quiet one; an ALARM
keeps its window. Proved in a container (`tests/e2e/test_phase11_exit_watch_colonized.py`): the
Warden on the device sits in WATCH with no lease, is Patrolled on its beat and quietly, makes a
planted CPU hog an Alarm at the next Patrol (the observations it covered hold no file while no
directory is named), is restarted by the Queen when `hive swarm watch` names one, and then covers
that directory's log lines and no other directory's, with the device left as found.

## Amendment (2026-10-26): SCRATCH runs no command on a device

The Decision's table says a bee on a `SCRATCH` Cell may "run programs". ADR-0060 narrows that:
below `FULL`, a command runs only on a Cell whose processes are contained, because a command run
as the machine's user can copy anything it can read into scratch. A borrowed device does not
contain its processes, so a device at `SCRATCH` runs no command, and its enrolment grants carry no
`exec` (docs/waggle/enrolment.md section 7.1). The one exception is ADR-0056's diagnostic Drone,
which may run a fixed list of read-only diagnostics below `FULL` (ADR-0060, "Amendment: the
read-only diagnostics"). Everything else in the table stands; watch mode is unchanged, since it
runs no command.
