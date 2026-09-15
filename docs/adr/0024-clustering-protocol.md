# ADR-0024: Clustering pauses and preserves per provider, resumes from Handoffs, and takes operator orders through a durable table the running Queen polls

- Status: Accepted
- Date: 2026-09-15

## Context

When a model provider becomes unavailable and no fallback binding fits within Forage (the Hive's
capacity), or a cost cap is hit, the Hive has three choices: fail every affected task, keep running
on autopilot alone, or pause. Codingrules 8.13 chooses pausing ("Clustering", after what a colony
does in cold: it stops foraging and preserves the brood) and this ADR records the protocol's shape
and one decision the roadmap leaves open: how `hive cluster` and `hive wake`, which run in a second
process, reach a Queen that phase 3 runs inside `hive run` with no Hive Entrance until phase 10. The
phase 4 exit criterion is precise about what must survive: after the fake provider is killed
mid-run, the trail shows checkpoint then `PAUSED` for every affected bee, leases stay open, and
`hive wake` after restoring the provider resumes every task from its Handoff (the structured
document a bee writes so a fresh bee can continue) with no duplicated work.

## Decision

**Clustering is per provider and is one mechanism with a new name.** `queen/cluster/protocol.py`
is triggered by `ProviderHealth` reaching `DOWN` with no fallback within Forage, by a cost cap, or
by hand. It checkpoints every bee bound to that provider (the same checkpoint-and-Handoff path
rebind, takeover and threshold reset already use, codingrules 8.9), moves their tasks to `PAUSED`,
keeps their leases, Cells, heartbeats and watchdogs alive, polls the provider's health with
backoff, and resumes every paused bee from its Handoff on recovery or on `hive wake`. Bees bound to
other providers continue untouched. The Queen's own awake slot being unavailable is handled by her
autopilot running the same protocol, which is why nothing in the protocol awaits a model. The
Queen's mode machine (`RUNNING ↔ CLUSTERED`, `queen/state.py`) is per provider set, and every edge
records `queen.clustered` or `queen.resumed`.

**Operator orders are durable rows the running Queen polls, not a second Queen.** `hive cluster
[provider]` and `hive wake` write an order into a small table in the Hive's SQLite owned by
`queen/cluster/`, and the running Queen reads pending orders on every tick and acts on them as
inbox items. This is the same shape `hive inbox answer` already uses in phase 3, where an answer is
written into the Brood Chamber and the running Queen's sync picks it up, and it keeps the exit
criterion literally true: leases are in-process objects, so the process that holds them must be
the one that resumes. When phase 10 lands the Entrance, the order table stays as the durable record
and the Landing Board becomes another writer to it.

**Resume never re-runs finished work.** A resumed bee starts from its Handoff's progress and
do-not-redo lists, on the same slot or the provider's recovered binding; a task whose Handoff says
it was complete is accepted through the Warden's normal acceptance path rather than re-executed.

## Consequences

Positive: a provider outage costs time, never work. Leases and Cells stay warm, so resuming is
fast and Overwintering (phase 5) has a clear hook for long outages. Every pause and resume is on
the trail per bee, so the exit criterion is a trail assertion. `hive wake` after a restart of the
operator's shell still works because the order is a row, not a socket.

Negative: polling a table each tick is a small fixed cost on every Queen tick; it is bounded by one
indexed query and disappears behind the Entrance in phase 10. A Handoff written under pressure may
be incomplete; the phase 4 handoff eval exists to measure that, and a bee whose Handoff cannot be
read resumes from the task's last acceptance state instead. Health polling with backoff means a
short outage is noticed at the next poll, not instantly.

## Alternatives considered

Running the Hive on autopilot alone until the provider returns: rejected by codingrules 8.13; the
deterministic table cannot plan or judge, so work would either stall silently or land uncapped.

Failing affected tasks and letting the human re-run the goal: loses in-flight work that Handoffs
already capture, and turns every provider blip into a human interruption.

Releasing leases and destroying Cells on cluster, re-acquiring on wake: cleaner resource-wise, but
it discards scratch state a Handoff references, so a resumed bee could not find its own files, and
it contradicts the exit criterion's "leases stay open".

`hive wake` starting a fresh Queen against the stores: works for tasks but cannot reach leases held
by the still-running `hive run` process, and would risk two Queens over one Brood Chamber.
