"""Hand one sub-bee its Comb tools: a bundle it may be offered, where their bytes come from, a sink.

A Worker never reads the Comb Registry (the catalogue of promoted tools): its Warden gives it a
`CombToolBundle` of `CombToolEntry` values attenuated to what that one Worker may call, a
`PackageSource` that serves the packages' bytes from the resident Basket, and a `RetainSink` that
deposits an artefact it wants kept (roadmap 9.7, ADR-0046, ADR-0048). This module is the pure half
of that hand-over at spawn: `CombSeams` is what a Warden offers `spawn_sub_bee` (its tools so far,
its source, a way to make a sink for one Worker), `visible_entries` is the attenuation, and
`make_handles` turns the first into one Worker's `CombHandles`. The Warden-side wiring that fills
`CombSeams` lives in `hivemind.wardens.ticks.tools.seams`, because it needs the Warden itself.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's spawn
    sub-package. Built by `hivemind.wardens.ticks.assign` and `.alarms` into `WardenCellContext.
    comb`; read by `hivemind.wardens.spawn.spawn.spawn_sub_bee` and, when a tool is promoted
    later, by `hivemind.wardens.ticks.tools.install`. Calls into `hivemind.guard` and
    `hivemind.workers` and waggle only.

Key invariants:
    - A tool reaches a bundle only when the sub-bee's own set allows `tool:<name>` and every
      capability the tool needs: a bundle never widens what the Queen's grant gave the bee.
    - A CELL-scope tool reaches only the bees of the Cell it is for.
    - Nothing here is async except the one call that reads the Warden's current tools.

See Also:
    - docs/adr/0046 and docs/adr/0048 for the rules, and .claude/roadmap.md step 9.7.
    - hivemind.workers.tools.comb.entry for the bundle and the entry.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from hivemind.guard import Capability, CapabilityFamily, CapabilitySet, InvalidCapabilityError
from hivemind.workers.context import RetainSink
from hivemind.workers.tools.comb import CombToolBundle, CombToolEntry, PackageSource
from waggle.ids import CellId, TaskId, WorkerId
from waggle.messages.tool.authoring import ToolScope

__all__ = ["CombHandles", "CombSeams", "make_handles", "visible_entries"]


@dataclass(frozen=True, slots=True)
class CombSeams:
    """What a Warden offers `spawn_sub_bee` so a new sub-bee can be given Comb tools."""

    entries: Callable[[], Awaitable[Iterable[CombToolEntry]]]  # The tools this Warden holds now.
    package_source: PackageSource  # Serves a package's bytes by hash (the resident Basket).
    retain_factory: Callable[[WorkerId, TaskId], RetainSink]  # Makes one Worker's retain sink.


@dataclass(frozen=True, slots=True)
class CombHandles:
    """The three things one sub-bee's `WorkerContext` carries for the Comb."""

    bundle: CombToolBundle  # Its tools; the Warden replaces the entries when one is promoted.
    package_source: PackageSource  # Where their bytes come from.
    retain_sink: RetainSink  # Where an artefact it wants kept goes.


def visible_entries(
    entries: Iterable[CombToolEntry], capabilities: CapabilitySet, cell_id: CellId
) -> tuple[CombToolEntry, ...]:
    """Return the entries a sub-bee with `capabilities` on Cell `cell_id` may be offered.

    Args:
        entries: Every tool the Warden holds.
        capabilities: The sub-bee's own set, as its spawn computed it.
        cell_id: The Cell the sub-bee runs on.

    Returns:
        The entries whose scope fits the Cell and whose `tool:<name>` and needed capabilities the
        set allows, in the order given.
    """
    return tuple(entry for entry in entries if _may_offer(entry, capabilities, cell_id))


async def make_handles(
    seams: CombSeams | None,
    capabilities: CapabilitySet,
    cell_id: CellId,
    ids: tuple[WorkerId, TaskId],
) -> CombHandles | None:
    """Build one sub-bee's Comb handles, or None when its Warden has no Basket to serve them from.

    Args:
        seams: What the Warden offers; None when it holds no Basket.
        capabilities: The sub-bee's own set.
        cell_id: The Cell the sub-bee runs on.
        ids: The sub-bee's worker id and its task id, for the retain sink's provenance.

    Returns:
        The handles, with a bundle holding the tools this sub-bee may call right now.
    """
    if seams is None:
        return None
    # Read at spawn, not at Warden start: a tool promoted since then must be in a new bee's bundle.
    held = await seams.entries()
    bundle = CombToolBundle(visible_entries(held, capabilities, cell_id))
    return CombHandles(bundle, seams.package_source, seams.retain_factory(*ids))


def _may_offer(entry: CombToolEntry, capabilities: CapabilitySet, cell_id: CellId) -> bool:
    """Return whether `entry` fits the Cell and the set allows calling it (module docstring)."""
    # A tool a Warden promoted for its own Cell is invisible to every other Cell's bees.
    if entry.scope is ToolScope.CELL and entry.cell_id != cell_id:
        return False
    try:
        needed = [Capability.parse(text) for text in entry.required_capabilities]
    except InvalidCapabilityError:
        return False  # An entry the Guard cannot even read is never offered.
    needed.append(Capability(family=CapabilityFamily.TOOL, scope=entry.name))
    return all(capabilities.allows(capability) for capability in needed)
