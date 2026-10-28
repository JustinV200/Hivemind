"""Test the relocation rules: who may speak as the Queen, where to dial, and when the old node goes.

When the Hive Stand (the machine the Queen, the Hive's central orchestrator, runs on) moves, a
signed ``control.queen_moved`` tells the Pollen Packet (the thin gateway program on a device the
Hive borrows) the new address and node key (enrolment.md section 12). The packet acts only on a
notice a Hive node signed whose sequence is above the highest it has accepted, pins the new node's
key beside the old one, dials the new address from ``effective_at`` (the old stays a fallback), and
at ``grace_until`` forgets the old key and address entirely, so the machine that stepped down keeps
no authority. Each address is dialled with the certificate pin it came with, the new one from the
notice itself (ADR-0065). A notice that would swap a key under a node already trusted is refused.
These rules are pure functions of a state and a time, tested directly with a fake clock.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.agent.moved.

Key invariants:
    - A stale, replayed or foreign-signed notice changes nothing.
    - At and after ``grace_until`` the old node has no authority and no key.

See Also:
    - docs/waggle/enrolment.md section 12.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from states import sample_state

from pollen.agent import accept_moved, dial_plan, hive_nodes, pin_for, settle, trusted_keys
from pollen.state import PacketState
from waggle.clock import FakeClock
from waggle.ids import NodeId, new_node_id
from waggle.messages.control import QueenMoved
from waggle.signing import Ed25519Signer, public_key_from_hex, public_key_hex

CLOCK = FakeClock()
NEW_URL = "ws://127.0.0.1:9000"


def _key() -> str:
    """Make a fresh public key in hex."""
    return public_key_hex(Ed25519Signer.generate().public_key_bytes)


def _notice(sequence: int = 1, **changes: object) -> QueenMoved:
    """Build a relocation notice that takes effect in one minute and ends its grace in an hour."""
    now = CLOCK.now()
    values: dict[str, object] = {
        "sequence": sequence,
        "new_address": NEW_URL,
        "new_node_id": new_node_id(CLOCK),
        "new_node_public_key_hex": _key(),
        "effective_at": now + timedelta(minutes=1),
        "grace_until": now + timedelta(hours=1),
        "reason": "The Hive Stand is moving.",
    }
    values.update(changes)
    return QueenMoved(**values)


def _accepted(state: PacketState, notice: QueenMoved, signer: NodeId | None = None) -> PacketState:
    """Accept ``notice`` as signed by the Hive node (or ``signer``), asserting it is taken."""
    result = accept_moved(state, notice, signer or state.hive_node_id, CLOCK.now())
    assert result is not None
    return result


def test_with_no_relocation_only_the_pinned_node_speaks_for_the_queen() -> None:
    state = sample_state(CLOCK)
    assert hive_nodes(state, CLOCK.now()) == {state.hive_node_id}
    assert dial_plan(state, CLOCK.now()).urls == (state.queen_url,)
    assert settle(state, CLOCK.now()) is state


def test_the_verifier_trusts_every_listed_node_and_the_pinned_key_always_wins() -> None:
    state = sample_state(CLOCK)
    other = new_node_id(CLOCK)
    forged_listing = state.model_copy(
        update={
            "trusted_nodes": (
                *state.trusted_nodes,
                state.trusted_nodes[0].model_copy(update={"public_key_hex": _key()}),
                state.trusted_nodes[0].model_copy(update={"node_id": other}),
            )
        }
    )
    keys = trusted_keys(forged_listing)
    assert keys[state.hive_node_id] == public_key_from_hex(state.hive_key_hex)
    assert set(keys) == {state.hive_node_id, other}


def test_an_accepted_notice_pins_the_new_node_and_keeps_the_old_one_for_now() -> None:
    state = sample_state(CLOCK)
    notice = _notice()
    moved = _accepted(state, notice)
    assert moved.moved_sequence == 1 and moved.queen_move is not None
    assert moved.queen_move.previous_node_id == state.hive_node_id
    assert moved.queen_move.new_url == NEW_URL
    now = CLOCK.now()
    assert hive_nodes(moved, now) == {state.hive_node_id, notice.new_node_id}
    keys = trusted_keys(moved)
    assert public_key_from_hex(notice.new_node_public_key_hex) == keys[notice.new_node_id]
    assert state.hive_node_id in keys


def test_the_old_address_is_dialled_until_the_move_takes_effect_then_the_new_one_first() -> None:
    state = sample_state(CLOCK)
    moved = _accepted(state, _notice())
    now = CLOCK.now()
    assert dial_plan(moved, now).urls == (state.queen_url,)
    assert dial_plan(moved, now + timedelta(minutes=2)).urls == (NEW_URL, state.queen_url)


def test_at_the_end_of_the_grace_the_old_node_loses_its_key_its_authority_and_its_address() -> None:
    state = sample_state(CLOCK)
    notice = _notice()
    moved = _accepted(state, notice)
    just_before = CLOCK.now() + timedelta(minutes=59)
    assert settle(moved, just_before) is moved
    after = CLOCK.now() + timedelta(hours=1)
    settled = settle(moved, after)
    assert settled.queen_move is None and settled.moved_sequence == 1
    assert (settled.queen_url, settled.hive_node_id) == (NEW_URL, notice.new_node_id)
    assert settled.hive_key_hex == notice.new_node_public_key_hex
    assert state.hive_node_id not in trusted_keys(settled)
    assert hive_nodes(settled, after) == {notice.new_node_id}
    assert dial_plan(settled, after).urls == (NEW_URL,)


@pytest.mark.parametrize("sequence", [0, 1, 2])
def test_a_notice_not_above_the_highest_accepted_is_ignored(sequence: int) -> None:
    state = sample_state(CLOCK, moved_sequence=2)
    if sequence >= 1:
        assert accept_moved(state, _notice(sequence), state.hive_node_id, CLOCK.now()) is None
    assert accept_moved(state, _notice(3), state.hive_node_id, CLOCK.now()) is not None


def test_a_notice_not_signed_by_a_hive_node_is_ignored() -> None:
    state = sample_state(CLOCK)
    stranger = new_node_id(CLOCK)
    assert accept_moved(state, _notice(), stranger, CLOCK.now()) is None


def test_each_address_is_dialled_with_its_own_pin_and_the_new_pin_survives_the_grace() -> None:
    old_pin, new_pin, new_url = "ab" * 32, "cd" * 32, "wss://new.example.net:8720"
    state = sample_state(CLOCK, queen_url="wss://hive.example.net:8720", tls_pin_sha256=old_pin)
    moved = _accepted(state, _notice(new_address=new_url, new_tls_pin_sha256=new_pin))
    during = CLOCK.now() + timedelta(minutes=2)
    assert [pin_for(moved, url) for url in dial_plan(moved, during).urls] == [new_pin, old_pin]
    settled = settle(moved, CLOCK.now() + timedelta(hours=1))
    assert (settled.queen_url, settled.tls_pin_sha256) == (new_url, new_pin)
    # Moving to a loopback ws:// address drops the pin with the old address.
    back = _accepted(settled, _notice(2, new_address=NEW_URL))
    assert settle(back, CLOCK.now() + timedelta(hours=2)).tls_pin_sha256 is None


def test_a_notice_cannot_swap_the_key_of_a_node_that_is_already_trusted() -> None:
    state = sample_state(CLOCK)
    same_node_new_key = _notice(new_node_id=state.hive_node_id)
    assert accept_moved(state, same_node_new_key, state.hive_node_id, CLOCK.now()) is None
    same_node_same_key = _notice(
        new_node_id=state.hive_node_id, new_node_public_key_hex=state.hive_key_hex
    )
    assert accept_moved(state, same_node_same_key, state.hive_node_id, CLOCK.now()) is not None


def test_a_second_notice_folds_out_the_nodes_of_the_first_unless_it_points_back() -> None:
    state = sample_state(CLOCK)
    first = _notice(1)
    after_first = _accepted(state, first)
    signer = first.new_node_id  # Either Hive node may sign during the grace.
    second = _notice(2)
    after_second = _accepted(after_first, second, signer)
    # The first move's new node was never in charge and is no longer trusted; the pinned one
    # stays the current Queen until the new window settles.
    assert first.new_node_id not in {n.node_id for n in after_second.trusted_nodes}
    assert second.new_node_id in {n.node_id for n in after_second.trusted_nodes}
    assert after_second.queen_move is not None
    assert after_second.queen_move.previous_node_id == state.hive_node_id


def test_a_rollback_points_the_packet_back_at_the_old_queen_under_the_same_rules() -> None:
    state = sample_state(CLOCK)
    away = _notice(1)
    after_away = _accepted(state, away)
    later = CLOCK.now() + timedelta(minutes=5)  # The move took effect; the new Queen is current.
    back = QueenMoved(
        sequence=2,
        new_address=state.queen_url,
        new_node_id=state.hive_node_id,
        new_node_public_key_hex=state.hive_key_hex,
        effective_at=later,
        grace_until=later + timedelta(hours=1),
        is_rollback=True,
        reason="The handover failed.",
    )
    rolled = accept_moved(after_away, back, away.new_node_id, later)
    assert rolled is not None and rolled.queen_move is not None
    assert rolled.queen_move.previous_node_id == away.new_node_id
    assert rolled.queen_move.new_url == state.queen_url
    assert state.hive_node_id in {n.node_id for n in rolled.trusted_nodes}
