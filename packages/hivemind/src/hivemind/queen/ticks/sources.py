"""Handle a Warden's report that a model source was loaded on, or lost from, its Cell.

A Warden (a Cell's always-on supervisor) that starts a model server on its own Cell loads models
within the ceilings (bounds on its local pool) the Queen set once, and tells her afterwards: a
``forage.source_added`` when a source becomes servable, a ``forage.source_removed`` when it goes
(roadmap step 8.3, ADR-0044). She does not approve either; she learns of them.
``handle_source_item`` is the Queen's side: on an added source she puts it on her Forage map (the
live list of every source that can serve a model) with the Warden's Cell as its host, and records
the seats it offers in her ledger (her live book of Forage), so grants and hosting plans can count
on them; on a removal she takes the source off the map and zeroes its seats. Both are recorded on
the Pheromone Trail (the append-only audit log). If the map already lists the source (the
manifest's ``[forage.map]`` entry, with its prices and any measured grade) the Queen keeps those
figures and changes only what the report knows: the host, the seats and the VRAM. A report from a
Warden about a Cell that is not its own, or naming a host other than that Cell, is refused: a
Warden speaks only for its own Cell.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by ``hivemind.queen.ticks.liveness.handle_infrastructure_item``, ahead of
    the autopilot table, once per ``SourceAdded``/``SourceRemoved``. Calls into ``hivemind.forage``,
    ``hivemind.queen.deps``, ``hivemind.queen.trail`` (record_forage_event) and waggle only.

Key invariants:
    - A report is applied only when its ``cell_id`` is the sending Warden's own Cell and its source
      names that Cell as host; anything else is logged and dropped.
    - Applying the same report twice leaves the same map and ledger (the map replaces by source id
      and the seat book sets, not adds): a Warden whose report is replayed does no harm.
    - A removal sets the source's seat capacity to zero rather than forgetting it, so a stale seat
      reservation can never be granted against a source that is gone.

See Also:
    - docs/adr/0044-local-model-serving-via-openai-compatible-api.md for the decision.
    - hivemind.wardens.local_pool.hosting.report for the Warden's side of these messages.
    - hivemind.queen.forage.evict for the one order running the other way.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.common.logging import get_logger
from hivemind.forage import Abundance, ModelSource, ModelSourceSpec, UnknownSourceError
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.trail import record_forage_event
from hivemind.supervision.attendant import InboxItem
from waggle.ids import WardenId
from waggle.messages.forage import SourceAdded, SourceRemoved

# Which side recorded the row: the Warden records the same kinds as "warden" (hosting.report).
REPORTER = "queen"

log = get_logger(__name__)

__all__ = ["REPORTER", "handle_source_item"]


async def handle_source_item(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], item: InboxItem
) -> bool:
    """Apply a SourceAdded or SourceRemoved InboxItem; report whether it was one.

    Args:
        deps: The Queen's collaborators: her map, ledger and trail.
        wardens: Every Warden currently attached, keyed by id.
        item: The ordered InboxItem; ``item.principal`` is the reporting Warden's own id.

    Returns:
        True if ``item.payload`` was a SourceAdded or SourceRemoved (applied or refused); False
        otherwise, so the caller falls through to its other handlers.
    """
    payload = item.payload
    if not isinstance(payload, SourceAdded | SourceRemoved):
        return False
    warden_id = WardenId(item.principal)
    link = wardens.get(warden_id)
    # A Warden speaks only for its own Cell, and a source it reports is hosted on that Cell.
    if (
        link is None
        or payload.cell_id != link.cell.id
        or payload.source.host_cell_id != link.cell.id
    ):
        log.warning("queen.sources.refused", warden_id=warden_id, kind=item.payload_kind)
        return True
    if isinstance(payload, SourceAdded):
        await _add(deps, warden_id, payload)
    else:
        await _remove(deps, warden_id, payload)
    return True


async def _add(deps: QueenDeps, warden_id: WardenId, message: SourceAdded) -> None:
    """Put the reported source on the map, count its seats, and record that she learned of it."""
    source_id = message.source.source_id
    spec = _spec_for(deps, message)
    source = ModelSource(
        source_id=source_id, spec=spec, abundance=Abundance(seats_free=message.seats_total)
    )
    await deps.map.add_source(source)
    await deps.ledger.seats.set_capacity(source_id, message.seats_total)
    await record_forage_event(
        deps,
        "forage.source_added",
        message.cell_id,
        warden_id=warden_id,
        reporter=REPORTER,
        server_id=message.server_id,
        source_id=source_id,
        seats_total=message.seats_total,
    )


async def _remove(deps: QueenDeps, warden_id: WardenId, message: SourceRemoved) -> None:
    """Take the source off the map, zero its seats, and record that she learned it is gone."""
    source_id = message.source.source_id
    await deps.map.remove_source(source_id)
    await deps.ledger.seats.set_capacity(source_id, 0)
    await record_forage_event(
        deps,
        "forage.source_removed",
        message.cell_id,
        warden_id=warden_id,
        reporter=REPORTER,
        server_id=message.server_id,
        source_id=source_id,
        cause=message.cause.value,
    )


def _spec_for(deps: QueenDeps, message: SourceAdded) -> ModelSourceSpec:
    """Build the spec to list: the map's own row plus the report's facts, else the report alone."""
    host, seats = message.cell_id, message.seats_total
    vram = message.vram_bytes or None  # The spec reads "no local footprint" as None, never 0.
    try:
        known = deps.map.get(message.source.source_id)
    except UnknownSourceError:
        # Not in the manifest's map: the report is all she has, so grade and window are the
        # Warden's own (its hand-set figures).
        return ModelSourceSpec(
            provider=message.source.provider,
            model=message.source.model,
            grade=message.grade,
            context_window=message.context_window,
            capabilities=message.capabilities,
            seats=seats,
            host_cell_id=host,
            vram_bytes_required=vram,
        )
    # Kept: the manifest's prices and any measured grade, which only the Queen's side holds.
    return known.spec.model_copy(
        update={"host_cell_id": host, "seats": seats, "vram_bytes_required": vram}
    )
