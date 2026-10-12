"""Serve a Worker's Comb tools their package bytes: the Warden's `PackageSource` over the Basket.

A Worker loads a Comb tool only from bytes that hash to the digest its entry names
(`hivemind.workers.tools.comb.invoke`); where those bytes come from is its Warden's concern. A
Cell's Warden serves them from its resident Basket (`ResidentPackageSource`), which hashes them
again on every read; the Hive Stand's Warden serves them from the Hive's own store
(`StorePackageSource`), which is the canonical copy of every blob and so needs no residency
step. Both turn a missing or corrupt blob into `PackageUnavailableError`, the one error the
Worker's loader handles. `blob_verified` is the Warden's own question -- "do I really hold this
package, and does it still hash right?" -- asked before it hands a tool to anyone.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.basket sub-package. Called by `hivemind.wardens.ticks.tools` (install, seams, the
    quarantine order). Calls into `hivemind.basket` and `hivemind.workers.tools` only.

Key invariants:
    - Bytes returned from here always hash to the digest asked for; nothing else is returned.
    - Reading a blob never changes it, never executes it and never installs it (ADR-0048).

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the inert-blob rule.
    - hivemind.basket.residency.ResidentBasket for the resident view served from.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from hivemind.basket import BasketError, BasketStore, ResidentBasket
from hivemind.workers.tools.comb import PackageSource
from hivemind.workers.tools.errors import PackageUnavailableError

if TYPE_CHECKING:
    from hivemind.wardens.deps import WardenDeps

__all__ = [
    "ResidentPackageSource",
    "StorePackageSource",
    "blob_verified",
    "package_source",
    "store_of",
]


class ResidentPackageSource:
    """A `PackageSource` over a Cell's resident Basket."""

    def __init__(self, resident: ResidentBasket) -> None:
        """Serve packages from `resident`; a blob it does not hold is unavailable."""
        self._resident = resident

    async def bytes_for(self, sha256: str) -> bytes:
        """Return the resident blob's bytes, re-hashed; `PackageUnavailableError` otherwise."""
        try:
            # Local disk and one SQLite read; a miss is refused at once, never waited for.
            return await self._resident.bytes_for(sha256)
        except BasketError as exc:
            raise PackageUnavailableError(sha256, "not resident or unreadable") from exc


class StorePackageSource:
    """A `PackageSource` over the Hive's canonical Basket store (the Hive Stand's Warden)."""

    def __init__(self, store: BasketStore) -> None:
        """Serve packages straight from `store`, which re-hashes every read."""
        self._store = store

    async def bytes_for(self, sha256: str) -> bytes:
        """Return the stored blob's bytes; `PackageUnavailableError` if missing or retired."""
        try:
            return await self._store.open_bytes(sha256)
        except BasketError as exc:
            raise PackageUnavailableError(sha256, "not stored or unreadable") from exc


def store_of(deps: WardenDeps) -> BasketStore | None:
    """Return the store this Warden writes blobs to (the Hive's or a Cell's own); None if none."""
    if deps.royal_jelly is not None:
        return deps.royal_jelly.basket
    return deps.basket_store


def package_source(deps: WardenDeps) -> PackageSource | None:
    """Return the `PackageSource` this Warden's Workers load Comb tools from, or None.

    Args:
        deps: The Warden's collaborators.

    Returns:
        The Hive's store on the Hive Stand, the resident Basket in a Cell, None for a Warden with
        no Basket at all (its Workers are then offered no Comb tools).
    """
    if deps.royal_jelly is not None:
        return StorePackageSource(deps.royal_jelly.basket)
    if deps.resident_basket is not None:
        return ResidentPackageSource(deps.resident_basket)
    return None


async def blob_verified(deps: WardenDeps, sha256: str) -> bool:
    """Return whether this Warden holds the blob `sha256` and its bytes still hash to it.

    Args:
        deps: The Warden's collaborators.
        sha256: The digest a promoted tool's package is named by.

    Returns:
        True only when the resident copy (or, on the Hive Stand, the stored one) reads back and
        hashes to `sha256`; False for a blob that is absent, retired or corrupt.
    """
    source = package_source(deps)
    if source is None:
        return False
    try:
        data = await source.bytes_for(sha256)
    except PackageUnavailableError:
        return False
    # The sources hash already; this is the Warden's own check before it vouches for a tool.
    return hashlib.sha256(data).hexdigest() == sha256
