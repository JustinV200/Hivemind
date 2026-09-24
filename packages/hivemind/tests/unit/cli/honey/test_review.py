"""Tests for hivemind.cli.honey.review: `hive honey review`, `approve`, `deny` and `--judge`.

Fits into the Hive:
    Mirrors src/hivemind/cli/honey/review.py (codingrules section 3). Drives the real typer app
    against a real fake-provider manifest and SQLite file (`harness`): a ripened Hive Stand
    deposit only the Real Cell floor holds at C2 is stored, its proposal filed the way a House
    Bee with no judge files it, and then listed, decided and judged through the CLI; `--judge`
    runs the real `ModelClearanceJudge` against a scripted JUDGE slot.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.honey.review for the module under test.
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the flow.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from unit.cli.honey.harness import (
    READING_REASON,
    append_toml,
    db_path,
    file_proposals,
    invoke,
    make_hive,
    proposals,
    script_judge,
    seed_eligible,
    trail_events,
)

from hivemind.cell import HoneyClearance
from hivemind.cli.stores import open_honey_store
from hivemind.honey_store import LoweringProposal, LoweringState, Nectar

_TITLE = "Build log"
_TEXT = "pytest passed: 42 tests on the build box in 3.1 seconds."


def _filed(tmp_path: Path, text: str = _TEXT) -> tuple[Path, LoweringProposal]:
    """Write a Hive, store one eligible deposit and file its proposal; return both."""
    manifest = make_hive(tmp_path)
    seed_eligible(manifest, text, title=_TITLE)
    (proposal,) = file_proposals(manifest)
    return manifest, proposal


def _nectar(manifest: Path, proposal: LoweringProposal) -> Nectar:
    """Read back the Nectar a proposal concerns."""
    return asyncio.run(open_honey_store(db_path(manifest)).get_nectar(proposal.nectar_id))


def test_review_says_so_when_nothing_was_ever_proposed(tmp_path: Path) -> None:
    result = invoke(make_hive(tmp_path), "review")

    assert result.exit_code == 0, result.output
    assert "no label lowering proposals" in result.stdout


def test_review_lists_a_waiting_proposal_with_its_labels_title_path_and_reasons(
    tmp_path: Path,
) -> None:
    manifest, proposal = _filed(tmp_path)

    result = invoke(manifest, "review")

    assert result.exit_code == 0, result.output
    assert f"PROPOSED  {proposal.id}  C2 -> C1  attempts=0  /hive/honey_" in result.stdout
    assert _TITLE in result.stdout
    assert f"ripener reason: {READING_REASON}" in result.stdout
    assert "1 waiting for the judge, 0 waiting for the human" in result.stdout


def test_review_json_withholds_a_title_above_the_readers_clearance(tmp_path: Path) -> None:
    manifest, proposal = _filed(tmp_path)

    full = json.loads(invoke(manifest, "review", "--json").stdout)
    ceiling = json.loads(invoke(manifest, "--clearance", "C1", "review", "--json").stdout)

    (entry,) = full["proposals"]
    assert (entry["id"], entry["state"], entry["title"]) == (proposal.id, "PROPOSED", _TITLE)
    assert entry["path"].startswith("/hive/honey_")
    assert (entry["from_label"], entry["to_label"]) == ("C2", "C1")
    (withheld,) = ceiling["proposals"]
    assert withheld["title"] is None  # A C2 deposit's own words stay above a C1 reader.
    assert withheld["path"] == entry["path"]  # Ids are never withheld.
    assert full["is_truncated"] is False


def test_review_lists_waiting_proposals_first_and_says_when_more_exist(tmp_path: Path) -> None:
    manifest, first = _filed(tmp_path)
    seed_eligible(manifest, "ruff check: all checks passed on the build box.")
    (_, second) = file_proposals(manifest)
    assert invoke(manifest, "review", "deny", first.id, "--reason", "Names a host.").exit_code == 0

    listing = json.loads(invoke(manifest, "review", "--json").stdout)
    page = json.loads(invoke(manifest, "review", "--limit", "1", "--json").stdout)

    assert [p["id"] for p in listing["proposals"]] == [second.id, first.id]
    assert [p["state"] for p in listing["proposals"]] == ["PROPOSED", "REJECTED"]
    assert ([p["id"] for p in page["proposals"]], page["is_truncated"]) == ([second.id], True)


def test_review_approve_lowers_the_nectar_as_the_human(tmp_path: Path) -> None:
    manifest, proposal = _filed(tmp_path)

    result = invoke(manifest, "review", "approve", proposal.id, "--reason", "Only test output.")

    assert result.exit_code == 0, result.output
    assert f"lowered: {proposal.id} C2 -> C1 (approver HUMAN)" in result.stdout
    (decided,) = proposals(manifest, LoweringState.LOWERED)
    assert decided.human_reason == "Only test output."
    assert _nectar(manifest, proposal).clearance is HoneyClearance.C1
    (event,) = trail_events(manifest, "honey.label_lowered")
    assert (event.actor, event.payload["approver"]) == ("human", "HUMAN")
    assert "Only test output." not in json.dumps(event.payload)  # Never on the trail.


def test_review_deny_rejects_and_only_the_human_may_still_lower_it(tmp_path: Path) -> None:
    manifest, proposal = _filed(tmp_path)

    denied = invoke(manifest, "review", "deny", proposal.id, "--reason", "Names a host.")
    again = invoke(manifest, "review", "deny", proposal.id, "--reason", "Still no.")
    approved = invoke(manifest, "review", "approve", proposal.id, "--reason", "Checked: fine.")

    assert denied.exit_code == 0, denied.output
    assert f"rejected: {proposal.id} stays C2 (approver HUMAN)" in denied.stdout
    assert again.exit_code == 1
    assert "lowering_transition_refused" in again.stderr
    assert approved.exit_code == 0, approved.output
    assert _nectar(manifest, proposal).clearance is HoneyClearance.C1
    (rejection,) = trail_events(manifest, "honey.lowering_rejected")
    assert (rejection.actor, rejection.payload["outcome"]) == ("human", "REJECT")


def test_review_decisions_refuse_an_unknown_id_and_an_empty_reason(tmp_path: Path) -> None:
    manifest, proposal = _filed(tmp_path)

    unknown = invoke(manifest, "review", "approve", "lowering_nope", "--reason", "Fine.")
    blank = invoke(manifest, "review", "deny", proposal.id, "--reason", "   ")

    assert unknown.exit_code == 1
    assert "lowering_not_found" in unknown.stderr
    assert blank.exit_code == 2
    assert "must say why" in blank.stderr
    assert proposals(manifest, LoweringState.PROPOSED) == (proposal,)  # Nothing was decided.


def test_review_judge_decides_waiting_proposals_on_the_judge_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, proposal = _filed(tmp_path)
    seen = script_judge(monkeypatch, "APPROVE")

    result = invoke(manifest, "review", "--judge")

    assert result.exit_code == 0, result.output
    assert "judge review: lowered=1  rejected=0" in result.stdout
    assert f"LOWERED   {proposal.id}  C2 -> C1" in result.stdout
    assert "judge reasons: Scripted verdict." in result.stdout
    assert "decided: JUDGE at " in result.stdout and "(rubric honey-clearance/1)" in result.stdout
    assert "judge: test-model" in result.stdout
    assert len(seen) == 1
    assert _TEXT in (seen[0].system or "")  # The judge was shown the deposit's whole text.
    (event,) = trail_events(manifest, "honey.label_lowered")
    assert (event.actor, event.payload["approver"]) == ("system", "JUDGE")


def test_review_judge_with_lowering_switched_off_says_why_and_exits_1(tmp_path: Path) -> None:
    manifest, proposal = _filed(tmp_path)
    append_toml(manifest, "[honey.lowering]\nenabled = false")

    result = invoke(manifest, "review", "--judge")

    assert result.exit_code == 1
    assert "judge: none ([honey.lowering] enabled = false)" in result.stdout
    assert proposals(manifest, LoweringState.PROPOSED) == (proposal,)


@pytest.mark.parametrize(
    "args",
    [
        ("review", "--json", "approve", "lowering_x", "--reason", "r"),
        ("review", "--judge", "deny", "lowering_x", "--reason", "r"),
        ("review", "--judge", "--json"),
    ],
)
def test_review_refuses_a_flag_it_would_otherwise_ignore(tmp_path: Path, args: tuple[str]) -> None:
    result = invoke(make_hive(tmp_path), *args)

    assert result.exit_code == 2
    assert "--json" in result.stderr
