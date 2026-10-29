Forage is everything the Hive has to spend. It is several things rather than one number, and the
[Forage screen](#/forage) draws how it is divided.

## What Forage is

- **Host compute**: cores, memory, disk, GPUs and VRAM, reported by every Cell and the Hive Stand.
- **Model seats**: one concurrent request on one model on one server. Seats run out first, so they
  are counted, never estimated. For a hosted provider the equivalent is its rate limits and caps.
- **Spend**: money for hosted models, capped per day and per goal.
- **Bee slots**: how many bees a Cell can carry, derived from the above and each role's footprint
  (what one Drone or Forager costs in CPU, memory, a seat while mid-call, and tokens per hour).

## The Forage map

Every model that can be served, wherever it lives, is a source on the map with a **grade** from 1
to 5 (how strong it is), a **distance** (measured latency and speed from a given Cell), an
**abundance** (free seats) and a cost. A task's tempo sets the minimum grade and the maximum
distance; routing picks the cheapest source that clears both and has a seat free. The Queen herself
always runs on the highest grade available.

## Two pools

The rule is simple: **the Queen divides what is shared; a Warden divides what is on its own Cell,
under ceilings the Queen set once.**

The **shared pool** is seats on the Hive Stand's model servers, seats and spend at hosted
providers, and new Cells. Only the Queen allocates it, after holding back a **Royal Reserve** for
herself, the Attendant and the House Bees. A **local pool** is everything physically on one Cell:
its cores, memory, VRAM and any seats on a model server running on it. The Cell's Warden owns that
outright and reports usage; it never asks. Spend is the one dimension that is always shared, since
every Cell draws from one wallet.

## Grants

A grant is what a Warden receives from the shared pool: which model bindings it may hand its
sub-bees, how many shared seats it holds, and its token and spend budgets. The Queen computes it
from the role footprints, the task's tempo, the goal's remaining caps and what the map says is
reachable from that Cell. A Warden spends within its grant without asking and files a request with
a reason for more; the Queen answers by rule when there is headroom and by judgement when there is
not. Grants are leases: renewed on the Warden's heartbeat, returned when it stops, shrunk when
measurements drift. A Warden over its grant gets an Alarm, not a crash.

## The Fanner

Every model call from any bee passes through the Fanner, named for the bees that fan to regulate
the hive. It enforces the seat count, queues overflow by tempo, measures speed and latency, and
reports usage, so a grant is a fact rather than a suggestion.

## Slots and hosting plans

Every bee asks for a model **slot** (queen, warden, worker, attendant, judge, ripener, embedder,
transcriber) and the manifest maps each slot to a provider, hosted or local. Each Cell carries a
hosting plan the Queen writes: for every slot a first choice and a fallback chain. A Cell with its
own models is local first, shared when needed: the Fanner spills to the next binding only when the
local grade is below what the task demands, the model needed is not loaded locally, or the local
queue has waited too long for the task's latency budget. Every spill is on the trail. On a Night
Veil Cell every slot is local and nothing spills.

## Tempo

Every task carries a tempo: how fast it must finish and how right it must be. An urgent, low-stakes
task gets a fast model at low effort, more parallelism and the shortest checking its risk allows; a
critical one gets the strongest model at high effort and a second judge. Tempo never bends access
levels, permissions or the promise to leave a device as found.

## Cost

Every model call carries a normalised usage record: input, output and cached tokens, and the cost
when the manifest lists a price for that model. The [Cost screen](#/cost) sums those by goal, by
slot and by provider, and says for each slot what moving it to a local model would save. A call
that named no goal lands in an unattributed row, kept last and explained.
