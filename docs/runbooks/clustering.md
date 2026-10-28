# Clustering: pausing the Hive when it cannot think

Bees in a cold hive cluster together and wait for spring. HiveMind does the same when a model
provider goes away: it pauses every bee bound to that provider, keeps their work warm, and picks it
back up once the provider answers again. Nothing runs half-blind meanwhile. The design is ADR-0024.

## When the Hive clusters by itself

Clustering is per provider. It starts when:

- a provider's health reaches `DOWN` and the slot has no fallback binding Forage allows, or
- a cost cap is reached.

For each bee bound to that provider, the Queen checkpoints it through a Handoff, moves its task to
`PAUSED` and keeps its lease, Cell, heartbeat and watchdog alive. Bees on other providers keep
working. She then polls the provider's health with backoff and resumes every paused bee from its
Handoff when it recovers. Each change records `queen.clustered` or `queen.resumed` on the trail.

The Queen's own awake slot going down is handled the same way. Her autopilot runs Clustering and
never waits on a model, so a Hive whose only provider is down still pauses cleanly.

A resumed bee never redoes finished work. It starts from its Handoff's progress and its do-not-redo
list, and a task the Handoff says was complete is accepted through the normal path instead of being
run again.

## Cluster and wake by hand

You might cluster on purpose, say before you restart a local model server or while a hosted
provider is having a bad day.

```
hive cluster --provider anthropic --manifest hive.toml
hive cluster --manifest hive.toml
```

The first pauses the bees on one provider. The second pauses every bound provider. Wake them with:

```
hive wake anthropic --manifest hive.toml
hive wake --manifest hive.toml
```

`hive wake` with no provider wakes every clustered one. Both commands write an order into the Hive's
database and print its id. The running Queen reads pending orders on every tick, because the leases
live in her process and only she can resume them, so nothing happens until a `hive run` or `hive
serve` is running on that manifest.

Check on orders with:

```
hive cluster status --manifest hive.toml
```

It shows every order still pending, and the ones the Queen already acted on, read back from the
trail.

## Clustering on a Cell that lost the Queen

A Warden on a Swarm device or in a Virtual Cell can lose its link to the Queen instead. What it does
then depends on whether it has a model of its own:

- A Warden with no local model clusters its bees at once, since it has nothing to judge with, and
  keeps them warm.
- A Nuc keeps its bees working on its own model server.

Both run full Clustering once `[supervision] max_offline_s` passes. `nucs.md` covers the rest.

## When a goal stays paused

- `hive llm providers --manifest hive.toml` shows each provider's health. A provider that never
  comes back keeps its bees paused, which is the point.
- Bind a fallback for the slot in `[llm.slots]` so the next outage rebinds instead of clustering
  (`moving-a-slot-to-local.md` shows a local slot with its hosted binding kept as the fallback).
- If you need the work moving now, `hive wake` resumes the bees, and they cluster again if the
  provider is still down.
- `hive run` stopped with Ctrl+C does not cancel a paused goal. Its unfinished tasks run again on the
  next start, from their Handoffs (`requeening.md`).
