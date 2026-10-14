"""Tests for hivemind.wardens.ticks.nuc.reply: the three shapes of swarm.nuc_promoted.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/nuc/reply.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.nuc.reply for the module under test.
    - waggle.messages.swarm.colonized for the validators these replies must satisfy.
"""

from __future__ import annotations

from hivemind.wardens.ticks.nuc.reply import demoted, failed, promoted
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_device_id
from waggle.messages.swarm import NucAction, NucOutcome, NucPromote, RuntimeLevel

_CLOCK = FakeClock()


def _order(action: NucAction) -> NucPromote:
    promoting = action is NucAction.PROMOTE
    return NucPromote(
        cell_id=new_cell_id(_CLOCK),
        device_id=new_device_id(_CLOCK),
        action=action,
        server_id="box" if promoting else None,
        initial_models=("small",) if promoting else (),
        deadline_s=30.0,
        reason="A test order.",
    )


def test_a_promoted_reply_names_the_server_and_the_models_on_offer() -> None:
    order = _order(NucAction.PROMOTE)

    reply = promoted(order, ("small", "mid"))

    assert reply.outcome is NucOutcome.PROMOTED and reply.level is RuntimeLevel.NUC
    assert reply.server_id == "box" and reply.loaded_models == ("small", "mid")
    assert (reply.cell_id, reply.device_id, reply.action) == (
        order.cell_id,
        order.device_id,
        order.action,
    )


def test_a_demoted_reply_names_no_server_and_no_models() -> None:
    reply = demoted(_order(NucAction.DEMOTE))

    assert reply.outcome is NucOutcome.DEMOTED and reply.level is RuntimeLevel.COLONIZED
    assert reply.server_id is None and reply.loaded_models == ()


def test_a_failed_reply_keeps_the_rung_it_is_given_and_says_why_in_one_word() -> None:
    for action in NucAction:
        for level in (RuntimeLevel.COLONIZED, RuntimeLevel.NUC):
            reply = failed(_order(action), level, "no_ceilings")

            assert reply.outcome is NucOutcome.FAILED and reply.level is level
            assert reply.server_id is None and reply.loaded_models == ()
            assert "no_ceilings" in reply.reason
