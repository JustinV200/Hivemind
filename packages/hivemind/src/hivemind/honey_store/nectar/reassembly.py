"""Reassemble chunked Nectar deposits, enforcing the Waggle spec's section 5 receiver rules exactly.

A Nectar deposit (raw findings a bee brings back for the Honey Store, the Hive's knowledge base)
larger than one Waggle message's `MAX_CHUNK_BYTES` arrives as several `NectarDeposit` chunks that
share one group key. `ChunkGroups` buffers each incomplete group and hands back the whole content
only once the final chunk arrives and every rule held: a first chunk at offset 0, a declared total
within the cap, every later chunk continuing at exactly the running length and repeating the
first chunk's metadata, the total never overrun, and on `final` a length equal to the declared
total and a sha256 equal to the declared digest. Any broken rule drops the whole group and raises
the matching `NectarRejectedError`, because a partly-checked deposit can never be trusted later.
The spec's two numbers, `MAX_OPEN_CHUNK_GROUPS` (8 incomplete groups per sender) and
`CHUNK_GROUP_TIMEOUT_S` (60 seconds idle), are named once in `waggle.messages.base`, the package
that owns the wire spec, and re-exported here rather than restated as a second copy that could
drift from the first.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.nectar`.
    Owned by `hivemind.honey_store.nectar.intake.NectarIntake`, which feeds it every chunk the
    Queen's tick receives and calls `expire` from the tick's housekeeping. Calls into
    `hivemind.honey_store.errors` and `waggle` only; no I/O.

Key invariants:
    - A group is keyed by `(envelope sender, deposit.worker_id, deposit.sha256)`: a Warden relays
      its Workers' deposits under its own address, so two Workers depositing the same bytes
      through one Warden never share a buffer.
    - Every rejection removes the group before raising, so a later chunk of the same deposit can
      never resume a group that already failed a check.
    - A group's buffer never holds more than its declared `total_bytes`, which never exceeds the
      caller's `max_bytes`, so one open deposit's memory is bounded by the manifest's cap.
    - A single-chunk deposit (offset 0 and final) never counts as open: it is checked and
      returned in the same call.

See Also:
    - docs/waggle/spec.md section 5 for the chunking and reassembly rules implemented here.
    - hivemind.honey_store.nectar.intake for NectarIntake, the one owner of a ChunkGroups.
    - hivemind.honey_store.errors for every NectarRejectedError subclass raised here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from hivemind.honey_store.errors import (
    ChunkMismatchError,
    DepositLengthMismatchError,
    DepositTimedOutError,
    FirstChunkNotAtZeroError,
    NectarTooLargeError,
    OffsetMismatchError,
    Sha256MismatchError,
    TooManyOpenDepositsError,
)
from waggle.messages.base import CHUNK_GROUP_TIMEOUT_S, MAX_OPEN_CHUNK_GROUPS
from waggle.messages.honey import NectarDeposit

# Every NectarDeposit field that describes the whole deposit rather than one slice of it; a later
# chunk must repeat each one exactly as its group's first chunk declared it. `sha256` and
# `worker_id` are part of the group key itself, and `chunk`/`offset`/`final` are per-slice.
_HEADER_FIELDS: tuple[str, ...] = (
    "total_bytes",
    "kind",
    "media_type",
    "title",
    "task_id",
    "cell_id",
    "observed_at",
    "clearance",
    "origin_tier",
    "event_id",
)

__all__ = ["CHUNK_GROUP_TIMEOUT_S", "MAX_OPEN_CHUNK_GROUPS", "ChunkGroups"]


@dataclass(frozen=True, slots=True)
class _GroupKey:
    """One deposit's group key (docs/waggle/spec.md section 5, plus the Worker behind a relay)."""

    sender: str  # The envelope sender: the Worker itself, or the Warden relaying for it.
    worker_id: str | None  # The Worker that gathered it; None when a Warden deposits its own.
    sha256: str  # The digest of the whole content every chunk declares.


