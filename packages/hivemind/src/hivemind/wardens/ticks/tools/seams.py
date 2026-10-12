"""Wire a Warden's Comb tools into `spawn_sub_bee`: its tools, their bytes and a retain sink.

`hivemind.wardens.spawn` hands each new sub-bee a bundle of Comb tools, a `PackageSource` and a
`RetainSink`, but spawn cannot import the Warden. `comb_seams` is the Warden-side half: called when
the Warden builds a `WardenCellContext`, it returns the `CombSeams` spawn reads. Two things are
read late, at the moment of each spawn, because both change between spawns: the tools the Warden
holds, and (on the Hive Stand, which owns the Comb Registry) the registry itself. A Hive Stand
Warden therefore asks `registry.visible_tools(cell_id)` for the current rows, turns each into a
`CombToolEntry` and keeps only the ones whose package it can really read and verify; a Cell's
Warden holds only what the Queen announced to it (`install`). The retain sink is made per Worker so
a deposit carries that Worker's provenance.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.tools sub-package. Called by `hivemind.wardens.ticks.assign` and `.alarms` when they
    build a spawn context. Calls into `hivemind.wardens.spawn.comb` (the seam type),
    `hivemind.wardens.ticks.basket` (source, sink) and `install`.

Key invariants:
    - No Basket, no Comb tools: a Warden with nothing to serve packages from returns None, and
      its Workers are offered no Comb tools and no `retain`.
    - A registry row whose package is not readable and hash-true is never offered
      (`tool.load_refused` is recorded instead).
    - The Hive Stand's entries mirror the registry at each spawn; a Cell's mirror what it was told.

See Also:
    - docs/adr/0046 and .claude/roadmap.md step 9.7 for what a Worker's bundle is.
    - hivemind.wardens.spawn.comb for `CombSeams`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.wardens.spawn.comb import CombSeams
from hivemind.wardens.ticks.basket.retain import WardenRetainSink
from hivemind.wardens.ticks.basket.source import blob_verified, package_source
from hivemind.wardens.ticks.tools.install import NOT_RESIDENT, record_load_refused
from hivemind.workers.tools.comb import CombToolEntry

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["comb_seams", "refresh_from_registry"]


def comb_seams(warden: Warden) -> CombSeams | None:
    """Return what `spawn_sub_bee` needs to give a new sub-bee Comb tools, or None without a Basket.

    Args:
        warden: The spawning Warden.

    Returns:
        The seams (tools read at spawn, the package source, a per-Worker retain sink); None when
        this Warden holds no Basket to serve packages from.
    """
    source = package_source(warden._deps)
    if source is None:
        return None
    return CombSeams(
        entries=lambda: _current_entries(warden),
        package_source=source,
        retain_factory=lambda worker_id, task_id: WardenRetainSink(warden, worker_id, task_id),
    )


async def refresh_from_registry(warden: Warden) -> None:
    """Rebuild a Hive Stand Warden's entries from the registry's current rows for its Cell.

    Args:
        warden: The Warden; a Cell's (no registry in its deps) is left exactly as it is.
    """
    deps, cell = warden._deps, warden._cell
    if deps.royal_jelly is None or cell is None:
        return
    fresh: dict[str, CombToolEntry] = {}
    for row in await deps.royal_jelly.registry.visible_tools(cell.id):
        entry = CombToolEntry.from_row(row)
        # The registry names a package by hash; the Warden hands it out only if it reads back true.
        if await blob_verified(deps, entry.package_sha256):
            fresh[entry.name] = entry
        else:
            await record_load_refused(warden, entry, NOT_RESIDENT, "the package does not verify.")
    warden._comb_entries.clear()
    warden._comb_entries.update(fresh)


async def _current_entries(warden: Warden) -> tuple[CombToolEntry, ...]:
    """Return the tools this Warden holds right now, freshly read where it owns the registry."""
    await refresh_from_registry(warden)
    return tuple(warden._comb_entries.values())
