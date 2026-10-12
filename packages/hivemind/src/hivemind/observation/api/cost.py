"""Build the Cost view: spend per goal, slot and provider, and what moving a slot local would save.

Every model call the Fanner completes is an ``llm.call`` trail event carrying the slot it served,
the provider's manifest name and the normalised ``Usage`` (tokens in, out, cached, and cost in US
dollars; codingrules 8.6). Summing those answers "where did the money go?" without any provider
detail: per goal (when the call's lane carried a goal id, directly or through its grant), per slot
and per provider, plus the ``if_local_saving_usd`` of each slot, the hosted spend that would be
zero had the slot been bound to a local model. A call that cost nothing (a local model, or an
unpriced one) is not hosted spend, so the saving is an upper bound: it assumes a local binding
could have served every call the hosted one did. A call whose lane named neither goal nor grant is
grouped under a null goal, never attributed by guesswork.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.api``. Built from the
    trail's ``llm.call`` events by ``hivemind.entrance.reads.screens.cost``; the metrics snapshot
    reuses ``cost_lines``. Pure: calls into the LLM event model and pydantic.

Key invariants:
    - Pure: events are handed in; only ``llm.call`` events with usage are counted.
    - Per-goal, per-slot and per-provider totals each sum to the grand total.

See Also:
    - hivemind.llm.fanner.lane for the call payload (``goal_id`` and ``grant_id``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.pheromone import LlmEvent, PheromoneEvent

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

CALL_KIND = "llm.call"  # The one event kind that records a completed call with its usage.

__all__ = [
    "CALL_KIND",
    "CostLine",
    "CostView",
    "GoalCost",
    "ProviderCost",
    "SlotSaving",
    "cost_lines",
    "cost_view",
]


class CostLine(BaseModel):
    """The spend of one goal on one slot through one provider (observe)."""

    model_config = _CONFIG

    goal_id: str | None = Field(description="The goal it served; null when not attributable.")
    slot: str = Field(description="The model slot the calls were routed to.")
    provider: str = Field(description="The provider's manifest name.")
    calls: int = Field(description="Completed calls.")
    input_tokens: int = Field(description="Tokens consumed.")
    output_tokens: int = Field(description="Tokens generated.")
    cached_tokens: int = Field(description="Tokens served from a provider cache.")
    cost_usd: float = Field(description="Cost in US dollars.")


class GoalCost(BaseModel):
    """One goal's spend, by slot and provider."""

    model_config = _CONFIG

    goal_id: str | None = Field(description="The goal; null groups every unattributed call.")
    cost_usd: float = Field(description="Its total cost.")
    calls: int = Field(description="Its completed calls.")
    lines: list[CostLine] = Field(description="Its spend per slot and provider.")


class SlotSaving(BaseModel):
    """One slot's spend, and what binding it to a local model would have saved."""

    model_config = _CONFIG

    slot: str = Field(description="The model slot.")
    cost_usd: float = Field(description="Everything spent through it.")
    hosted_usd: float = Field(description="Spend on calls that cost money (hosted).")
    if_local_saving_usd: float = Field(
        description="The hosted spend that would be zero if the slot were local: an upper bound."
    )


class ProviderCost(BaseModel):
    """One provider's spend across all slots."""

    model_config = _CONFIG

    provider: str = Field(description="The provider's manifest name.")
    calls: int = Field(description="Completed calls.")
    cost_usd: float = Field(description="Its total cost.")


class CostView(BaseModel):
    """Spend per goal, slot and provider (observe)."""

    model_config = _CONFIG

    as_of: datetime = Field(description="When the view was built.")
    total_usd: float = Field(description="Every call counted.")
    if_local_saving_usd: float = Field(description="The slots' savings together.")
    goals: list[GoalCost] = Field(description="Per goal, costliest first.")
    slots: list[SlotSaving] = Field(description="Per slot, with its local saving.")
    providers: list[ProviderCost] = Field(description="Per provider, costliest first.")


