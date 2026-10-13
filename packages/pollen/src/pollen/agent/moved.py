"""Honour a signed QueenMoved: where the packet dials, and which nodes may speak as the Queen.

Supersedure (moving the Hive Stand, the machine the Queen runs on, to another machine) is a later
phase, but the Pollen Packet (the thin gateway program on a device the Hive borrows) that honours
the notice is built now, because a device the Hive cannot find again is lost. These are the pure
rules (gateway-contract section 5 and enrolment.md section 12). The packet persists the Queen's
address from enrolment and replaces it only on a ``control.queen_moved`` that a Hive node signed
and whose ``sequence`` is above the highest it has persisted, so a replayed or older notice, a
rollback included, can never redirect it. It pins the new node's key and keeps the old Hive key as
well; from ``effective_at`` it dials the new address (the old one stays a fallback), and until
``grace_until`` frames signed by either node are accepted. At ``grace_until`` the old key and
address are dropped, so the machine that stepped down keeps no authority. A notice with
``is_rollback`` set points the packet back at the old Queen under exactly the same rules.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by
    pollen.agent.control (accepting a notice), pollen.agent.dispatch (who may speak as the Queen)
    and pollen.agent.packet (settling, then dialling); calls into pollen.state and waggle only.

Key invariants:
    - Pure: every function maps a state and a time to a new state or an answer; none does I/O.
    - A notice is acted on only if a Hive node signed it and its sequence is strictly above
      ``moved_sequence``; otherwise nothing changes.
    - The pinned Hive key is always among the verifier's keys, and the accept's list can never
      override it.
    - Nothing about the certificate pin changes: a notice carries no fingerprint.

See Also:
    - docs/waggle/enrolment.md section 12 (when the Hive Stand moves).
    - docs/waggle/gateway-contract.md section 5 (the link).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pollen.state import PacketState, QueenMove
from waggle.messages.control import QueenMoved
from waggle.messages.swarm import NodeKey
from waggle.signing import public_key_from_hex

__all__ = [
    "DialPlan",
    "accept_moved",
    "dial_plan",
    "hive_nodes",
    "settle",
    "trusted_keys",
]


@dataclass(frozen=True, slots=True)
class DialPlan:
    """Where the packet dials now, in the order it tries."""

    urls: tuple[str, ...]  # The Queen's addresses, preferred first.


def hive_nodes(state: PacketState, now: datetime) -> frozenset[str]:
    """Return the nodes that may speak as the Queen right now.

    Args:
        state: The persisted state.
        now: The current time.

    Returns:
        The pinned Hive node, plus both ends of a relocation still inside its window.
    """
    nodes = {state.hive_node_id}
    move = state.queen_move
    if move is not None and now < move.grace_until:
        nodes |= {move.previous_node_id, move.new_node_id}
    return frozenset(nodes)


def trusted_keys(state: PacketState) -> dict[str, bytes]:
    """Return the verifier's keys: every trusted node, with the pinned Hive key winning.

    Args:
        state: The persisted state.

    Returns:
        ``node_id`` to raw public key.
    """
    keys: dict[str, bytes] = {
        node.node_id: public_key_from_hex(node.public_key_hex) for node in state.trusted_nodes
    }
    keys[state.hive_node_id] = public_key_from_hex(state.hive_key_hex)
    return keys


def settle(state: PacketState, now: datetime) -> PacketState:
    """Fold a relocation whose grace window has ended into the base state.

    Args:
        state: The persisted state.
        now: The current time.

    Returns:
        ``state`` itself when nothing has expired; otherwise a state whose address and Hive key
        are the new Queen's, with the old node no longer trusted and the window closed.
    """
    move = state.queen_move
    if move is None or now < move.grace_until:
        return state
    kept = tuple(n for n in state.trusted_nodes if n.node_id != move.previous_node_id)
    return state.model_copy(
        update={
            "queen_url": move.new_url,
            "hive_node_id": move.new_node_id,
            "hive_key_hex": move.new_key_hex,
            "trusted_nodes": kept,
            "queen_move": None,
        }
    )


def dial_plan(state: PacketState, now: datetime) -> DialPlan:
    """Say which addresses to dial: the old one until the move takes effect, then the new first.

    Args:
        state: A settled state.
        now: The current time.

    Returns:
        The addresses in the order to try them.
    """
    move = state.queen_move
    if move is None:
        return DialPlan((state.queen_url,))
    if now < move.effective_at:
        return DialPlan((move.previous_url,))
    return DialPlan((move.new_url, move.previous_url))


def accept_moved(
    state: PacketState, notice: QueenMoved, signer_node: str, now: datetime
) -> PacketState | None:
    """Apply a verified QueenMoved to the state, or say it must be ignored.

    Args:
        state: The persisted state.
        notice: The relocation notice, already verified and admitted as a Queen-only frame.
        signer_node: The node whose key signed the notice's envelope.
        now: The current time.

    Returns:
        The new state, or None when the notice is stale (its sequence is not above the highest
        accepted), was not signed by a Hive node, would switch between a pinned ``wss://`` link
        and a plain one, or names a node already trusted under another key.
    """
    if notice.sequence <= state.moved_sequence or signer_node not in hive_nodes(state, now):
        return None
    # The certificate pin cannot change with a notice, so the scheme must keep fitting it; and a
    # node id already trusted keeps the key it has, so a notice cannot swap a key under a name.
    if not _scheme_fits(state, notice) or _key_conflicts(state, notice):
        return None
    url, node, key = _current_queen(state, now)
    move = QueenMove(
        sequence=notice.sequence,
        previous_url=url,
        previous_node_id=node,
        previous_key_hex=key,
        new_url=notice.new_address,
        new_node_id=notice.new_node_id,
        new_key_hex=notice.new_node_public_key_hex,
        effective_at=notice.effective_at,
        grace_until=notice.grace_until,
    )
    return state.model_copy(
        update={
            "moved_sequence": notice.sequence,
            "queen_move": move,
            "trusted_nodes": _retrusted(state, notice, node),
        }
    )


def _scheme_fits(state: PacketState, notice: QueenMoved) -> bool:
    """Return whether the new address keeps the link's pinned-or-plain character."""
    return notice.new_address.startswith("wss://") == (state.tls_pin_sha256 is not None)


