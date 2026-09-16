"""Tests for hivemind.queen.human_inbox: propose_wax_from_chat.

Fits into the Hive:
    Mirrors src/hivemind/queen/human_inbox.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.human_inbox for the module under test.
"""

from __future__ import annotations

from hivemind.queen.human_inbox import ChatWaxProposal, propose_wax_from_chat
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.cell.wax import WaxOrigin, WaxSeverity
from waggle.messages.labels import HoneyClearance


def test_propose_wax_from_chat_sets_origin_human_and_no_proposer() -> None:
    clock = FakeClock()
    proposal = ChatWaxProposal(
        cell_id=new_cell_id(clock),
        severity=WaxSeverity.BLOCK,
        text="Never run untrusted code on this Cell; it also runs payroll.",
        reason="The operator said so directly.",
        clearance=HoneyClearance.C2,
    )

    proposed = propose_wax_from_chat(proposal)

    assert proposed.origin is WaxOrigin.HUMAN
    assert proposed.proposer is None
    assert proposed.cell_id == proposal.cell_id
    assert proposed.severity is WaxSeverity.BLOCK
    assert proposed.task_id is None
