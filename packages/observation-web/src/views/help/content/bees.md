The Hive is a tree of supervisors: you, then the Queen, then one Warden per Cell, then the Workers
each Warden spawns. The same supervisor shape is used at every level.

## The Queen

The Queen is the central orchestrator and the only bee with a global view. She owns the task graph,
decides what compute a task gets, divides Forage, assigns work to Wardens, and never does the
hands-on work herself. In the chat she calls herself Monarch.

Structurally she is a small loop that never stops, closer to an operating-system kernel than to a
chat session. Everything lands in one inbox, which her Attendant orders by importance: your
messages weigh heavily but not absolutely, since a bee's question can be more urgent.

## Autopilot first, awake second

Every event, at every level, is handled by fixed rules before any model is consulted: heartbeats,
progress, finished tasks, grants within headroom, Alarms with a known playbook. That is autopilot,
and it never calls a model, which is what keeps the Hive alive when every provider is down.

Only what autopilot cannot decide runs an awake episode: a prompt assembled from stored state plus
the event, one decision, written back, then discarded. No bee carries a growing transcript. The
summary strip's Thinking cell counts bees "by model" (in an awake episode) against bees "by rule"
(on autopilot), and the Thoughts screen shows each episode and each autopilot decision.

## Wardens

Every Cell has exactly one Warden, an always-on supervisor built on the same shape as the Queen. It
spawns sub-bees on its Cell within the Forage grant the Queen gave it, hands each a subset of its
own capabilities and never more, watches their context and progress, and can compact, checkpoint,
hand off, rebind or take over any of them. It handles their Alarms by a policy table and escalates
what it cannot resolve to the Queen. A Warden never addresses you.

A Warden never provisions Cells. It lives on the Hive Stand by default, even for a remote device,
which it drives over a signed terminal session; when anything moves onto a device, the Warden moves
first, and its sub-bees always live where it lives. The Fleet row says where each Warden runs.

## Workers

A Worker is a subagent bound to one Cell, spawned and supervised by that Cell's Warden. It gets a
scoped objective, reports progress and a small telemetry summary of its own context, raises an
Alarm when stuck instead of failing quietly, hands off and resets itself before its context
overflows, and is expendable. The roles:

- **Forager**: gathers data from the outside world (scraping, search, monitoring).
- **Scout**: explores a new target before real resources are committed.
- **Guard Bee**: watches for security trouble and files requests for the Queen.
- **Undertaker**: tears down finished Virtual Cells and releases borrowed Real Cells.
- **Drone**: a generic, disposable Worker for one-off tasks.
- **House Bee**: keeps memory tidy; sweeps hot state into Bee Bread and ripens Nectar into Honey.

## Cells: Real and Virtual

A Cell is the machine a Worker runs in or on. A **Virtual Cell** is a fresh VM or container the
Hive provisions on demand, isolated, disposable, and torn down (or overwintered) when the task
ends. A **Real Cell** already exists and keeps existing: the Hive Stand itself, or a device enrolled
in the Swarm, borrowed for a task and left exactly as found. Work on a Real Cell happens inside a
lease with its own scratch folder; anything outside that folder needs an explicit capability and is
recorded on the trail.

Every Real Cell has an **access level**, set when it joins: read only, scratch or full. The Pollen
Packet asks for full; you may grant less, and nothing can be issued to that Cell beyond it. Virtual
Cells are always full.

Which kind a task gets is a per-task decision: reuse a Real Cell at hand when one fits, or provision
a Virtual one when isolation, a clean image or disposability matters more. Any Cell Wax on a
candidate counts: a block excludes it, a caution counts against it.

## Comb Shield levels

Every Cell has a security tier, separate from its access level, shown as a pill badge on every row
and page:

- **MEADOW** (tier 0): the default baseline; any machine, no VPN required.
- **PROPOLIS** (tier 1): hardened for normal production work; a VPN required, Tor disabled. On a
  Virtual Cell the Hive attests the tunnel before the Cell is ready; on a Real Cell the tier
  describes a VPN the device already runs, and the Hive only verifies it.
- **NIGHT VEIL** (tier 2): Virtual Cells only, asked for by a human; every connection through the
  VPN and Tor, direct routes blocked, local models only, a location-blind profile, and the Cell
  destroyed the moment its task ends.

A Real Cell can be Meadow or Propolis, never Night Veil. The tier belongs to the Cell, not the task:
any task run on a Night Veil Cell is Night Veil work.

## Watch mode and the Patrol

A Real Cell's Warden with no active bees drops into watch mode: a read-only script that observes
what the device's access level allows (processes, resource use, logs and file changes in allowed
folders) and never writes, never captures the screen or the keyboard. Once per Patrol interval it
wakes to review what it saw and raises an Alarm, files a summary, proposes Cell Wax for the device,
or discards it. The Fleet and the Cell page say `watch` or `active` for the mode.

## Alarms

An Alarm is an issue a bee cannot resolve. It climbs the chain, sub-bee to Warden to Queen to you,
and each level handles what its policy table allows: retry, respawn, rebind to a stronger model,
take over, or escalate. You are always last. An Alarm that reaches you is a line in the chat with
an Acknowledge button and a notice under the strip on every screen.

## The Pheromone Mask

Human-like behaviour is not a global mode. Two situational tactics, Write-Like-Human (tone and
pacing) and Mouse-Like-Human (input cadence), can be invoked by a Warden for one bounded task
segment, or forced by the Queen for a whole Cell with a reason and an expiry. The square mask badge
on every row and page says `OFF`, `WARDEN` or `QUEEN FORCED`, and a forced mask shows when it ends.

## When things go wrong

- **Clustering**: when a model provider goes away and no fallback fits, the Queen checkpoints every
  affected bee, pauses their tasks, keeps every lease and Cell alive, and resumes everyone from
  their Handoffs when the provider returns.
- **Requeening**: a crashed Queen rebuilds from the Brood Chamber and the Pheromone Trail and
  reconciles with what every Cell and Warden reports.
- **Supersedure**: moving the Hive Stand to another machine, started by you, with the stores
  copied and verified and never two Queens running.
- **Sting Cut**: a per-Cell emergency disconnect that revokes the lease and ends the Hive's
  processes there, keeping the central audit.
- **Absconding**: tear everything down and release every borrowed device. Human-only, with the
  password again and a typed phrase; never one click.
