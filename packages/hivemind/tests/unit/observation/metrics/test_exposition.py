"""Test hivemind.observation.metrics.exposition: Prometheus text exposition format 0.0.4, by hand.

The text for a small snapshot is checked line for line (HELP, TYPE, labelled samples); label values
and help text are escaped so none can break out of its line; numbers print as a parser reads them;
every metric name is hive_-prefixed and well formed, a counter's ends in _total, and no sample
precedes its TYPE line.

Fits into the Hive:
    Mirrors src/hivemind/observation/metrics/exposition.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import re

from builders.observation import after, make_cell_view, make_llm_call, make_warden_view

from hivemind.cell import HIVE_STAND_SOURCE
from hivemind.observation import (
    CellView,
    CostLine,
    HeadroomView,
    MetricsInputs,
    MetricsSnapshot,
    ReserveView,
    SummaryInputs,
    WardenView,
    cost_lines,
    metrics_snapshot,
    summary_view,
)
from hivemind.observation.metrics import CONTENT_TYPE, Metric, Sample, metrics_of, render
from waggle.clock import FakeClock
from waggle.ids import new_task_id

_NOW = after(100)


def _snapshot(
    cells: list[CellView], wardens: list[WardenView], lines: list[CostLine]
) -> MetricsSnapshot:
    """A snapshot of a tiny Hive: the given Cells, Wardens and cost lines, all else empty."""
    summary = summary_view(SummaryInputs(cells, wardens, [], [], (), _NOW))
    return metrics_snapshot(
        MetricsInputs(
            summary=summary,
            cells=cells,
            wardens=wardens,
            alarms=(),
            grants=[],
            headroom=HeadroomView(sub_bees=3, shared_seats=2),
            reserve=ReserveView(seats=1, memory_bytes=0, headroom_fraction=0.1),
            episodes=[],
            task_counts={},
            tool_promotions=1,
            denials=2,
            cost_lines=lines,
            as_of=_NOW,
        )
    )


def test_an_empty_hive_still_names_every_family_and_the_first_class_counters() -> None:
    text = render(metrics_of(_snapshot([], [], [])))

    lines = text.splitlines()
    assert text.endswith("\n")
    assert "# TYPE hive_bees_active gauge" in lines
    assert "hive_bees_active 0" in lines
    assert "hive_cells_active 0" in lines and "hive_cells_inactive 0" in lines
    assert "hive_cells_real 0" in lines and "hive_cells_virtual 0" in lines
    assert "hive_bees_awake 0" in lines and "hive_bees_autopilot 0" in lines
    assert "# TYPE hive_cells_total gauge" in lines  # Present, with no samples yet.
    assert not any(line.startswith("hive_cells_total") for line in lines)


def _expected_blocks(goal: str) -> list[list[str]]:
    """Each metric's lines for the small snapshot, contiguous and in order, as a scraper reads."""
    tokens = f'goal="{goal}",slot="worker",provider="cloud"'
    blocks = [
        [
            "# HELP hive_bees_active Active bees: active Wardens plus active sub-bees.",
            "# TYPE hive_bees_active gauge",
            "hive_bees_active 1",
        ],
        [
            "# HELP hive_cells_total Cells by kind and headline status.",
            "# TYPE hive_cells_total gauge",
            'hive_cells_total{kind="real",status="active"} 1',
        ],
        [
            "# HELP hive_wardens Attached Wardens by where they run.",
            "# TYPE hive_wardens gauge",
            'hive_wardens{location="hive_stand"} 1',
        ],
        [
            "# HELP hive_forage_headroom_sub_bees Shared sub-bee slots free.",
            "# TYPE hive_forage_headroom_sub_bees gauge",
            "hive_forage_headroom_sub_bees 3",
        ],
        [
            "# HELP hive_tool_promotions_total Tools promoted, from the Pheromone Trail.",
            "# TYPE hive_tool_promotions_total counter",
            "hive_tool_promotions_total 1",
        ],
        [
            "# HELP hive_llm_tokens_total Model tokens by goal, slot, provider, direction.",
            "# TYPE hive_llm_tokens_total counter",
            f'hive_llm_tokens_total{{{tokens},direction="input"}} 100',
            f'hive_llm_tokens_total{{{tokens},direction="output"}} 40',
            f'hive_llm_tokens_total{{{tokens},direction="cached"}} 0',
        ],
        [
            "# HELP hive_llm_cost_usd_total Model cost in USD by goal, slot, provider.",
            "# TYPE hive_llm_cost_usd_total counter",
            f"hive_llm_cost_usd_total{{{tokens}}} 0.5",
        ],
    ]
    return blocks


def test_the_text_for_a_small_snapshot_is_exactly_these_lines() -> None:
    warden = make_warden_view()
    cell = make_cell_view(id=warden.cell_id, warden_id=warden.id, source=HIVE_STAND_SOURCE)
    goal = new_task_id(FakeClock())
    lines = cost_lines(
        [make_llm_call(slot="WORKER", provider="cloud", cost_usd=0.5, lane={"goal_id": goal})], {}
    )

    text = render(metrics_of(_snapshot([cell], [warden], lines)))

    # Each block is one metric's lines, contiguous and in this order, exactly as a scraper reads.
    for block in _expected_blocks(goal):
        assert "\n".join(block) + "\n" in text, block


def test_label_values_and_help_text_are_escaped_and_numbers_print_for_a_parser() -> None:
    metric = Metric(
        "hive_probe",
        "Two\nlines and a back\\slash.",
        "gauge",
        (
            Sample((("who", 'say "hi"\\\n'),), 1),
            Sample((), 0.25),
            Sample((), float("inf")),
            Sample((), float("-inf")),
            Sample((), float("nan")),
        ),
    )

    assert render([metric]).splitlines() == [
        "# HELP hive_probe Two\\nlines and a back\\\\slash.",
        "# TYPE hive_probe gauge",
        'hive_probe{who="say \\"hi\\"\\\\\\n"} 1',
        "hive_probe 0.25",
        "hive_probe +Inf",
        "hive_probe -Inf",
        "hive_probe NaN",
    ]


def test_every_name_is_well_formed_and_no_sample_precedes_its_type_line() -> None:
    warden = make_warden_view()
    cell = make_cell_view(id=warden.cell_id, warden_id=warden.id)
    metrics = metrics_of(_snapshot([cell], [warden], []))

    for metric in metrics:
        assert re.fullmatch(r"hive_[a-z][a-z0-9_]*", metric.name), metric.name
        if metric.kind == "counter":
            assert metric.name.endswith("_total"), metric.name
        for sample in metric.samples:
            assert all(re.fullmatch(r"[a-z_][a-z0-9_]*", name) for name, _ in sample.labels)
    typed: set[str] = set()
    for line in render(metrics).splitlines():
        if line.startswith("# TYPE "):
            typed.add(line.split()[2])
        elif not line.startswith("#"):
            assert re.split(r"[{ ]", line, maxsplit=1)[0] in typed, line
    assert CONTENT_TYPE == "text/plain; version=0.0.4; charset=utf-8"