@dataclass
class _Sum:
    """Running totals for one (goal, slot, provider); local to the fold."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0


def cost_lines(events: Sequence[PheromoneEvent], grant_goal: Mapping[str, str]) -> list[CostLine]:
    """Sum completed calls into one line per goal, slot and provider.

    Args:
        events: Trail events; only ``llm.call`` events carrying slot, provider and usage count.
        grant_goal: The goal each known grant was issued for, to attribute a call by its grant.

    Returns:
        The lines, sorted by goal (unattributed last), slot and provider.
    """
    sums: dict[tuple[str | None, str, str], _Sum] = defaultdict(_Sum)
    # Each call adds into its (goal, slot, provider) bucket.
    for event in events:
        if not isinstance(event, LlmEvent) or event.kind != CALL_KIND or event.usage is None:
            continue
        if event.slot is None or event.provider is None:
            continue
        total = sums[(_goal(event, grant_goal), event.slot, event.provider)]
        total.calls += 1
        total.input_tokens += event.usage.input_tokens
        total.output_tokens += event.usage.output_tokens
        total.cached_tokens += event.usage.cached_tokens
        total.cost_usd += event.usage.cost_usd
    keys = sorted(sums, key=lambda key: (key[0] is None, key[0] or "", key[1], key[2]))
    return [
        CostLine(
            goal_id=goal,
            slot=slot,
            provider=provider,
            calls=sums[(goal, slot, provider)].calls,
            input_tokens=sums[(goal, slot, provider)].input_tokens,
            output_tokens=sums[(goal, slot, provider)].output_tokens,
            cached_tokens=sums[(goal, slot, provider)].cached_tokens,
            cost_usd=sums[(goal, slot, provider)].cost_usd,
        )
        for goal, slot, provider in keys
    ]


def cost_view(
    events: Sequence[PheromoneEvent], grant_goal: Mapping[str, str], as_of: datetime
) -> CostView:
    """Build the Cost view from ``llm.call`` events.

    Args:
        events: Trail events (non-call kinds are ignored).
        grant_goal: The goal each known grant was issued for.
        as_of: When the view is built.

    Returns:
        Spend per goal, slot and provider, and each slot's local saving.
    """
    lines = cost_lines(events, grant_goal)
    slots = _slots(lines, events)
    return CostView(
        as_of=as_of,
        total_usd=sum(line.cost_usd for line in lines),
        if_local_saving_usd=sum(slot.if_local_saving_usd for slot in slots),
        goals=_goals(lines),
        slots=slots,
        providers=_providers(lines),
    )


def _slots(lines: Sequence[CostLine], events: Sequence[PheromoneEvent]) -> list[SlotSaving]:
    """Total each slot's spend and what going local would save on it."""
    spent: dict[str, float] = defaultdict(float)
    for line in lines:
        spent[line.slot] += line.cost_usd
    # Hosted spend per slot: only calls that cost money could have been free had they been local.
    hosted = _hosted_by_slot(events)
    return [
        SlotSaving(
            slot=slot,
            cost_usd=spent[slot],
            hosted_usd=hosted.get(slot, 0.0),
            if_local_saving_usd=hosted.get(slot, 0.0),
        )
        for slot in sorted(spent)
    ]


def _goals(lines: Sequence[CostLine]) -> list[GoalCost]:
    """Group lines by goal, costliest goal first, the unattributed one after its equals."""
    by_goal: dict[str | None, list[CostLine]] = defaultdict(list)
    for line in lines:
        by_goal[line.goal_id].append(line)
    return sorted(
        (_goal_cost(goal, goal_lines) for goal, goal_lines in by_goal.items()),
        key=lambda goal: (-goal.cost_usd, goal.goal_id is None, goal.goal_id or ""),
    )


def _providers(lines: Sequence[CostLine]) -> list[ProviderCost]:
    """Total each provider's calls and cost, costliest first."""
    by_provider: dict[str, list[CostLine]] = defaultdict(list)
    for line in lines:
        by_provider[line.provider].append(line)
    return sorted(
        (
            ProviderCost(
                provider=provider,
                calls=sum(line.calls for line in provider_lines),
                cost_usd=sum(line.cost_usd for line in provider_lines),
            )
            for provider, provider_lines in by_provider.items()
        ),
        key=lambda item: (-item.cost_usd, item.provider),
    )


def _goal_cost(goal: str | None, lines: Sequence[CostLine]) -> GoalCost:
    """Total one goal's lines."""
    return GoalCost(
        goal_id=goal,
        cost_usd=sum(line.cost_usd for line in lines),
        calls=sum(line.calls for line in lines),
        lines=list(lines),
    )


def _goal(event: LlmEvent, grant_goal: Mapping[str, str]) -> str | None:
    """Attribute a call to a goal: its own goal id, else its grant's goal, else none."""
    goal, grant = event.payload.get("goal_id"), event.payload.get("grant_id")
    if isinstance(goal, str):
        return goal
    return grant_goal.get(grant) if isinstance(grant, str) else None


def _hosted_by_slot(events: Sequence[PheromoneEvent]) -> dict[str, float]:
    """Sum, per slot, the cost of calls that cost money."""
    hosted: dict[str, float] = defaultdict(float)
    for event in events:
        if not isinstance(event, LlmEvent) or event.kind != CALL_KIND or event.usage is None:
            continue
        if event.slot is not None and event.usage.cost_usd > 0:
            hosted[event.slot] += event.usage.cost_usd
    return hosted
