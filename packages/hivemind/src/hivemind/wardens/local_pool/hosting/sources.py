"""Choose which of a server's sources may load, and build the rows and wire messages for them.

A Warden (a Cell's always-on supervisor) loads a model on its own Cell without asking, but only
within the **ceilings** (bounds on its own local pool) the Queen (the central orchestrator) set
once: the Forage map source ids it may load (``Ceilings.loadable_sources``) and the VRAM it may
fill with models (``Ceilings.model_vram_bytes``), codingrules section 8.10. ``plan_loads`` is that
rule as a pure function: given a server's configured sources, the ceilings the Warden holds (or
None while the Queen has set none, so the operator's manifest alone bounds the load) and the VRAM
already in use, it returns the sources to load and, for each one it refused, a ``Refusal``: the
word for what it lay outside of and why. ``narrow`` cuts a server's sources down to the ones the
Queen named (a Nuc promotion's first models) and refuses, as ``Refusal`` too, a name the server
does not serve. ``hosted`` stamps a source with the Warden's own Cell id, the one place
``host_cell_id`` is filled. ``added_message`` and ``removed_message`` build the two Waggle events
(the Hive's bee-to-bee wire protocol) that tell the Queen what changed.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    local_pool.hosting sub-package. Called by ``host`` (planning, row building), ``offering``
    (loading one more source) and ``report`` (messages). Calls into ``config``,
    ``hivemind.forage`` and waggle only.

Key invariants:
    - Pure: no I/O and no clock; the same inputs give the same plan.
    - VRAM is counted from ``ModelSourceSpec.vram_bytes_required`` (a source that declares none
      counts as zero) and is cumulative across a plan, so two sources that each fit but together
      do not never both load.
    - With no ceilings every source loads: the manifest is the operator's own allowlist.
    - A refusal names ids, counts and a scope word only, so it can be recorded as it stands.

See Also:
    - .claude/codingrules.md section 8.10 for "ceilings, not approvals".
    - waggle.messages.forage.sources for SourceAdded and SourceRemoved, the messages built here.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from hivemind.forage import Ceilings, ModelSource
from hivemind.wardens.local_pool.hosting.config import RunningServer
from waggle.ids import CellId
from waggle.messages.forage import SourceAdded, SourceRemovalCause, SourceRemoved

__all__ = [
    "LoadPlan",
    "Refusal",
    "added_message",
    "hosted",
    "narrow",
    "plan_loads",
    "removed_message",
    "vram_in_use",
    "vram_of",
]


@dataclass(frozen=True, slots=True)
class Refusal:
    """One source that may not be loaded, and what it lay outside of.

    Attributes:
        source_id: The source that was refused.
        scope: What it lay outside of, as the Guard names a refusal's rule
            (``guard.scope.<scope>``): ``loadable_source`` (not in the ceilings' allowlist),
            ``model_vram`` (more than the VRAM ceiling leaves) or ``server_source`` (not one of
            the server's sources at all).
        why: A sentence naming ids and counts only.
    """

    source_id: str
    scope: str
    why: str


@dataclass(frozen=True, slots=True)
class LoadPlan:
    """What ``plan_loads`` decided for one server.

    Attributes:
        load: The sources to load, in the server's configured order.
        refused: One ``Refusal`` per source the ceilings kept out.
    """

    load: tuple[ModelSource, ...]
    refused: tuple[Refusal, ...]


def vram_of(source: ModelSource) -> int:
    """Return the VRAM ``source`` needs loaded, zero for a source that declares none."""
    return source.spec.vram_bytes_required or 0


def vram_in_use(servers: Iterable[RunningServer]) -> int:
    """Return the VRAM the sources ``servers`` offer occupy, in bytes."""
    return sum(vram_of(source) for server in servers for source in server.sources)


def narrow(
    sources: tuple[ModelSource, ...], only: Collection[str] | None
) -> tuple[tuple[ModelSource, ...], tuple[Refusal, ...]]:
    """Cut ``sources`` down to the ones named in ``only``, refusing a name none answers to.

    Args:
        sources: A server's configured sources, in manifest order.
        only: The source ids the Queen asked for, or None for every source.

    Returns:
        The named sources that the server serves, in the server's order (the order its VRAM is
        spent in, whatever order they were asked for in), and one refusal per name it does not.
    """
    if only is None:
        return sources, ()
    served = {source.source_id for source in sources}
    unknown = tuple(
        Refusal(name, "server_source", f"{name!r} is not a source this server serves")
        for name in only
        if name not in served
    )
    return tuple(source for source in sources if source.source_id in only), unknown


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
    refused: list[Refusal] = []
    left = ceilings.model_vram_bytes - vram_in_use
    # One pass in manifest order: an allowlisted source takes the VRAM it needs from what is left,
    # so an earlier source can crowd out a later one, never the reverse.
    for source in sources:
        if source.source_id not in ceilings.loadable_sources:
            why = f"{source.source_id!r} is not in the ceilings' loadable_sources"
            refused.append(Refusal(source.source_id, "loadable_source", why))
        elif vram_of(source) > left:
            why = f"{source.source_id!r} needs {vram_of(source)} VRAM bytes, {left} are left"
            refused.append(Refusal(source.source_id, "model_vram", why))
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
