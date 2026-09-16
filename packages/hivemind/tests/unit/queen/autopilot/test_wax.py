"""Tests for hivemind.queen.autopilot.wax: decide_wax_proposal.

Fits into the Hive:
    Mirrors src/hivemind/queen/autopilot/wax.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.autopilot.wax for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.queen.autopilot.wax import WaxAutopilotOutcome, WaxProposalSignal, decide_wax_proposal
from waggle.messages.cell.wax import WaxSeverity


def test_a_wardens_note_about_its_own_cell_within_the_cap_is_autopilot_written() -> None:
    signal = WaxProposalSignal(
        severity=WaxSeverity.NOTE,
        proposer_is_warden_about_own_cell=True,
        written_count_for_cell=0,
        cap=20,
    )

    assert decide_wax_proposal(signal) is WaxAutopilotOutcome.AUTOPILOT_WRITE


def test_a_wardens_caution_about_its_own_cell_within_the_cap_is_autopilot_written() -> None:
    signal = WaxProposalSignal(
        severity=WaxSeverity.CAUTION,
        proposer_is_warden_about_own_cell=True,
        written_count_for_cell=19,
        cap=20,
    )

    assert decide_wax_proposal(signal) is WaxAutopilotOutcome.AUTOPILOT_WRITE


def test_a_block_always_needs_judgement_even_about_its_own_cell_within_the_cap() -> None:
    signal = WaxProposalSignal(
        severity=WaxSeverity.BLOCK,
        proposer_is_warden_about_own_cell=True,
        written_count_for_cell=0,
        cap=20,
    )

    assert decide_wax_proposal(signal) is WaxAutopilotOutcome.NEEDS_JUDGEMENT


def test_a_proposal_not_from_the_cells_own_warden_needs_judgement() -> None:
    signal = WaxProposalSignal(
        severity=WaxSeverity.NOTE,
        proposer_is_warden_about_own_cell=False,
        written_count_for_cell=0,
        cap=20,
    )

    assert decide_wax_proposal(signal) is WaxAutopilotOutcome.NEEDS_JUDGEMENT


@pytest.mark.parametrize("written_count_for_cell", [20, 21])
def test_a_proposal_at_or_over_the_cap_needs_judgement(written_count_for_cell: int) -> None:
    signal = WaxProposalSignal(
        severity=WaxSeverity.NOTE,
        proposer_is_warden_about_own_cell=True,
        written_count_for_cell=written_count_for_cell,
        cap=20,
    )

    assert decide_wax_proposal(signal) is WaxAutopilotOutcome.NEEDS_JUDGEMENT


def test_decide_wax_proposal_is_pure() -> None:
    signal = WaxProposalSignal(
        severity=WaxSeverity.NOTE,
        proposer_is_warden_about_own_cell=True,
        written_count_for_cell=0,
        cap=20,
    )

    first = decide_wax_proposal(signal)
    second = decide_wax_proposal(signal)

    assert first is second is WaxAutopilotOutcome.AUTOPILOT_WRITE
