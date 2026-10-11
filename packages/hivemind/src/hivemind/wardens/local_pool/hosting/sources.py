"""Choose which of a server's sources may load, and build the rows and wire messages for them.

A Warden (a Cell's always-on supervisor) loads a model on its own Cell without asking, but only
within the **ceilings** (bounds on its own local pool) the Queen (the central orchestrator) set
once: the Forage map source ids it may load (``Ceilings.loadable_sources``) and the VRAM it may
fill with models (``Ceilings.model_vram_bytes``), codingrules section 8.10. ``plan_loads`` is that
rule as a pure function: given a server's configured sources, the ceilings the Warden holds (or
None while the Queen has set none, so the operator's manifest alone bounds the load) and the VRAM
already in use, it returns the sources to load and, for each one it refused, why. ``hosted`` stamps
a source with the Warden's own Cell id, the one place ``host_cell_id`` is filled. ``added_message``
and ``removed_message`` build the two Waggle events (the Hive's bee-to-bee wire protocol) that tell
the Queen what changed.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    local_pool.hosting sub-package. Called by ``host`` (planning, row building) and ``report``
    (messages). Calls into ``hivemind.forage`` and waggle only.

Key invariants:
    - Pure: no I/O and no clock; the same inputs give the same plan.
    - VRAM is counted from ``ModelSourceSpec.vram_bytes_required`` (a source that declares none
      counts as zero) and is cumulative across a plan, so two sources that each fit but together
      do not never both load.
    - With no ceilings every source loads: the manifest is the operator's own allowlist.

See Also:
    - .claude/codingrules.md section 8.10 for "ceilings, not approvals".
    - waggle.messages.forage.sources for SourceAdded and SourceRemoved, the messages built here.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.forage import Ceilings, ModelSource
from waggle.ids import CellId
from waggle.messages.forage import SourceAdded, SourceRemovalCause, SourceRemoved

__all__ = ["LoadPlan", "added_message", "hosted", "plan_loads", "removed_message", "vram_of"]


@dataclass(frozen=True, slots=True)
class LoadPlan:
    """What ``plan_loads`` decided for one server.

    Attributes:
        load: The sources to load, in the server's configured order.
        refused: One ``(source_id, why)`` per source the ceilings kept out, for the log.
    """

    load: tuple[ModelSource, ...]
    refused: tuple[tuple[str, str], ...]


def vram_of(source: ModelSource) -> int:
    """Return the VRAM ``source`` needs loaded, zero for a source that declares none."""
    return source.spec.vram_bytes_required or 0


def plan_loads(
    sources: tuple[ModelSource, ...], ceilings: Ceilings | None, vram_in_use: int
) -> LoadPlan:
    """Pick the sources within ``ceilings`` and the VRAM left after ``vram_in_use``.

    Args:
        sources: The server's configured sources, in manifest order.
        ceilings: The Queen's ceilings for this Warden, or None when she has set none.
        vram_in_use: VRAM the Warden's already-loaded models occupy, in bytes.

    Returns:
        The sources to load and the reason each refused one was kept out.
    """
    if ceilings is None:
        return LoadPlan(load=sources, refused=())
    load: list[ModelSource] = []
    refused: list[tuple[str, str]] = []
    left = ceilings.model_vram_bytes - vram_in_use
    # One pass in manifest order: an allowlisted source takes the VRAM it needs from what is left,
    # so an earlier source can crowd out a later one, never the reverse.
    for source in sources:
        if source.source_id not in ceilings.loadable_sources:
            refused.append((source.source_id, "not in the ceilings' loadable_sources"))
        elif vram_of(source) > left:
            refused.append((source.source_id, f"needs {vram_of(source)} VRAM bytes, {left} left"))
        else:
            load.append(source)
            left -= vram_of(source)
    return LoadPlan(load=tuple(load), refused=tuple(refused))


def hosted(source: ModelSource, cell_id: CellId) -> ModelSource:
    """Return ``source`` served by ``cell_id``: its spec with ``host_cell_id`` filled."""
    spec = source.spec.model_copy(update={"host_cell_id": cell_id})
    return source.model_copy(update={"spec": spec})


def added_message(cell_id: CellId, server_id: str, source: ModelSource, reason: str) -> SourceAdded:
    """Build the ``forage.source_added`` event for ``source``, loaded on ``server_id``."""
    return SourceAdded(
        cell_id=cell_id,
        server_id=server_id,
        source=source.source_ref(),
        grade=source.spec.grade,  # The hand-set one, as the message says.
        context_window=source.spec.context_window,
        seats_total=source.spec.seats,
        vram_bytes=vram_of(source),
        capabilities=source.spec.capabilities,
        reason=reason,
    )


def removed_message(
    cell_id: CellId,
    server_id: str,
    source: ModelSource,
    cause: SourceRemovalCause,
    reason: str,
) -> SourceRemoved:
    """Build the ``forage.source_removed`` event for ``source``, gone from ``server_id``."""
    return SourceRemoved(
        cell_id=cell_id,
        server_id=server_id,
        source=source.source_ref(),
        cause=cause,
        reason=reason,
    )
