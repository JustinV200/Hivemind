# ADR-0056: A lost link is investigated by a diagnostic Drone, inside a budget carved out of the offline limit

- Status: Accepted
- Date: 2026-10-13

## Context

The floor for every cut-off Cell is the one ADR-0053 sets: retry the link with backoff, keep working
within what the Warden (the always-on supervisor of one Cell, a unit of compute) owns, and run full
Clustering (pause and preserve) past `[supervision] max_offline_s`. Where a model is reachable that
floor wastes something. Link failures often have a cause a few commands could name and sometimes fix
(a wrong resolver entry, a stopped interface, a proxy setting), and a Warden that can think should
look before it waits. Roadmap step 11.9a adds the looking, and four points in it are hard to reverse
because they set what a cut-off device is allowed to do on its own. Who investigates, and with what
standing: a new role, a new capability family or a request to the Queen (the central orchestrator)
would each add machinery to a path that runs precisely when the Queen cannot be reached. How long it
may try, when the offline limit is also the deadline for Clustering and for what an offline Warden
is allowed to keep. How much access it has, since a Warden that is cut off must not become more
powerful than one that is connected. And who investigates from the Hive Stand's side, the machine
the Queen runs on, once a colonized device's own Warden has moved onto it (ADR-0052; a colonized
device is one whose Warden runs on it) and is therefore on the wrong side of the cut.

## Decision

**A Drone, not a new role.** Where a model is reachable, reconnection is investigated: the Warden
spawns a short-lived Drone (the generic, disposable Worker role) with the objective "diagnose and,
if possible, restore the link to the peer". The Drone does ordinary Capping-gated scratch work
through its `CellSession` (the terminal every Cell offers): `run_command` diagnostics and one-off
scripts written to scratch. The Capping gate is the Hive's quality gate, where a bee proposes an
effect, declares what must be true afterwards and has it checked, so any effect outside scratch is
gated like any other bee's. A new role would need a footprint, a grade floor, a tool set and
escalation rows to describe a capability the Drone already has. The Drone never touches the Royal
Jelly Lab (where tools are authored) or the Quarantine Comb (the sandbox every new tool must pass),
because nothing here is promoted as a reusable tool for other bees: a fix is tried once and
reported.

**Who spawns it.** A Nuc's Warden (a Nuc is a colonized device that also runs a model server of its
own) spawns the Drone from its own local pool, everything physically on its Cell, with no request to
anyone, because a local pool is owned outright (codingrules 8.10). On the Hive Stand side the Queen
hands W0, the Warden of the Hive Stand's own Cell, a diagnosis task pinned to the Hive Stand
(`RequestOrigin.QUEEN`, role Drone, low tempo) for any lost device, whatever its level. A Level 0
device's Warden (Level 0 is a device with only the packet on it) does live on the Hive Stand, but
its hands are on the lost device, so its own session is the thing that is cut; reachability has to
be checked from a Cell that is known to be up. W0's Cell is that Cell, which is also why a colonized
device, whose own Warden left with it, gets the same answer as a Level 0 one. The Drone checks from
this end: the name and route to the device, and whether other Swarm nodes are affected too, which
rules out "my network" against "that device".

**The budget is carved out of the offline limit, never added to it.** `[supervision]
reconnect_budget` has `attempts` (default 2) and `wall_clock_s` (default 300). The manifest
validator refuses a budget that is not smaller than `max_offline_s`, and Clustering keeps the same
deadline as a backstop whatever the Drone is doing, so an investigation can spend only time the
offline limit already allowed.

**Being cut off never grants more access.** The Drone is capped by the Cell's access level (how much
of the device the Hive may touch) exactly as a bee on a connected Cell is. It reports `FIXED`,
`DIAGNOSED` (the cause is known and cannot be fixed from here) or `NO_CAUSE` to its Warden, and it
deposits its findings as Nectar (raw findings) either way, so the Hive learns why a device drops
even when the Drone cannot help. `warden.diagnosed` records the outcome, and then the timeline of
ADR-0053 resumes untouched: a fixed link reconnects as before, and anything else follows the
existing path of holding, deciding and Clustering.

**A Warden with no model skips it.** A Level 1 Warden, one on a colonized device with no model
server of its own, has nothing to think with, so it skips the Drone and clusters at once, as
ADR-0053 says.

## Consequences

Positive: a common class of failures, a local misconfiguration within reach of the Cell's access
level, is repaired with neither the Queen nor a human involved, and the task goes on. The Hive
records a cause for the failures it cannot repair. Nothing new has to be trusted: the Capping gate,
the access level and the Warden's grant already bound a Drone, and the budget is two numbers in the
manifest. Clustering's deadline does not move, so the worst case for an unfixable fault costs
exactly what the existing path cost before. The Hive Stand side is covered with the machinery the
Queen already has, an ordinary task for an ordinary Warden.

Negative: diagnosis costs model calls. A Nuc spends its own local seats, and W0 spends the Hive
Stand's for every lost device, so many devices lost at once mean many diagnosis tasks; they are
ordinary tasks, so W0's bee slots pace them. A Drone on a Cell whose access level forbids running
programs can do little, which is the intended result and not a gap to close. A fix that changes a
device outside scratch rides on the same capabilities, Capping and lease records as any other
change, so it is as limited as the level the operator granted. The budget bounds attempts and
wall-clock time, not the quality of the guess, and a confidently wrong diagnosis is still only
Nectar for the next reader to weigh.

## Alternatives considered

A dedicated diagnostician role: it duplicates a Drone's powers under a second name and a second set
of limits to keep in step. Retrying with backoff alone: it is blind to causes a command would find
at once, and the exit criteria plant one such cause on purpose. Asking the Queen to investigate: she
holds no session by design (codingrules 8.8) and cannot reach inside a device that is cut off. A
budget added on top of the offline limit: Clustering would start later than the manifest says, and
the offline limit is also the point past which a Warden's grants are given up, so it must not
stretch. A Drone allowed to author a reusable tool through the Royal Jelly Lab: a one-off diagnostic
is not a tool anyone asked for, and promotion while disconnected would be a way to widen capability
from the wrong side of the cut. Extra access for a cut-off Cell because the usual route is gone:
being cut off would then reward whatever caused it. Leaving a Level 0 device's own Warden to
investigate: its hands are on the lost device, so it can only report what it already knows. No Hive
Stand side at all: the Queen would wait out the grace period blind, though the Hive Stand's own
network may be the fault.