@dataclass(slots=True)
class _OpenGroup:
    """One incomplete deposit's buffer; mutable, owned and mutated only by `ChunkGroups`."""

    header: tuple[object, ...]  # The first chunk's `_HEADER_FIELDS` values, in that order.
    total_bytes: int  # The declared length of the whole content.
    buffer: bytearray  # Every byte received so far, in offset order.
    last_seen: datetime  # When the newest chunk arrived; drives the idle timeout.


class ChunkGroups:
    """Buffer incomplete chunked Nectar deposits per sender and release each one once it verifies.

    Owns mutable state (the open groups and their buffers) and mutates it in place, which
    codingrules 8.5 allows only inside the class that owns that state: nothing else ever holds a
    reference to a group. Not safe across threads; the Queen's tick is its one caller, on one
    event loop, and every method here is synchronous, so no call can interleave with another.
    """

    def __init__(self) -> None:
        """Start with no open groups."""
        self._groups: dict[_GroupKey, _OpenGroup] = {}

    def accept(
        self, sender: str, deposit: NectarDeposit, now: datetime, max_bytes: int
    ) -> bytes | None:
        """Add one chunk to its group; return the whole content once the final chunk verifies.

        Args:
            sender: The envelope sender the chunk arrived from.
            deposit: One chunk of a deposit.
            now: The receiver's current time, from the injected clock.
            max_bytes: The largest whole deposit accepted (`[honey.store] max_nectar_bytes`).

        Returns:
            The complete, verified content when `deposit` is the final chunk; None while the
            group still waits for more.

        Raises:
            FirstChunkNotAtZeroError: A new group's first chunk does not start at offset 0.
            NectarTooLargeError: A new group declares a total over `max_bytes`.
            TooManyOpenDepositsError: The sender already holds `MAX_OPEN_CHUNK_GROUPS` open groups.
            DepositTimedOutError: The chunk continues a group idle past `CHUNK_GROUP_TIMEOUT_S`.
            ChunkMismatchError: The chunk's metadata differs from its group's first chunk.
            OffsetMismatchError: The chunk does not continue at the group's running length.
            DepositLengthMismatchError: The bytes overrun the declared total, or the final chunk
                leaves them short of it.
            Sha256MismatchError: The complete content does not hash to the declared digest.
        """
        key = _GroupKey(sender=sender, worker_id=deposit.worker_id, sha256=deposit.sha256)
        group = self._groups.get(key)
        # A key with no group is a new deposit; one with a group must continue it exactly.
        if group is None:
            group = self._open(key, deposit, now, max_bytes)
        else:
            self._check_continuation(key, group, deposit, now)
        received = len(group.buffer) + len(deposit.chunk)
        # Checked on every chunk and before buffering it: a sender that never says `final` must
        # still never make one group outgrow the total it declared (and so the manifest's cap).
        if received > group.total_bytes:
            del self._groups[key]
            raise DepositLengthMismatchError(sender, key.sha256, group.total_bytes, received)
        group.buffer.extend(deposit.chunk)
        group.last_seen = now
        if not deposit.final:
            return None
        # The final chunk closes the group whether or not the whole verifies.
        del self._groups[key]
        return _verified_whole(key, group)

    def expire(self, now: datetime) -> int:
        """Drop every group idle for longer than `CHUNK_GROUP_TIMEOUT_S`.

        Args:
            now: The receiver's current time, from the injected clock.

        Returns:
            How many groups were dropped.
        """
        # Collected first, then deleted: a dict cannot shrink while it is being iterated.
        stale = [key for key, group in self._groups.items() if _is_idle(group, now)]
        for key in stale:
            del self._groups[key]
        return len(stale)

    def open_count(self, sender: str | None = None) -> int:
        """Count the open (incomplete) groups, for one sender or across every sender.

        Args:
            sender: Count only this envelope sender's groups; None counts all of them.

        Returns:
            The number of groups still waiting for more chunks.
        """
        if sender is None:
            return len(self._groups)
        return sum(1 for key in self._groups if key.sender == sender)

    def _open(
        self, key: _GroupKey, deposit: NectarDeposit, now: datetime, max_bytes: int
    ) -> _OpenGroup:
        """Check a new group's first chunk (spec section 5) and register the group for it."""
        if deposit.offset != 0:
            raise FirstChunkNotAtZeroError(deposit.offset)
        # Checked against the declared total before any byte is buffered, so an oversized deposit
        # is refused on its first chunk instead of after megabytes have arrived.
        if deposit.total_bytes > max_bytes:
            raise NectarTooLargeError(deposit.total_bytes, max_bytes)
        # Only a group that stays open counts against the per-sender limit; stale groups are swept
        # first so a sender is never refused because of deposits that already timed out.
        if not deposit.final:
            self.expire(now)
            open_count = self.open_count(key.sender)
            if open_count >= MAX_OPEN_CHUNK_GROUPS:
                raise TooManyOpenDepositsError(key.sender, open_count, MAX_OPEN_CHUNK_GROUPS)
        group = _OpenGroup(
            header=_header_of(deposit),
            total_bytes=deposit.total_bytes,
            buffer=bytearray(),
            last_seen=now,
        )
        self._groups[key] = group
        return group

    def _check_continuation(
        self, key: _GroupKey, group: _OpenGroup, deposit: NectarDeposit, now: datetime
    ) -> None:
        """Check a later chunk against its open group; drop the group before raising on a fault."""
        idle_s = (now - group.last_seen).total_seconds()
        # A chunk arriving after the timeout finds a group the next sweep would have dropped anyway.
        if idle_s > CHUNK_GROUP_TIMEOUT_S:
            del self._groups[key]
            raise DepositTimedOutError(key.sender, key.sha256, idle_s)
        mismatch = _first_mismatch(group.header, deposit)
        if mismatch is not None:
            del self._groups[key]
            raise ChunkMismatchError(key.sender, key.sha256, mismatch)
        # Chunks must arrive contiguous and in order: no gaps, no overlaps, no resends.
        if deposit.offset != len(group.buffer):
            del self._groups[key]
            raise OffsetMismatchError(len(group.buffer), deposit.offset)


