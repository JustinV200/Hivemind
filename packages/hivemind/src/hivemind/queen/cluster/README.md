# hivemind.queen.cluster

The cluster package is Clustering: pausing and preserving the Hive's state while a model
provider is unavailable, then resuming every task from its Handoff (roadmap step 4.9,
docs/adr/0024-clustering-protocol.md). The Queen's own mode machine (`QueenMode`, `ClusterState`)
lives one level up, at `hivemind.queen.state`, since it is her own kernel state, not specific to
this package.

## Modules

- `protocol.py`: `cluster(provider, cause, deps, state, wardens)` and
  `resume(provider, deps, state, wardens)`, both pure-decision-then-effects and neither ever
  awaiting a model. Affected bees are found through `deps.ledger.live_grants()` filtered against
  `deps.map` (a live grant's own allowed bindings name the sources its Warden may actually spend
  its sub-bees' model calls against); `cluster` sends each affected Warden an
  `Intervene(HANDOFF)` then a `TaskPause`, moves the task to `PAUSED` and records one
  `queen.clustered`; `resume` looks up each task's newest Bee Bread `HANDOFF` entry, re-assigns
  through `hivemind.queen.dispatcher.resume_paused` (so no work is redone) and records one
  `queen.resumed`. Neither call ever touches a grant, so the same ledger read finds the same bees
  again for `resume`.
- `health.py`: `HealthPoller` polls a clustered provider's `LLMProvider.health()` (never
  `.complete`/`.stream`) on an exponential backoff schedule (`ClusterBackoff`, and the pure
  `next_probe_at(attempt, now)` the schedule is built from).
- `orders.py`: `ClusterOrder`/`OrderKind` are the durable rows `hive cluster`/`hive wake`
  (roadmap step 4.11) write into; `OrderStore` is the persistence seam, with `InMemoryOrderStore`
  for tests and `SqliteOrderStore` (with its own `0001_create_cluster_orders.sql` migration
  beside it) as the durable implementation, following `hivemind.queen.forage.ledger`'s own
  Protocol-plus-two-implementations shape.
- `tick.py`: `run_cluster_tick(deps, state, wardens)` is the single call the orchestrator wires
  into a running Queen's own tick (checks cost caps, drains pending orders, probes clustered
  providers' health); `awake_available(state, deps)` is the pure check her own `_run_awake` hook
  consults to skip an awake episode when her own bound provider is clustered.
- `triggers.py`: `check_cost_caps(deps, state, wardens)`, the cost-cap trigger: clusters a goal's
  own provider once `deps.ledger.spend.headroom` for that goal hits zero.

## How to test this

```bash
uv run pytest packages/hivemind/tests/unit/queen/cluster -q
uv run pytest packages/hivemind/tests/unit/queen/test_state.py -q
uv run pytest packages/hivemind/tests/e2e/test_clustering.py -q -m e2e
```
