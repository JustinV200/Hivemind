# ADR-0052: A device climbs a ladder of three rungs, one at a time, and the Warden is the first bee to move onto it

- Status: Accepted
- Date: 2026-10-13

## Context

A Real Cell (an existing device the Hive borrows for a task and leaves exactly as found) starts with
only the Pollen Packet on it, the thin gateway of ADR-0051. Its Warden (the always-on supervisor of
one Cell, a unit of compute) lives on the Hive Stand, the machine the Queen (the central
orchestrator) runs on, and drives the device over a signed terminal session carried by Waggle, the
protocol the bees use to talk to each other. That costs a network round trip for every command,
leaves the device with no autonomy when the link drops, and charges the Hive Stand's model seats
(the concurrent requests a model server will take) and memory for every device it supervises. Moving
more of the Hive onto the device removes those costs but asks more of a machine the Hive does not
own, so it has to be gradual, reversible and decided from data. Codingrules 8.7 and 8.10 fix the
ladder and two invariants, and roadmap steps 11.6 to 11.8 build it. What they leave open is hard to
reverse once devices run it. In what order does what move, and what must always be true of the
pieces. Whose identity does the Warden carry across a move: the spec's resolved question 37 assumed
the Warden's id is stable and told the packet never to hard-pin it, but did not decide. How do a
Warden's tasks survive the move when the Warden that resumes them, on the device, cannot read the
Hive Stand's memory store. How does the `hivemind` runtime reach the device without the packet
becoming something that executes code from the wire. And where does a device's own configuration
live, since a Warden on a laptop in another city has the laptop's environment and network, not the
Hive Stand's.

## Decision

**The ladder has three rungs, and the wire names them.** `RuntimeLevel` is `GATEWAY`, `COLONIZED` or
`NUC`.

