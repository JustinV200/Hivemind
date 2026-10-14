"""Define RungKeeper: what the Queen remembers of the rung moves she made on devices herself.

Deciding a move is a pure function of facts (`.decide`), but two of the facts are memory, and the
Queen must not forget them between one pass and the next or she would flap a device between rungs.
She remembers, for each rung she raised a device to, when a reason for it was last seen to hold, so
that taking it back waits for the reasons to be gone for the whole of `lower_after_s`; a rung the
operator raised has no such mark, so it is never hers to take back. And she remembers, for each move
that failed, when she may try it again: `retry_after_s` after the first failure, twice that after
the second in a row, and so on up to 32 times the base, so a device that keeps failing costs the
Hive one attempt a day and not one a pass. A move she launches is an attempt; the keeper settles it
on a later pass, once no flow owns the device, by looking at the rung the device is now on: it
reached the target (the move worked) or it did not (the flow failed and undid itself). Nothing here
is persisted: a restarted Queen has no marks and so takes nothing back by herself, which errs
toward leaving a device where it is, and a failed move is tried once more at once.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.rungs`. Owned by `.evaluate.RungEvaluator`, which settles every
    device at the start of a pass and asks it for the memory the decision needs. Calls into
    `hivemind.manifest` (the back-off ceiling), `hivemind.queen.swarm_gate.terms` and
    `hivemind.swarm` only.

Key invariants:
    - A mark exists only for a rung the Queen raised and the device is still on or below; a rung
      the device has left, however it left it, loses its mark, so a rung the operator later gives
      the device again is the operator's.
    - A move succeeds when the device is on the rung it was moved to once no flow owns it;
      anything else is a failure and extends the back-off, which a success clears.
    - The back-off doubles with each failure in a row and stops doubling at `MAX_RETRY_DOUBLINGS`.
    - A refusal word is recorded once: it is reported as new only when it differs from the last.

See Also:
    - hivemind.manifest.schema.swarm for `lower_after_s`, `retry_after_s` and their ceiling.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from hivemind.manifest.schema.swarm import MAX_RETRY_DOUBLINGS
from hivemind.queen.swarm_gate.rungs.decide import Memory, Move
from hivemind.queen.swarm_gate.terms import GateTerms
from hivemind.swarm import RuntimeLevel, SwarmNode
from waggle.ids import DeviceId

# The rungs, lowest first: a mark above the rung a device is on has nothing to mark.
_RUNGS = (RuntimeLevel.GATEWAY, RuntimeLevel.COLONIZED, RuntimeLevel.NUC)

__all__ = ["RungKeeper"]


class RungKeeper:
    """Keep the marks, the back-offs and the in-flight moves of the Queen's own rung decisions."""

    def __init__(self, terms: GateTerms) -> None:
        """Build a keeper that remembers nothing.

        Args:
            terms: The gate's numbers; the back-off times are read from it.
        """
        self._terms = terms
        self._seen: dict[DeviceId, dict[RuntimeLevel, datetime]] = {}
        self._retry_at: dict[DeviceId, dict[Move, datetime]] = {}
        self._failures: dict[tuple[DeviceId, Move], int] = {}
        self._attempts: dict[DeviceId, tuple[Move, str]] = {}
        self._declined: dict[DeviceId, str] = {}

    def memory(self, device_id: DeviceId) -> Memory:
        """Return what the Queen remembers of her moves on `device_id`, as a snapshot."""
        return Memory(dict(self._seen.get(device_id, {})), dict(self._retry_at.get(device_id, {})))

    def pending(self, why: str) -> bool:
        """Whether a move launched for `why` (a reason's word) has not been settled yet."""
        return any(reason == why for _move, reason in self._attempts.values())

    def begin(self, device_id: DeviceId, move: Move, why: str) -> None:
        """Remember that a flow for `move` was launched on `device_id` because of `why`."""
        self._attempts[device_id] = (move, why)

    def mark_seen(self, device_id: DeviceId, level: RuntimeLevel, now: datetime) -> None:
        """Note that a reason for the rung `device_id` is on holds at `now`."""
        self._seen.setdefault(device_id, {})[level] = now

    def failed(self, device_id: DeviceId, move: Move, now: datetime) -> None:
        """Note that the move failed at `now`, and set when it may be tried again."""
        count = self._failures.get((device_id, move), 0) + 1
        self._failures[(device_id, move)] = count
        base = (
            self._terms.nuc_retry_after_s if RuntimeLevel.NUC in move else self._terms.retry_after_s
        )
        delay = base * 2 ** min(count - 1, MAX_RETRY_DOUBLINGS)
        self._retry_at.setdefault(device_id, {})[move] = now + timedelta(seconds=delay)

    def settle(self, node: SwarmNode, now: datetime) -> None:
        """Settle the move launched on `node`, if one was, and drop marks the device has left.

        Called for a device no flow owns, so the flow that was launched has ended: the move worked
        if the device is on the rung it was sent to.

        Args:
            node: The device as the registry holds it now.
            now: The time, from the Queen's clock.
        """
        attempt = self._attempts.pop(node.device_id, None)
        if attempt is not None:
            move, _why = attempt
            if node.level is move[1]:
                self._succeeded(node.device_id, move, now)
            else:
                self.failed(node.device_id, move, now)
        marks = self._seen.get(node.device_id, {})
        for level in [level for level in marks if _RUNGS.index(level) > _RUNGS.index(node.level)]:
            del marks[level]  # The device left that rung, however it left: it is no longer hers.

    def declined(self, device_id: DeviceId, word: str | None) -> bool:
        """Remember why a reason that holds cannot be acted on; True when it is news.

        Args:
            device_id: The device.
            word: The refusal word this pass, or None when nothing is refused now.

        Returns:
            True when `word` is not the one last remembered, so it is worth recording; a None
            forgets the last, so the same refusal after a change of heart is news again.
        """
        if word is None:
            self._declined.pop(device_id, None)
            return False
        news = self._declined.get(device_id) != word
        self._declined[device_id] = word
        return news

    def _succeeded(self, device_id: DeviceId, move: Move, now: datetime) -> None:
        """Note that the move worked: a raised rung is marked, a lowered one's mark is replaced."""
        self._failures.pop((device_id, move), None)
        self._retry_at.get(device_id, {}).pop(move, None)
        marks = self._seen.setdefault(device_id, {})
        origin, target = move
        if _RUNGS.index(target) > _RUNGS.index(origin):
            marks[target] = now  # The reason that raised it held when she began.
        else:
            marks.pop(origin, None)
            if target in marks:
                marks[target] = now  # Its reasons are given the whole wait, counted from now.
