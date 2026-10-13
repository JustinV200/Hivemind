"""Hold the Observation Hive's metrics: one snapshot of the Hive's figures, and its Prometheus text.

Operators watch the Hive from outside as well as from the Observation Hive, so the same facts the
screens show are published as numbers: ``snapshot`` defines the frozen ``MetricsSnapshot`` and the
pure builder that derives it from the records the Entrance gathers; ``exposition`` renders it as
Prometheus text exposition format 0.0.4, written by hand (no client library). The Entrance serves
the text at ``GET /v1/metrics``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation``. Used by
    ``hivemind.entrance.reads.screens.metrics`` and ``hivemind.entrance.routes.screens.metrics``
    through ``hivemind.observation``'s face; never imports the Entrance. Calls into the sibling
    ``api`` package, the views and pydantic.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Nothing here reads or writes a store: the snapshot is built from values handed in.

See Also:
    - .claude/roadmap.md step 12.1 for the figures the snapshot carries.

Public API:
    - MetricsSnapshot, MetricsInputs, metrics_snapshot and the count models (snapshot).
    - render_snapshot, render, metrics_of, Metric, Sample, CONTENT_TYPE (exposition).
"""

from hivemind.observation.metrics.exposition import (
    CONTENT_TYPE,
    PREFIX,
    Metric,
    Sample,
    metrics_of,
    render,
    render_snapshot,
)
from hivemind.observation.metrics.snapshot import (
    EPISODE_WINDOW,
    MAX_COST_SERIES,
    UNKNOWN_LOCATION,
    AlarmCount,
    CellCount,
    GrantTotals,
    MetricsInputs,
    MetricsSnapshot,
    PrincipalCount,
    StatusCount,
    WardenCount,
    metrics_snapshot,
)

__all__ = [
    "CONTENT_TYPE",
    "EPISODE_WINDOW",
    "MAX_COST_SERIES",
    "PREFIX",
    "UNKNOWN_LOCATION",
    "AlarmCount",
    "CellCount",
    "GrantTotals",
    "Metric",
    "MetricsInputs",
    "MetricsSnapshot",
    "PrincipalCount",
    "Sample",
    "StatusCount",
    "WardenCount",
    "metrics_of",
    "metrics_snapshot",
    "render",
    "render_snapshot",
]
