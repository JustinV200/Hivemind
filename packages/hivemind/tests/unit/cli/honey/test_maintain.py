"""Tests for hivemind.cli.honey.maintain: `hive honey ripen`, `reembed [--prune]` and `relabel`.

Fits into the Hive:
    Mirrors src/hivemind/cli/honey/maintain.py (codingrules section 3). Drives the real typer app
    against a real fake-provider manifest and SQLite file (`harness`); an embedder change, or
    switching label lowering off, is a real manifest edit, exactly what an operator does, and the
    JUDGE slot is scripted through the real registry (`harness.script_judge`).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.honey.maintain for the module under test.
    - .claude/phase-7-handoff.md section 8 item 7 for why `ripen --now` had to start draining
      queued operator notes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unit.cli.honey.harness import (
    append_toml,
    bind_embedder,
    invoke,
    make_hive,
    proposals,
    rows,
    script_judge,
    seed_eligible,
    seed_nectar,
    trail_events,
)

import hivemind.cli.honey.maintain as maintain_module
from hivemind.cell import HoneyClearance, hive_stand_cell_id
from hivemind.honey_store import LabelApprover, LoweringState, NectarOrigin
from hivemind.manifest import load_manifest

_BUILD_LOG = "pytest passed: 42 tests on the build box in 3.1 seconds."


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
    assert "0 operator note(s) queued" in result.stdout
    assert rows(manifest) == ()


def test_ripen_without_now_also_counts_queued_operator_notes(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    assert invoke(manifest, "propose", "/hive", "Ops note", "Nothing urgent today.").exit_code == 0

    result = invoke(manifest, "ripen")

    assert result.exit_code == 0, result.output
    assert "0 Nectar waiting to ripen" in result.stdout
    assert "1 operator note(s) queued" in result.stdout
    assert rows(manifest) == ()


def test_ripen_now_drains_a_proposed_note_into_human_honey_attributed_to_the_hive_stand(
    tmp_path: Path,
) -> None:
    """(phase 7 handoff items 4 and 7) a note proposed from the CLI is drained and attributed."""
    manifest = make_hive(tmp_path)
    proposed = invoke(manifest, "propose", "/hive", "Ops note", "Disk usage looked fine today.")
    assert proposed.exit_code == 0, proposed.output

    result = invoke(manifest, "ripen", "--now")

    assert result.exit_code == 0, result.output
    assert "drained 1 operator note(s)" in result.stdout
    expected_cell_id = hive_stand_cell_id(load_manifest(manifest, {}).hive.node_id)
    (row,) = rows(manifest)
    assert row.origin is NectarOrigin.HUMAN
    assert row.cell_id == expected_cell_id


def test_ripen_now_runs_one_pass_and_names_every_slot(tmp_path: Path) -> None:
    manifest = _seeded(tmp_path)

    result = invoke(manifest, "ripen", "--now")

    assert result.exit_code == 0, result.output
    assert "ripened 2 Nectar into 2 Honey rows" in result.stdout
    assert "filed 0 label lowering proposal(s)" in result.stdout  # Declared C1 off a Virtual Cell.
    assert "ripener: test-model" in result.stdout
    assert "embedder: test-model" in result.stdout
    assert "judge: test-model" in result.stdout
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


def test_ripen_now_files_and_the_judge_lowers_an_eligible_deposit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0034: the House Bee's whole pass, run here, files the proposal and asks the judge."""
    manifest = make_hive(tmp_path)
    seed_eligible(manifest, _BUILD_LOG)
    seen = script_judge(monkeypatch, "APPROVE")

    result = invoke(manifest, "ripen", "--now")

    assert result.exit_code == 0, result.output
    assert "filed 1 label lowering proposal(s); judge review: lowered=1" in result.stdout
    assert "judge: test-model" in result.stdout
    assert len(seen) == 1
    (lowered,) = proposals(manifest, LoweringState.LOWERED)
    assert lowered.approver is LabelApprover.JUDGE


def test_ripen_now_with_lowering_switched_off_files_but_never_asks_a_judge(
    tmp_path: Path,
) -> None:
    # The fake JUDGE is unscripted here: any call to it would fail the pass with exit 1.
    manifest = make_hive(tmp_path)
    seed_eligible(manifest, _BUILD_LOG)
    append_toml(manifest, "[honey.lowering]\nenabled = false")

    result = invoke(manifest, "ripen", "--now")

    assert result.exit_code == 0, result.output
    assert "filed 1 label lowering proposal(s); judge review: lowered=0" in result.stdout
    assert "judge: none ([honey.lowering] enabled = false)" in result.stdout
    assert len(proposals(manifest, LoweringState.PROPOSED)) == 1


def test_reembed_prune_drops_every_other_models_vectors_as_the_human(tmp_path: Path) -> None:
    manifest = _seeded(tmp_path)
    assert invoke(manifest, "ripen", "--now").exit_code == 0
    bind_embedder(manifest, model="test-embed-2")

    result = invoke(manifest, "reembed", "--prune")

    assert result.exit_code == 0, result.output
    assert "re-embedded 2 rows for test-embed-2" in result.stdout
    assert "pruned every embedding model but test-embed-2:" in result.stdout
    assert "    test-model: 2 vectors dropped" in result.stdout
    assert "  test-embed-2: 2 vectors" in result.stdout
    assert "  test-model: 2 vectors\n" not in result.stdout
    (event,) = trail_events(manifest, "honey.vectors_pruned")
    assert (event.actor, event.payload["dropped_rows"]) == ("human", 2)


def test_reembed_prune_refuses_while_a_row_lacks_a_vector_and_deletes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _seeded(tmp_path)
    assert invoke(manifest, "ripen", "--now").exit_code == 0
    bind_embedder(manifest, model="test-embed-2")
    # The runaway guard at zero: no embedding pass runs, so the backlog is never drained.
    monkeypatch.setattr(maintain_module, "MAX_REEMBED_PASSES", 0)

    result = invoke(manifest, "reembed", "--prune")

    assert result.exit_code == 1
    assert "prune refused: 2 live row(s) still lack a test-embed-2 vector" in result.stderr
    assert "  test-model: 2 vectors" in result.stdout  # Nothing was deleted.
    assert trail_events(manifest, "honey.vectors_pruned") == []


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