def _key_conflicts(state: PacketState, notice: QueenMoved) -> bool:
    """Return whether the notice names an already trusted node under a different key."""
    known = {node.node_id: node.public_key_hex for node in state.trusted_nodes}
    known[state.hive_node_id] = state.hive_key_hex
    return known.get(notice.new_node_id, notice.new_node_public_key_hex) != (
        notice.new_node_public_key_hex
    )


def _retrusted(state: PacketState, notice: QueenMoved, current_node: str) -> tuple[NodeKey, ...]:
    """Trust the new node, and fold out the nodes of a move this notice supersedes."""
    earlier = state.queen_move
    needed = {current_node, notice.new_node_id}
    # A move still pending is replaced, not stacked: the nodes it named lose their trust now
    # unless the Queen currently in charge is one of them or the new notice points back at it.
    dropped = set() if earlier is None else {earlier.previous_node_id, earlier.new_node_id} - needed
    kept = [
        node
        for node in state.trusted_nodes
        if node.node_id not in dropped and node.node_id != notice.new_node_id
    ]
    kept.append(NodeKey(node_id=notice.new_node_id, public_key_hex=notice.new_node_public_key_hex))
    return tuple(kept)


def _current_queen(state: PacketState, now: datetime) -> tuple[str, str, str]:
    """Return the address, node and key of the Queen the packet treats as current right now."""
    move = state.queen_move
    if move is not None and now >= move.effective_at:
        return move.new_url, move.new_node_id, move.new_key_hex
    return state.queen_url, state.hive_node_id, state.hive_key_hex