def _header_of(deposit: NectarDeposit) -> tuple[object, ...]:
    """Return `deposit`'s whole-deposit metadata, in `_HEADER_FIELDS` order."""
    return tuple(getattr(deposit, name) for name in _HEADER_FIELDS)


def _first_mismatch(header: tuple[object, ...], deposit: NectarDeposit) -> str | None:
    """Return the first `_HEADER_FIELDS` name `deposit` declares differently, or None."""
    # Walked in declaration order so the reported field is stable for the same pair of chunks.
    for name, expected in zip(_HEADER_FIELDS, header, strict=True):
        if getattr(deposit, name) != expected:
            return name
    return None


def _is_idle(group: _OpenGroup, now: datetime) -> bool:
    """Return whether `group` has waited longer than `CHUNK_GROUP_TIMEOUT_S` for its next chunk."""
    return (now - group.last_seen).total_seconds() > CHUNK_GROUP_TIMEOUT_S


def _verified_whole(key: _GroupKey, group: _OpenGroup) -> bytes:
    """Return a closed group's content once its length and digest match what it declared."""
    whole = bytes(group.buffer)
    # Overrun was already refused chunk by chunk; here only a short deposit can remain.
    if len(whole) != group.total_bytes:
        raise DepositLengthMismatchError(key.sender, key.sha256, group.total_bytes, len(whole))
    computed = hashlib.sha256(whole).hexdigest()
    if computed != key.sha256:
        raise Sha256MismatchError(key.sha256, computed)
    return whole
