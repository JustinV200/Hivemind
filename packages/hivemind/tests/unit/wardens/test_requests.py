"""Tests for hivemind.wardens.requests: propose_wax.

Fits into the Hive:
    Mirrors src/hivemind/wardens/requests.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.requests for the module under test.
"""

from __future__ import annotations

from hivemind.wardens.requests import WaxProposalInputs, propose_wax
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_warden_id
from waggle.messages.cell.wax import WaxOrigin, WaxSeverity
from waggle.messages.labels import HoneyClearance


def test_propose_wax_builds_a_bee_originated_proposal_naming_the_warden() -> None:
    clock = FakeClock()
    warden_id = new_warden_id(clock)
    inputs = WaxProposalInputs(
        cell_id=new_cell_id(clock),
        severity=WaxSeverity.CAUTION,
        text="This Cell's disk fills up under heavy load.",
        reason="Saw two ENOSPC failures in a row.",
        clearance=HoneyClearance.C1,
    )

    proposed = propose_wax(inputs, warden_id)

    assert proposed.origin is WaxOrigin.BEE
    assert proposed.proposer == warden_id
    assert proposed.cell_id == inputs.cell_id
    assert proposed.severity is WaxSeverity.CAUTION
    assert proposed.text == inputs.text
    assert proposed.reason == inputs.reason
    assert proposed.task_id is None
    assert proposed.expires_at is None


def test_propose_wax_carries_an_optional_task_id_and_expiry() -> None:
    clock = FakeClock()
    warden_id = new_warden_id(clock)
    expires_at = clock.now()
    inputs = WaxProposalInputs(
        cell_id=new_cell_id(clock),
        severity=WaxSeverity.NOTE,
        text="Occasionally slow to boot.",
        reason="Noticed during a routine Patrol.",
        clearance=HoneyClearance.C1,
        task_id=None,
        expires_at=expires_at,
    )

    proposed = propose_wax(inputs, warden_id)

    assert proposed.expires_at == expires_at
