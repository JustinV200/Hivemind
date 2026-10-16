"""Decide which blobs a Cell keeps resident under its disk ceiling, and apply the decision.

Every Cell keeps only the blobs its Warden chose to have, within the disk ceiling the Queen set
(`Ceilings.resident_basket_disk_bytes`, roadmap 4.8). `decide_residency` is the pure core: given
what is resident, how big the incoming blob is and the ceiling, it says whether the blob fits and
which resident blobs to evict, least recently used first, to make room, and refuses outright a blob
larger than the whole ceiling (evicting everything could never fit it). `ResidentBasket` is the
per-Cell facade over a `BasketStore`: it applies a decision through the store, answers whether a
blob is resident, and hands back a resident blob's bytes only after hashing them again. Every
change to the resident set goes through one lock per facade, so an admission's read, evictions
and write are one step: two blobs admitted at once see each other, and a read that counts as a
use cannot resurrect a blob an admission is evicting. A composition root makes one facade per
Cell, which is what makes the lock per Cell.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket`. Called by
    `hivemind.basket.transfer.receiver` (`plan`, before the first chunk) and by Wardens
    (`admit`, `bytes_for`, `evict`); the Warden's autopilot never awaits a model, and nothing here
    does. Calls into `hivemind.basket.store` and `hivemind.basket.errors` only.

Key invariants:
    - After `decide_residency` admits a blob and its evictions are applied, the resident total is
      at most `ceiling_bytes`, however many admissions are in flight through the same facade.
    - Evictions are taken strictly oldest `last_used` first, then oldest `resident_since`, then by
      hash, so the same state always gives the same decision.
    - An oversized blob is refused with nothing evicted: a refusal never costs the Cell its set.
    - A ceiling of zero admits nothing; a caller wanting "no limit" passes the Cell's free disk.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the residency decision.
    - hivemind.forage.models.pools.Ceilings for the ceiling's source.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from hivemind.basket.errors import BlobIntegrityError, BlobNotFoundError
from hivemind.basket.models import Blob
from hivemind.basket.store import BasketStore, ResidentBlob
from waggle.clock import Clock
from waggle.ids import CellId

__all__ = ["ResidencyDecision", "ResidentBasket", "ResidentBlob", "decide_residency"]


@dataclass(frozen=True, slots=True)
class ResidencyDecision:
    """Whether an incoming blob fits, and which resident blobs to evict so it does."""

    admit: bool  # True when the blob may become resident once `evict` has been applied.
    evict: tuple[str, ...]  # Hashes to evict, in order; empty when nothing needs to go.
    reason: str  # A sentence for the trail and the human; never blob content.


def decide_residency(
    resident: Sequence[ResidentBlob], incoming_size: int, ceiling_bytes: int
) -> ResidencyDecision:
    """Decide whether `incoming_size` bytes fit under the ceiling, evicting oldest-used first.

    Args:
        resident: The blobs the Cell holds now, in any order.
        incoming_size: The size in bytes of the blob wanting room.
        ceiling_bytes: The most the Cell may hold resident, in bytes.

    Returns:
        Admit with the hashes to evict (possibly none), or refuse with nothing to evict when the
        blob alone is larger than the ceiling.
    """
    # A blob bigger than the whole ceiling can never fit, however much is evicted; refuse before
    # touching anything so a hopeless request costs the Cell nothing.
    if incoming_size > ceiling_bytes:
        return ResidencyDecision(
            False, (), f"a {incoming_size}-byte blob is over the {ceiling_bytes}-byte ceiling."
        )
    used = sum(blob.size_bytes for blob in resident)
    evict: list[str] = []
    # Walk the Cell's blobs least recently used first, dropping each until the newcomer fits; the
    # accumulator is the bytes still resident after the drops so far.
    for blob in sorted(resident, key=lambda b: (b.last_used, b.resident_since, b.sha256)):
        if used + incoming_size <= ceiling_bytes:
            break
        evict.append(blob.sha256)
        used -= blob.size_bytes
    reason = (
        "fits" if not evict else f"fits after evicting {len(evict)} least-recently-used blob(s)."
    )
    return ResidencyDecision(True, tuple(evict), reason)


class ResidentBasket:
    """One Cell's resident set: a facade over a store, a Cell id and a byte ceiling."""

    def __init__(
        self, store: BasketStore, cell_id: CellId, ceiling_bytes: int, clock: Clock
    ) -> None:
        """Bind the facade to a store and a Cell.

        Args:
            store: Where the Cell's residency rows and the blobs live.
            cell_id: The Cell this resident set belongs to.
            ceiling_bytes: The most this Cell may keep resident (from its `Ceilings`).
            clock: Stamps `resident_since` and `last_used`.
        """
        self._store = store
        self._cell_id = cell_id
        self._ceiling = ceiling_bytes
        self._clock = clock
        # Serialises every read-then-write on this Cell's resident set: the store serialises
        # single calls, but an admission is several, and two of them interleaved would both see
        # room for one blob and both take it.
        self._lock = asyncio.Lock()

    @property
    def cell_id(self) -> CellId:
        """The Cell this resident set belongs to."""
        return self._cell_id

    @property
    def ceiling_bytes(self) -> int:
        """The most this Cell may keep resident, in bytes."""
        return self._ceiling

    async def plan(self, size_bytes: int) -> ResidencyDecision:
        """Say whether a blob of this size would fit, without changing anything.

        Args:
            size_bytes: The incoming blob's size.

        Returns:
            The decision `admit` would apply; the receiver asks before the first chunk arrives.
        """
        # Local store read; bounded by the store's own busy timeout.
        return decide_residency(
            await self._store.resident(self._cell_id), size_bytes, self._ceiling
        )

    async def admit(self, blob: Blob) -> ResidencyDecision:
        """Make a blob resident, evicting least-recently-used blobs if it needs the room.

        Args:
            blob: A blob the store already holds.

        Returns:
            The decision applied: admit False (nothing changed) or admit True (evictions done and
            the blob marked resident).
        """
        # Held from the read to the last write: a second admission waits here and then reads a
        # resident set that already holds this blob.
        async with self._lock:
            resident = await self._store.resident(self._cell_id)
            # Already here: refresh its last use and make no room, so a repeat shipment is
            # harmless.
            if any(held.sha256 == blob.sha256 for held in resident):
                await self._store.mark_resident(self._cell_id, blob.sha256, self._clock.now())
                return ResidencyDecision(True, (), "already resident.")
            decision = decide_residency(resident, blob.size_bytes, self._ceiling)
            if not decision.admit:
                return decision
            # Evictions first, then the newcomer, so the resident total never passes the ceiling.
            for sha256 in decision.evict:
                await self._store.evict(self._cell_id, sha256)
            await self._store.mark_resident(self._cell_id, blob.sha256, self._clock.now())
            return decision

    async def is_resident(self, sha256: str) -> bool:
        """Return whether this Cell holds the blob."""
        return any(held.sha256 == sha256 for held in await self._store.resident(self._cell_id))

    async def bytes_for(self, sha256: str) -> bytes:
        """Return a resident blob's bytes after hashing them again, and note the use.

        Args:
            sha256: The blob wanted.

        Returns:
            Bytes whose SHA-256 is `sha256`.

        Raises:
            BlobNotFoundError: The blob is not resident on this Cell.
            BlobIntegrityError: The bytes the store returned do not hash to `sha256`.
        """
        # Under the lock so the use is noted on a blob that is still resident: `mark_resident`
        # is an upsert, and noting a use after an admission evicted the blob would bring it back
        # over the ceiling.
        async with self._lock:
            if not await self.is_resident(sha256):
                raise BlobNotFoundError(sha256)
            data = await self._store.open_bytes(sha256)
            # Defence in depth: this facade is what a Warden hands bytes out through, so it does
            # not rely on the store alone for the one check that makes resident bytes trustworthy.
            if hashlib.sha256(data).hexdigest() != sha256:
                raise BlobIntegrityError(
                    sha256, "the resident bytes do not hash to the blob's name"
                )
            # A read is a use: the least recently used blob is the one evicted first.
            await self._store.mark_resident(self._cell_id, sha256, self._clock.now())
            return data

    async def evict(self, sha256: str) -> None:
        """Drop a blob from this Cell's resident set (the blob itself stays in the store)."""
        async with self._lock:
            await self._store.evict(self._cell_id, sha256)
