"""Tests for hivemind.cli.honey.maintain: `hive honey ripen`, `reembed` and `relabel`.

Fits into the Hive:
    Mirrors src/hivemind/cli/honey/maintain.py (codingrules section 3). Drives the real typer app
    against a real fake-provider manifest and SQLite file (`harness`); an embedder change is a
    real manifest edit, exactly what an operator does.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.honey.maintain for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from unit.cli.honey.harness import (
    bind_embedder,
    invoke,
    make_hive,
    rows,
    seed_nectar,
    trail_events,
)

from hivemind.cell import HoneyClearance


def _seeded(tmp_path: Path, count: int = 2) -> Path:
    """Write a Hive and deposit `count` distinct C1 Nectar, not yet ripened."""
    manifest = make_hive(tmp_path)
    for index in range(count):
        seed_nectar(manifest, f"Finding {index}: the widget factory's config lives in /etc.")
    return manifest


def test_ripen_without_now_only_says_how_much_is_waiting(tmp_path: Path) -> None:
    manifest = _seeded(tmp_path)

    result = invoke(manifest, "ripen")

    assert result.exit_code == 0, result.output
    assert "2 Nectar waiting to ripen" in result.stdout
    assert rows(manifest) == ()


def test_ripen_now_runs_one_pass_and_names_both_slots(tmp_path: Path) -> None:
    manifest = _seeded(tmp_path)

    result = invoke(manifest, "ripen", "--now")

    assert result.exit_code == 0, result.output
    assert "ripened 2 Nectar into 2 Honey rows" in result.stdout
    assert "ripener: test-model" in result.stdout
    assert "embedder: test-model" in result.stdout
    assert len(rows(manifest)) == 2
    assert len(trail_events(manifest, "honey.ripened")) == 2


def test_ripen_now_without_an_embedder_still_ripens_and_says_why(tmp_path: Path) -> None:
    manifest = _seeded(tmp_path)
    bind_embedder(manifest, hosted=True)

    result = invoke(manifest, "ripen", "--now")

    assert result.exit_code == 0, result.output
    assert "ripened 2 Nectar" in result.stdout
    assert "embedder: none (hivemind.llm.embedding_unsupported" in result.stdout
    assert all(row.embedding_model is None for row in rows(manifest))


def test_reembed_after_an_embedder_change_counts_vectors_per_model(tmp_path: Path) -> None:
    manifest = _seeded(tmp_path)
    assert invoke(manifest, "ripen", "--now").exit_code == 0
    bind_embedder(manifest, model="test-embed-2")

    first = invoke(manifest, "reembed")
    again = invoke(manifest, "reembed")

    assert first.exit_code == 0, first.output
    assert "re-embedded 2 rows for test-embed-2" in first.stdout
    assert "test-embed-2: 2 vectors" in first.stdout
    assert "test-model: 2 vectors" in first.stdout
    assert again.exit_code == 0, again.output
    assert "re-embedded 0 rows for test-embed-2" in again.stdout


def test_reembed_without_an_embedder_exits_1_with_the_reason(tmp_path: Path) -> None:
    manifest = _seeded(tmp_path)
    bind_embedder(manifest, hosted=True)

    result = invoke(manifest, "reembed")

    assert result.exit_code == 1
    assert "embedder: none (hivemind.llm.embedding_unsupported" in result.stdout
    assert "nothing can be re-embedded" in result.stdout


def test_relabel_raises_then_lowers_and_records_both(tmp_path: Path) -> None:
    manifest = _seeded(tmp_path, count=1)
    assert invoke(manifest, "ripen", "--now").exit_code == 0
    (row,) = rows(manifest)

    raised = invoke(manifest, "relabel", row.path, "C2", "--reason", "Names an internal host.")
    lowered = invoke(manifest, "relabel", row.path, "C0", "--reason", "Reviewed: public.")
    unchanged = invoke(manifest, "relabel", row.path, "C0", "--reason", "Checked again.")

    assert raised.exit_code == 0, raised.output
    assert f"raised: {row.path} C1 -> C2" in raised.stdout
    assert f"lowered: {row.path} C2 -> C0" in lowered.stdout
    assert "unchanged" in unchanged.stdout
    (after,) = rows(manifest)
    assert after.clearance is HoneyClearance.C0
    (raise_event,) = trail_events(manifest, "honey.label_raised")
    (lower_event,) = trail_events(manifest, "honey.label_lowered")
    assert raise_event.actor == lower_event.actor == "human"
    assert lower_event.payload["approver"] == "HUMAN"
    assert lower_event.payload["reason"] == "Reviewed: public."


def test_relabel_refuses_what_the_readers_ceiling_hides(tmp_path: Path) -> None:
    manifest = _seeded(tmp_path, count=1)
    assert invoke(manifest, "ripen", "--now").exit_code == 0
    (row,) = rows(manifest)
    assert invoke(manifest, "relabel", row.path, "C2", "--reason", "Royal.").exit_code == 0

    result = invoke(manifest, "--clearance", "C1", "relabel", row.path, "C0", "--reason", "r")

    assert result.exit_code == 1
    assert "browse_not_found" in result.stderr
