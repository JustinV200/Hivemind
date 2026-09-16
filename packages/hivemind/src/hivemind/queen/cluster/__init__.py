"""Provide Clustering: pause and preserve Hive state while a model provider is unavailable.

Every task then resumes from its Handoff, a resumable snapshot of that task's memory. Roadmap step
4.9 gives this package its first real modules: `state` lives one level up
(`hivemind.queen.state`, since a Queen's own mode belongs beside her other kernel state, not
buried in her cluster sub-package) but `protocol` (`cluster`/`resume`, both pure-decision-then-
effects over that mode), `health` (`HealthPoller`, backoff-scheduled provider probing), `orders`
(the durable `hive cluster`/`hive wake` rows the running Queen polls every tick, docs/
adr/0024-clustering-protocol.md), `tick` (`run_cluster_tick`, the one call a Queen's own tick
loop wires in) and `triggers` (the cost-cap check) all live here.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package.
    Handles Clustering: pausing and resuming Hive state around a provider outage. Called by
    `hivemind.queen.queen` (once the orchestrator wires `run_cluster_tick`/`awake_available` in;
    see `hivemind.queen.cluster.tick`'s own module docstring for the exact lines, since this
    dispatch may not edit that file) and by whichever composition root builds a `hive cluster`/
    `hive wake` CLI (roadmap step 4.11, a later dispatch). Calls into sibling packages at Layer 6
    or below, never back up into queen's other sub-packages directly.

Key invariants:
    - `protocol.cluster`/`protocol.resume` never await a model (codingrules section 8.13: the
      Queen's own autopilot runs this same protocol when her own awake slot's provider is down).
    - Every module here keeps `hivemind.queen.deps.QueenDeps`/`WardenLink` as `TYPE_CHECKING`-only
      imports (see `protocol.py`'s own module docstring): `QueenDeps` itself now holds fields from
      this package (`orders`, `health_poller`), so a real import here would cycle back through
      this package's own `__init__.py`, the same trap `hivemind.queen.forage.grants`'s module
      docstring documents and works around for the sibling `forage` sub-package.

See Also:
    - .claude/codingrules.md section 3 for where this sub-package sits under queen.
    - .claude/codingrules.md section 8.13 and docs/adr/0024-clustering-protocol.md for the
      protocol this package implements.
    - .claude/roadmap.md step 4.9 for the deliverable this package's modules satisfy.
    - hivemind.queen.state for QueenMode and ClusterState, the mode machine this package's
      `protocol` module drives.

Public API:
    - ClusterOutcome, ResumeOutcome, cluster, resume: pause and resume one provider (protocol).
    - ClusterBackoff, HealthPoller, next_probe_at: backoff-scheduled health polling (health).
    - ClusterOrder, InMemoryOrderStore, OrderKind, OrderStore, SqliteOrderStore,
      apply_order_migrations, new_order_id: the durable operator-order table (orders).
    - awake_available, run_cluster_tick: the two hooks a running Queen's tick wires in (tick).
    - check_cost_caps: the cost-cap trigger (triggers).
"""

from hivemind.queen.cluster.health import ClusterBackoff, HealthPoller, next_probe_at
from hivemind.queen.cluster.orders import (
    ClusterOrder,
    InMemoryOrderStore,
    OrderKind,
    OrderStore,
    SqliteOrderStore,
    apply_order_migrations,
    new_order_id,
)
from hivemind.queen.cluster.protocol import ClusterOutcome, ResumeOutcome, cluster, resume
from hivemind.queen.cluster.tick import awake_available, run_cluster_tick
from hivemind.queen.cluster.triggers import check_cost_caps

__all__ = [
    "ClusterBackoff",
    "ClusterOrder",
    "ClusterOutcome",
    "HealthPoller",
    "InMemoryOrderStore",
    "OrderKind",
    "OrderStore",
    "ResumeOutcome",
    "SqliteOrderStore",
    "apply_order_migrations",
    "awake_available",
    "check_cost_caps",
    "cluster",
    "new_order_id",
    "next_probe_at",
    "resume",
    "run_cluster_tick",
]
