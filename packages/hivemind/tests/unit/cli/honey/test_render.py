"""Tests for hivemind.cli.honey.render: how listings, documents and slot status are printed.

Most of this module is exercised through the commands (test_query.py, test_browse.py,
test_maintain.py, test_review.py); this covers the pieces a command test cannot pin down on its
own: the JSON kind of each document shape, both forms of a slot's status line, every form of a
prune's result, and a proposal whose title is withheld and whose decision was the human's.

Fits into the Hive:
    Mirrors src/hivemind/cli/honey/render.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.honey.render for the module under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from unit.honey_store.browse.harness import open_browse_hive

from hivemind.cell import HoneyClearance
from hivemind.cli.honey.context import SlotStatus
from hivemind.cli.honey.render import (
    ReviewEntry,
    document_json,
    print_prune,
    print_review_entry,
    print_slot,
)
from hivemind.honey_store import (
    LabelApprover,
    LoweringId,
    LoweringProposal,
    LoweringState,
    PruneOutcome,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_nectar_id


async def test_document_json_names_each_document_kind(tmp_path: Path) -> None:
    hive = await open_browse_hive(tmp_path)
    (row,) = await hive.ripen("A row.")
    wax = hive.wax_note(new_cell_id(hive.clock))
    entry = hive.bee_bread_note()

    kinds = [json.loads(document_json("/x", document))["kind"] for document in (row, wax, entry)]

    assert kinds == ["HONEY", "WAX", "BEE_BREAD"]


def test_print_slot_names_the_model_or_the_reason(capsys: pytest.CaptureFixture[str]) -> None:
    print_slot("embedder", SlotStatus(model="test-model", reason=None), "unused")
    print_slot("ripener", SlotStatus(model=None, reason="code: why"), "summaries are heuristic")

    out = capsys.readouterr().out.splitlines()

    assert out == ["embedder: test-model", "ripener: none (code: why); summaries are heuristic"]


def test_print_prune_names_each_dropped_model_or_the_refusal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_prune(PruneOutcome(kept_model="embed-2", dropped={"embed-1": 3}))
    print_prune(PruneOutcome(kept_model="embed-2"))
    print_prune(PruneOutcome(kept_model="embed-2", missing=2))

    captured = capsys.readouterr()

    assert captured.out.splitlines() == [
        "pruned every embedding model but embed-2:",
        "    embed-1: 3 vectors dropped",
        "pruned nothing: every stored vector is already embed-2's",
    ]
    assert captured.err.splitlines() == [
        "prune refused: 2 live row(s) still lack a embed-2 vector; nothing was deleted"
    ]


def test_print_review_entry_marks_a_withheld_title_and_the_humans_decision(
    capsys: pytest.CaptureFixture[str],
) -> None:
    clock = FakeClock()
    proposal = LoweringProposal(
        id=LoweringId("lowering_01ARZ3NDEKTSV4RRFFQ69G5FAV"),
        nectar_id=new_nectar_id(clock),
        from_label=HoneyClearance.C2,
        to_label=HoneyClearance.C1,
        state=LoweringState.REJECTED,
        approver=LabelApprover.HUMAN,
        human_reason="Names a host.",
        proposed_at=clock.now(),
        decided_at=clock.now(),
    )

    print_review_entry(ReviewEntry(proposal=proposal, title=None, path=None))

    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"REJECTED  {proposal.id}  C2 -> C1  attempts=0  -  (title above --clearance)",
        "    ripener reason: -",
        "    judge reasons: -",
        f"    decided: HUMAN at {clock.now().isoformat()}",
        "    human reason: Names a host.",
    ]
