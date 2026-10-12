"""Render a metrics snapshot as Prometheus text exposition format 0.0.4, by hand.

Prometheus scrapes plain text: for each metric a ``# HELP`` line, a ``# TYPE`` line and one line
per labelled sample. The format is small and stable, so the Hive writes it itself rather than
depending on a client library for what is a few dozen lines. ``metrics_of`` turns the snapshot into
named metrics (every name is prefixed ``hive_``); ``render`` writes them, escaping label values and
help text as the format requires and printing numbers the way the parser reads them.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.metrics``. Called by
    the Entrance's metrics route with the snapshot it gathered. Pure: calls into the snapshot
    model only.

Key invariants:
    - Every metric name starts with ``hive_`` and matches ``[a-z_][a-z0-9_]*``; every label name
      likewise; a counter's name ends in ``_total``.
    - The output ends with a newline and holds no sample before its metric's ``# TYPE`` line.
    - Label values and help text are escaped, so no value can break out of its line.

See Also:
    - https://prometheus.io/docs/instrumenting/exposition_formats/ for the format.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from hivemind.observation.metrics.snapshot import MetricsSnapshot

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"  # What a scraper expects to be sent.
PREFIX = "hive_"  # Every metric the Hive exposes starts with it.
UNATTRIBUTED = "unattributed"  # The goal label for calls whose lane named neither goal nor grant.

__all__ = ["CONTENT_TYPE", "PREFIX", "Metric", "Sample", "metrics_of", "render", "render_snapshot"]

_Labels = tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Sample:
    """One line of a metric: its labels and value.

    Attributes:
        labels: Label (name, value) pairs, in the order they are written.
        value: The number; an int is written without a decimal point.
    """

    labels: _Labels
    value: float


@dataclass(frozen=True, slots=True)
class Metric:
    """One named metric family.

    Attributes:
        name: The full name, ``hive_...``.
        help: One line saying what it measures.
        kind: ``gauge`` (goes up and down) or ``counter`` (only up, name ends ``_total``).
        samples: Its lines; empty is legal and writes only the header.
    """

    name: str
    help: str
    kind: Literal["gauge", "counter"]
    samples: tuple[Sample, ...]


def render_snapshot(snapshot: MetricsSnapshot) -> str:
    """Render a snapshot as exposition text.

    Args:
        snapshot: The snapshot.

    Returns:
        The text, ready to serve as ``CONTENT_TYPE``.
    """
    return render(metrics_of(snapshot))


def render(metrics: Iterable[Metric]) -> str:
    """Write metrics in exposition format 0.0.4.

    Args:
        metrics: The metrics, in the order to write them.

    Returns:
        Each metric's HELP and TYPE lines then its samples; a final newline ends the text.
    """
    lines: list[str] = []
    for metric in metrics:
        lines.append(f"# HELP {metric.name} {_escape_help(metric.help)}")
        lines.append(f"# TYPE {metric.name} {metric.kind}")
        lines.extend(f"{metric.name}{_labels(s.labels)} {_number(s.value)}" for s in metric.samples)
    return "\n".join(lines) + "\n"


def metrics_of(snapshot: MetricsSnapshot) -> list[Metric]:
    """Name every figure in the snapshot as a Prometheus metric.

    Args:
        snapshot: The snapshot.

    Returns:
        The metrics, summary counters first.
    """
    return [
        *_summary(snapshot),
        *_cells_and_wardens(snapshot),
        *_forage(snapshot),
        *_alarms_tasks_episodes(snapshot),
        *_llm(snapshot),
    ]


def _one(name: str, text: str, value: float, kind: Literal["gauge", "counter"] = "gauge") -> Metric:
    """Build an unlabelled metric."""
    return Metric(PREFIX + name, text, kind, (Sample((), value),))


def _family(
    name: str, text: str, rows: Sequence[tuple[_Labels, float]], kind: str = "gauge"
) -> Metric:
    """Build a labelled metric from (labels, value) rows."""
    return Metric(
        PREFIX + name,
        text,
        "counter" if kind == "counter" else "gauge",
        tuple(Sample(labels, value) for labels, value in rows),
    )


def _summary(snapshot: MetricsSnapshot) -> list[Metric]:
    """The first-class counters of the summary strip."""
    s = snapshot.summary
    return [
        _one("bees_active", "Active bees: active Wardens plus active sub-bees.", s.active_bees),
        _one("bees_awake", "Bees in an awake (LLM) episode in the activity window.", s.bees_awake),
        _one(
            "bees_autopilot",
            "Bees handled only by autopilot in the activity window.",
            s.bees_autopilot,
        ),
        _one("cells_active", "Cells whose Warden reports ACTIVE.", s.cells_active),
        _one("cells_inactive", "Cells that are not active.", s.cells_inactive),
        _one("cells_real", "Real (borrowed) Cells.", s.cells_real),
        _one("cells_virtual", "Virtual (provisioned) Cells.", s.cells_virtual),
    ]


def _cells_and_wardens(snapshot: MetricsSnapshot) -> list[Metric]:
    """Cells by kind and status; Wardens by location."""
    cells = [((("kind", c.kind), ("status", c.status)), c.count) for c in snapshot.cells]
    wardens = [((("location", w.location),), w.count) for w in snapshot.wardens]
    return [
        _family("cells_total", "Cells by kind and headline status.", cells),
        _family("wardens", "Attached Wardens by where they run.", wardens),
    ]


def _forage(snapshot: MetricsSnapshot) -> list[Metric]:
    """Grants, headroom and the reserve."""
    g = snapshot.grants
    return [
        _one("grants_live", "Live Forage grants.", g.live),
        _one("grant_tokens_issued", "Tokens the live grants allow.", g.tokens_issued),
        _one("grant_tokens_spent", "Tokens spent against live grants.", g.tokens_spent),
        _one("grant_spend_issued_usd", "Spend the live grants allow, USD.", g.spend_issued_usd),
        _one("grant_spent_usd", "Spend charged to live grants, USD.", g.spent_usd),
        _one("forage_headroom_sub_bees", "Shared sub-bee slots free.", g.headroom_sub_bees),
        _one("forage_headroom_seats", "Shared model seats free.", g.headroom_shared_seats),
        _one("forage_reserve_seats", "Seats the Royal Reserve holds back.", g.reserve_seats),
    ]


def _alarms_tasks_episodes(snapshot: MetricsSnapshot) -> list[Metric]:
    """Alarms, tasks, episodes, tool promotions and denials."""
    alarms = [((("kind", a.kind), ("level", a.level)), a.count) for a in snapshot.alarms]
    tasks = [((("status", t.status),), t.count) for t in snapshot.tasks]
    awake = [((("principal", e.principal),), e.awake) for e in snapshot.episodes]
    autopilot = [((("principal", e.principal),), e.autopilot) for e in snapshot.episodes]
    return [
        _family("alarms_open", "Open Alarms by kind and level.", alarms),
        _family("tasks", "Tasks by status.", tasks),
        _family("awake_episodes_last_hour", "Awake episodes in the last hour, per bee.", awake),
        _family(
            "autopilot_decisions_last_hour", "Autopilot decisions in the last hour.", autopilot
        ),
        _one(
            "tool_promotions_total",
            "Tools promoted, from the Pheromone Trail.",
            snapshot.tool_promotions,
            "counter",
        ),
        _one(
            "guard_denials_total",
            "Guard denials, from the Pheromone Trail.",
            snapshot.denials,
            "counter",
        ),
    ]


def _llm(snapshot: MetricsSnapshot) -> list[Metric]:
    """Tokens and cost per goal, slot and provider."""
    tokens: list[tuple[_Labels, float]] = []
    cost: list[tuple[_Labels, float]] = []
    for line in snapshot.llm:
        base = (
            ("goal", line.goal_id or UNATTRIBUTED),
            ("slot", line.slot.lower()),
            ("provider", line.provider),
        )
        cost.append((base, line.cost_usd))
        tokens.extend(
            ((*base, ("direction", name)), value)
            for name, value in (
                ("input", line.input_tokens),
                ("output", line.output_tokens),
                ("cached", line.cached_tokens),
            )
        )
    return [
        _family(
            "llm_tokens_total",
            "Model tokens by goal, slot, provider, direction.",
            tokens,
            "counter",
        ),
        _family(
            "llm_cost_usd_total", "Model cost in USD by goal, slot, provider.", cost, "counter"
        ),
    ]


def _labels(labels: _Labels) -> str:
    """Write a sample's label set, ``{a="x",b="y"}``, or nothing for none."""
    if not labels:
        return ""
    return "{" + ",".join(f'{name}="{_escape_value(value)}"' for name, value in labels) + "}"


def _escape_value(value: str) -> str:
    """Escape a label value: backslash, double quote and newline."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(text: str) -> str:
    """Escape help text: backslash and newline."""
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _number(value: float) -> str:
    """Write a number as the parser reads it: ints bare, floats exact, NaN and Inf named."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return repr(value)
