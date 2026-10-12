"""Say where a Cell's models come from, from its hosting plan and the Forage map.

The Queen writes each Cell a ``HostingPlan`` (codingrules 8.10): per model slot, a primary Forage
map source and its fallbacks. A source is only an id in the plan; the map says which Cell's server
serves it, or that it is a hosted provider. The Fleet badge and the Cell page need the one-word
answer an operator reads at a glance, so this module classifies each slot's primary source against
the Cell the plan is for: ``local`` (a Cell's own model server, this Cell's or a peer's),
``hive_stand`` (served by the Hive Stand's Cell), ``hosted`` (a provider), and ``hybrid`` when the
plan mixes them. A Cell with no plan, or a plan naming only sources the map no longer holds, is
``unknown``: never guessed.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.api``. Used by the
    fleet and Cell page builders. Pure: calls into the Forage plan and map models and pydantic.

Key invariants:
    - Pure: the plans and the map's hosts are handed in as ``HostingFacts``.
    - Only a slot's primary source (and the default's) counts; a fallback is a contingency.

See Also:
    - hivemind.queen.forage.hosting for the writer of the plan.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hivemind.forage import HostingPlan, ModelSlot, ModelSource

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

ModelsFrom = Literal["local", "hive_stand", "hosted", "hybrid", "unknown"]
_Place = Literal["local", "hive_stand", "hosted"]  # Where one source runs, relative to a Cell.

__all__ = [
    "HostingFacts",
    "HostingView",
    "ModelsFrom",
    "SlotHostingView",
    "hosting_facts",
    "hosting_view",
    "models_from",
]


@dataclass(frozen=True, slots=True)
class HostingFacts:
    """What classifying a plan needs besides the plan: who hosts each source.

    Attributes:
        plans: Each Cell's current hosting plan.
        hosts: Each Forage map source's host Cell id; None for a hosted provider.
        hive_stand_cells: The Cells that are the Hive Stand (their servers are 'hive_stand').
    """

    plans: Mapping[str, HostingPlan]
    hosts: Mapping[str, str | None]
    hive_stand_cells: frozenset[str]


class SlotHostingView(BaseModel):
    """One slot's primary source in a Cell's plan, and where that source runs."""

    model_config = _CONFIG

    slot: ModelSlot = Field(description="The model slot.")
    primary_source: str = Field(description="The Forage map source every call tries first.")
    models_from: ModelsFrom = Field(description="Where that source runs, for this Cell.")


class HostingView(BaseModel):
    """Where a Cell's bees get their models: the one-word badge and the plan behind it."""

    model_config = _CONFIG

    models_from: ModelsFrom = Field(description="local, hive_stand, hosted, hybrid or unknown.")
    plan_revision: int | None = Field(description="The plan's revision; null with no plan.")
    slots: list[SlotHostingView] = Field(description="Each planned slot's primary source.")


def hosting_facts(
    plans: Iterable[HostingPlan], sources: Iterable[ModelSource], hive_stand_cells: Iterable[str]
) -> HostingFacts:
    """Index the plans and the Forage map's hosts for classification.

    Args:
        plans: Each Cell's current hosting plan.
        sources: The Forage map's sources.
        hive_stand_cells: The ids of the Cells that are the Hive Stand.

    Returns:
        The facts ``models_from`` and ``hosting_view`` classify with.
    """
    return HostingFacts(
        plans={plan.cell_id: plan for plan in plans},
        hosts={source.source_id: source.spec.host_cell_id for source in sources},
        hive_stand_cells=frozenset(hive_stand_cells),
    )


def models_from(cell_id: str, facts: HostingFacts) -> ModelsFrom:
    """Classify where a Cell's models come from.

    Args:
        cell_id: The Cell.
        facts: The plans and hosts.

    Returns:
        ``unknown`` with no plan or no classifiable source; the one place when all agree; else
        ``hybrid``.
    """
    return hosting_view(cell_id, facts).models_from


def hosting_view(cell_id: str, facts: HostingFacts) -> HostingView:
    """Build a Cell's hosting view: each slot's primary source and the overall badge.

    Args:
        cell_id: The Cell.
        facts: The plans and hosts.

    Returns:
        Its hosting view; ``unknown`` and empty when the Cell has no plan.
    """
    plan = facts.plans.get(cell_id)
    if plan is None:
        return HostingView(models_from="unknown", plan_revision=None, slots=[])
    slots: list[SlotHostingView] = []
    places: set[_Place] = set()
    # Each slot's primary source, classified; a source the map no longer holds is left out.
    for slot_plan in plan.slots:
        place = _place(slot_plan.chain.primary, cell_id, facts)
        slots.append(
            SlotHostingView(
                slot=slot_plan.slot,
                primary_source=slot_plan.chain.primary,
                models_from=place or "unknown",
            )
        )
        if place is not None:
            places.add(place)
    # The default chain serves every slot the plan does not name.
    default = _place(plan.default.primary, cell_id, facts)
    if default is not None:
        places.add(default)
    return HostingView(models_from=_badge(places), plan_revision=plan.revision, slots=slots)


def _place(source_id: str, cell_id: str, facts: HostingFacts) -> _Place | None:
    """Return where one source runs relative to ``cell_id``; None when the map lacks it."""
    if source_id not in facts.hosts:
        return None
    host = facts.hosts[source_id]
    # A hosted provider has no host Cell.
    if host is None:
        return "hosted"
    if host == cell_id:
        return "local"
    # Another Cell's server: the Hive Stand's is named; a peer's counts as local hardware.
    return "hive_stand" if host in facts.hive_stand_cells else "local"


def _badge(places: set[_Place]) -> ModelsFrom:
    """Collapse the places a plan uses into the one-word badge."""
    if not places:
        return "unknown"
    if len(places) > 1:
        return "hybrid"
    return next(iter(places))