| Level | Wire name | The device runs | The Warden runs | Sub-bee brains and hands | Models |
|---|---|---|---|---|---|
| 0 | `GATEWAY` | the Pollen Packet only | on the Hive Stand, in the Queen's process over the memory transport (an in-process link) | brains (the model loops of its sub-bees, the Workers it spawns) beside the Warden on the Hive Stand; hands on the device through `PollenSession` (the `CellSession`, a Cell's terminal, carried over Waggle) | the shared sources the Hive Stand reaches |
| 1 | `COLONIZED` | the packet and the Warden with its sub-bees (`hivemind-in-cell` in a borrowed mode: the in-Cell runtime of ADR-0027 leasing a Cell it did not create) | on the device | brains and hands both on the device | shared sources named by the hosting plan (the Queen's per-Cell choice of model sources) |
| 2 | `NUC` | the above, plus a model server | on the device | on the device | the device's own server first |

A Cell is colonized from Level 1 and stays that way across leases, even back in `WATCH` (the
read-only mode of an idle Warden, ADR-0054): the tools scoped to it and the Honey (the Hive's
distilled knowledge) it has accumulated outlive any one lease (codingrules 8.7). A colonized Cell
that also runs a model server of its own is a Nuc.

**One rung at a time, and the Warden moves first.** `hive swarm promote` climbs one rung and
`demote` descends one; `GATEWAY -> NUC` is refused. A Warden and its sub-bees are always co-located:
sub-bees run where their Warden runs, never the other way round. The Warden is the first bee onto a
device because it is the supervisor and the reconnecting agent, and it needs no model to keep the
lights on: its autopilot (the deterministic rules that handle every event before any model is asked)
never awaits a model (ADR-0011), so a Warden alone can hold a lease, enforce the Capping gate (the
Hive's check on every side effect), raise Alarms (issues a bee cannot resolve, escalated up the
chain), cluster its sub-bees (pause and preserve them while the Hive cannot think) and re-dial the
Queen. A sub-bee that ran on a device without its Warden would have none of that, and a model server
without a Warden would have nobody to start it within ceilings (the limits the Queen sets once on a
Warden's own pool of capacity), since starting one needs the Cell's session and that code lives with
the Warden (codingrules 8.10).

**The Warden keeps its id across the move, and a move is checkpoint, Handoff, resume.** This is the
one mechanism with many names of codingrules 8.9, applied to a Warden's host. The Queen pauses the
Hive Stand-side Warden's tasks, and each sub-bee checkpoints and writes a Handoff (the structured
document a bee writes before its context is reset). She shuts that Warden down, which releases its
lease and leaves the device as found. The packet then starts the on-device Warden under the same
`warden_id`; it dials the swarm listener (the Queen's WebSocket listener for devices), its first
frame a `cell.ready` signed by the device's node key (ADR-0051), and is attached. The Queen resumes
each paused task on it with the Handoff carried inline in `TaskAssign.handoff` (Waggle 1.13),
because the device's Warden cannot read the Hive Stand's memory store; a validator ties the carried
Handoff to the `resume_from` reference and its clearance, and a tainted Handoff never travels. A
Handoff too large for one frame falls back to a fresh start of that task. `warden.migrated` records
`from_node` and `to_node`: the id is the same, the signing node (the process whose key signs the
Warden's frames) is not, since a Level 0 Warden signs as the Hive Stand's node and a Level 1 Warden
as the device's. `swarm.node_update` tells the packet the Cell's current `warden_id` and level, so
its sender rule follows the move and it never hard-pins an id.

**Colonization is one request with three actions.** `swarm.colonize` carries an `action`. `PREPARE`:
the packet's `bootstrap` installs the pinned `hivemind` runtime version into a virtual environment
under its data directory, from the wheelhouse or package index the operator configured at `pollen
install`, never from anything on the wire. `START`: the packet starts the on-device Warden as a
background process, giving it through its environment the location of the device's node key, the
swarm listener's URL and certificate pin, the Warden id, the Cell id, the access level, the tier and
the device's local manifest. `WITHDRAW`: the packet stops it. Each is answered by `swarm.colonized`
with the outcome and the level that now holds. The packet stays a gateway throughout: it installs
files and starts and stops one process, and it imports nothing from `hivemind`.

**Withdrawal is for an idle Warden; Nuc demotion is local.** Level 1 to Level 0 happens only when
the on-device Warden is idle, in `WATCH`; a busy Warden refuses with a reason, because its Handoffs
live on the device. Demotion from Nuc to colonized (2 to 1) is local to the device's Warden, which
stops its model server, and needs no migration.

**The device carries its own configuration.** An on-device Warden reads a small device manifest,
`<data_dir>/warden.toml`, which the operator writes at `pollen install --warden-manifest`. It holds
the Warden's `[llm.providers]` and `[llm.slots]` for shared sources reachable from the device, and
its `[llm.servers]` for a Nuc: phase 8 left a Nuc as the place a device-side server is declared.
Keys come from the device's own environment, never from the wire. The Queen's `NucPromote.server_id`
must name one of the servers the device's `CapacityReport.model_servers` declared, so she can only
start what the operator put there.

**The decisions are pure and Guard-gated.** `swarm/level.py` holds `decide_colonize`: the device's
Forage (its spare capacity: compute, model seats and spend) covers a Warden plus one sub-bee, and
one of three reasons holds: the Hive Stand needs relief (its relief pressure, defined in the
amendment below, past `[swarm.ladder] relief_pressure`), a task needs to survive short outages, or
the operator asked through `desired_level`. `swarm/nuc.py` holds `decide_nuc`: the device is at
Level 1, it declares a server whose allowlisted models fit its free VRAM, and one of three reasons
holds: the shared pool's seat pressure past `[swarm.nuc] seat_pressure`, a task's need for
autonomy, or `desired_level`. Both only decide; the Queen acts on the
answer. Every rung change, up or down, passes the guard's `NUC_PROMOTION` enforcement point (the
named place where the Hive's policy engine decides, ADR-0039) and is recorded as `swarm.promoted` or
`swarm.demoted` with the `from_level` and `to_level`.

## Consequences

Positive: because the Warden's id never changes, its grants (leases on shared capacity), ceilings,
Cell Wax (the Queen's cautions about one Cell), leases and trail history (the Pheromone Trail, the
Hive's append-only audit log) stay attached to one bee across every rung, and nothing keyed by it
needs migrating. A sub-bee never runs unsupervised. Every rung is reversible except where reversing
would lose a Handoff, and that case is refused rather than lost. The move reuses the checkpoint,
Handoff and resume path that Clustering (ADR-0024), rebinding and takeover already use, adding only
the inline Handoff, so there is no second recovery mechanism to trust or test. The packet's job
through all three rungs is a handful of file operations and one background process. Placement and
the readbacks see the same Cell throughout, with the Warden's location and the level as attributes.

Negative: Level 1 needs a Python environment on the device, disk for the runtime and an
operator-provisioned wheelhouse or index, so a device without those stays at Level 0, which is why
the gateway contract treats colonization as optional. Tasks are paused for the length of a
migration, so promotion costs latency on the work in flight, and a task whose Handoff does not fit a
frame restarts from scratch. Once a Warden is on a device its Handoffs, memory and trail segments
live there, so a wiped device loses them; withdrawal refuses while it is busy for exactly this
reason. Level 0 keeps every device's Warden and sub-bee brains on the Hive Stand, so a Hive with
many devices spends its seats and memory there until `decide_colonize` finds relief; the limits the
roadmap documents for Level 0 itself are remote exec latency and no autonomy when the link drops. An
on-device Warden depends on a manifest and environment the operator maintains on that device, and a
mistake there leaves it unable to reach shared sources, in which case it clusters its sub-bees at
once (ADR-0053).

## Alternatives considered

Moving sub-bees before or without their Warden: they would run with no Capping gate, no Alarm chain,
no left-as-found enforcement and no reconnection, and the co-location invariant exists to forbid it.
Minting a new `warden_id` on the device: it would orphan the Warden's grants, ceilings, wax and
trail attribution and force a migration of everything keyed by it, for no gain. Going straight from
Level 0 to a Nuc: there would be no Warden on the device to start the model server within ceilings.
A level fixed at enrolment: devices change, with free VRAM, load and the Hive Stand's pressure, and
the operator may want to move a device later. The on-device Warden fetching its Handoffs from the
Hive Stand's memory store on demand: it would open the Queen's store to reads from a remote device
and add message kinds; one inline Handoff in an existing message keeps the Queen the only reader.
The Queen sending the runtime over Waggle: it makes the packet an executor of code from the wire on
a borrowed machine, where the operator's configured wheelhouse is a trust anchor the operator
already controls. Embedding the Warden in the packet process: the packet would import `hivemind`,
which breaks the single-dependency rule of codingrules 4 and the path to a compiled gateway.

## Amendment (2026-10-14): the Queen's own rung decisions

Until now only the operator moved a device. The Queen now moves devices herself, with the same
pure rules (`decide_colonize`, `decide_nuc`), the same Guard check and the same flows, and records
her reason in the `swarm.promoted` or `swarm.demoted` the move writes. She does it from the gate's
reconcile pass, in `queen/swarm_gate/rungs`, for a reachable device that has no `desired_level`.

**Two figures, each defined once** (`rungs/pressure.py`). The relief pressure is the larger of the
cores and the memory that the running sub-bees of Level 0 Wardens take, as a share of the Hive
Stand's own cores and memory (what its Warden last reported), from 0 to 1; a sub-bee costs what its
role's `[forage.roles]` footprint says. It counts only what colonizing would take off the Hive
Stand, so the Queen herself and the Hive Stand's own Cell are not in it, and the Queen sizes
nothing she has not been told the size of. The seat pressure is 1 minus the shared pool's free
seats over its usable ones (the seats its sources declare, less the Royal Reserve's), from 0 to 1.
Each bears only on a device that has a sub-bee running: moving a device that has none takes nothing
off the Hive Stand and no call off the pool, and she does not install a runtime, or load a model,
on a machine for a strain it has no part in. A pressure is acted on one device at a time, the
busiest first, and the next device is judged on the figure the move changed.

**A task can say it must survive** (`TaskNeeds.survives_link_loss`, default false, settable in a
plan and in an operator's goal). It is read for every unfinished task that is placed on a device and
every one pinned to a device, which a goal's ceiling does when it allows exactly one named Real
Cell and no other kind of Cell. It is the outage reason for the first rung and the autonomy reason
for the second.

**She does not flap.** A rung she raised is hers to take back; one the operator raised is not. She
takes it back only when every reason for it has been absent for `lower_after_s` (a reason that
holds again restarts the time) and only while the device is idle (its Warden reports `WATCH` and no
task is on its Cell). A move that fails, or that the Guard refuses, is not tried again for
`retry_after_s`, doubling with each failure in a row up to 32 times. What she remembers of her own
moves is kept in the gate's process and not persisted: a restarted Queen has no marks, so she takes
nothing back until she has raised it again, which errs toward leaving a device where it is. A
reason that holds on a device that cannot carry the rung (too old a Python, too little VRAM, no
server declared) is a `queen.decided` with `rung_declined` and the stable word, recorded once.

**The operator always wins.** A device with a `desired_level` is never evaluated; the colonizer
carries the wish out. `hive swarm demote` also writes a `ceiling` on the device, the highest rung
the Queen may take it to by her own decisions: a `promote` lifts it, a `demote` sets it to the rung
the device is lowered to, so a device pinned at the gateway is never climbed again. A device above
its ceiling is taken down, one rung, when it is idle; she never climbs past it.

## Amendment (2026-10-14): a move waits for a bee that is inside a command

The move pauses each running task of the Warden it ends, and the Handoff a task resumes from is the
one its bee writes when told to. A Drone is inside a command (a `sleep`, a build, a test run) for
nearly all of its time and can write a Handoff only between two commands, so the pause used to
reach it first and a paused bee cannot hand off: the task resumed on the device from nothing, and
the move kept it from failing while throwing its progress away. The Queen now sends the bee the
order to hand off, waits for the Handoff in real time for up to `HANDOFF_PATIENCE_S` (thirty seconds)
for each task, and only then pauses it. A bee that does not come back from its command in time is
paused all the same and resumes from its last acceptance state, as before. Clustering gives its
bees no such time, because the provider it clusters on is the one they are stuck on.
