"""Define the Warden's routing step: choose a sub-bee's binding, bind along it, and report it.

Roadmap step 8.1 (ADR-0045): ``hivemind.llm.routing.choose_binding`` is a pure function that
reorders a slot's fallback chain by the task's tempo (how fast it must be done and how right it
must be) and the Forage map (the Hive's list of models, their grade, price and free seats). This
module is its effectful edge on a Warden (the per-Cell supervisor). ``route_spawn`` gathers the
facts routing weighs (the assignment's slot and tempo, the capabilities its Exoskeleton needs, the
live map, the Warden's own Cell, the hosting plan the Queen last sent, the ``[llm.slots]`` rows
and the operator's ``RoutingRules``) and returns a small ``Routed``; ``route_rebind`` does the same
for a rebind on escalation, with the grade floor raised for that one bee; ``bind_routed`` turns a
``Routed`` into a ``BoundModel`` whose fallback chain follows the routed order and whose head
carries the routed effort; and ``record_routing`` writes the ``llm.fallback`` trail event when the
chosen binding is not the slot's own and files a ``ForageRequest`` when the chosen source has no
free seat. With no map, no rules or no ``[llm.slots]`` row for the slot every function here does
nothing, so a Warden built before phase 8 binds the slot's own key exactly as it did.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens spawn
    sub-package. Called by ``hivemind.wardens.spawn.spawn`` for a first binding and by
    ``hivemind.wardens.ticks.alarms`` for a rebind on escalation. Calls into
    ``hivemind.llm.routing`` (the pure policy), ``hivemind.llm.slots`` (BoundModel),
    ``hivemind.wardens.requests`` (the ForageRequest builder), ``hivemind.pheromone`` (LlmEvent)
    and waggle only.

Key invariants:
    - Routing never widens what a sub-bee may call: ``Routed.key`` and every key in its order are
      keys of the slot's own manifest chain, and the Guard's ``slot_binding`` point still checks
      ``Routed.key`` (in ``spawn.py``) before anything starts.
    - ``bind_routed`` re-links every link's own ``fallback``, so no manifest-order link leaks into
      the routed chain.
    - A shortfall is reported, never waited on: ``record_routing`` sends the ForageRequest and
      returns; the Fanner (the seat meter every model call passes through) still makes the call.
    - ``route_rebind`` returns None rather than the binding that just failed, so a caller falls
      back to the grant's next allowed binding or escalates.

See Also:
    - docs/adr/0045-provider-routing-forage-aware-fallbacks-and-offline-mode.md for the policy.
    - hivemind.llm.routing for choose_binding, RoutingInputs and RoutingDecision.
    - hivemind.wardens.requests for forage_request.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.forage import Effort, HostingPlan, ModelSlot, Tempo
from hivemind.llm.routing import (
    ForageShortfall,
    RoutingDecision,
    RoutingInputs,
    choose_binding,
)
from hivemind.llm.slots import BoundModel
from hivemind.pheromone import LlmEvent
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.links import send_guarded
from hivemind.wardens.requests import ForageRequestInputs, forage_request
from waggle.envelope import wrap
from waggle.ids import CellId, GrantId, WorkerId, new_event_id
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.forage import ForageDelta, ForageRequestKind
from waggle.messages.forage.values import MAX_MODEL_GRADE
from waggle.messages.task import TaskAssign

VISION = "vision"  # The Forage-map capability flag a source needs to take an image.
AUDIO = "audio"  # The Forage-map capability flag a source needs to take audio parts.

__all__ = [
    "AUDIO",
    "VISION",
    "BindingTarget",
    "RouteScope",
    "Routed",
    "bind_routed",
    "pick_binding",
    "record_routing",
    "route_rebind",
    "route_spawn",
]


@dataclass(frozen=True, slots=True)
class Routed:
    """A routing decision reduced to what a spawn needs: the key, the effort and the evidence."""

    key: str  # The `[llm.slots]` key the sub-bee is bound to first.
    effort: Effort  # The effort the head link is asked for, from tempo and the row's own.
    decision: RoutingDecision  # The whole decision: chain order, reason, passed-over, shortfall.


# What `spawn_sub_bee` may be told to bind: an explicit manifest key (a Queen-ordered rebind,
# routing skipped) or a decision this module already made (a Warden's routed rebind).
BindingTarget = str | Routed


@dataclass(frozen=True, slots=True)
class RouteScope:
    """Everything routing a single sub-bee needs from its Warden, grouped for codingrules 5.1."""

    deps: WardenDeps  # The map, rules, `[llm.slots]` rows, local providers, trail and queen link.
    cell_id: CellId  # The Warden's own Cell: a source hosted there ranks as local.
    hosting: HostingPlan | None  # The plan the Queen last sent for that Cell, if any.
    assignment: TaskAssign  # The task the sub-bee will run: slot, tempo, Exoskeleton need.
    grant_id: GrantId  # The grant a Forage request would ask to extend.


def pick_binding(scope: RouteScope, override: BindingTarget | None) -> tuple[str, Routed | None]:
    """Choose the key a sub-bee binds to first, and the routing decision behind it if there is one.

    Args:
        scope: The Warden, Cell and assignment being routed.
        override: A rebind target. A string is a key to bind as given (routing is skipped, as it
            was before phase 8); a `Routed` is a decision a rebind already made.

    Returns:
        `(key, routed)`: `routed` is None when the key is the slot's own or an explicit string.
    """
    if isinstance(override, Routed):
        return override.key, override
    if override is not None:
        return override, None  # An explicit target: the caller already chose, routing must not.
    routed = route_spawn(scope)
    if routed is not None:
        return routed.key, routed
    return ModelSlot.from_wire(scope.assignment.slot).manifest_key, None


def route_spawn(scope: RouteScope) -> Routed | None:
    """Route the assignment's first binding by its tempo and the Forage map.

    Args:
        scope: The Warden, Cell and assignment being routed.

    Returns:
        The routing decision, or None when this Warden has no map, no routing rules or no
        `[llm.slots]` row for the slot (it then binds the slot's own key, as before phase 8).

    Raises:
        hivemind.llm.UnresolvableSlotError: A fallback in the slot's chain names a row that
            `WardenDeps.bindings` lacks, or the chain cycles (the manifest validators prevent both).
    """
    inputs = _inputs(scope)
    if inputs is None:
        return None
    decision = choose_binding(inputs)
    return Routed(key=decision.key, effort=decision.effort, decision=decision)


def route_rebind(scope: RouteScope, current: str) -> Routed | None:
    """Route again, one grade stronger per attempt, for a bee whose binding just failed.

    The floor is what the first routing used plus the assignment's attempt number (1 on the first
    try), so each escalation asks for a stronger model than the one before it. Routing only
    reorders the slot's chain, so when `current` is still the best binding that clears the raised
    floor there is nothing stronger to move to.

    Args:
        scope: The Warden, Cell and the failed bee's assignment (its `attempt` counts the tries).
        current: The `[llm.slots]` key the failed bee was bound to.

    Returns:
        The routed rebind, or None when routing is unavailable or picks `current` again (the
        caller then uses the grant's next allowed binding, or escalates).
    """
    inputs = _inputs(scope)
    if inputs is None:
        return None
    standing = choose_binding(inputs).reason.floor
    raised = inputs.rules.raise_floor(min(MAX_MODEL_GRADE, standing + scope.assignment.attempt))
    decision = choose_binding(dataclasses.replace(inputs, rules=raised))
    if decision.key == current:
        return None  # Nothing in the chain clears the raised floor better than the failed one.
    return Routed(key=decision.key, effort=decision.effort, decision=decision)


def bind_routed(rebind: Callable[[str], BoundModel], key: str, routed: Routed | None) -> BoundModel:
    """Resolve the sub-bee's `BoundModel`, along the routed order when there is one.

    Args:
        rebind: `WardenDeps.rebind`, resolving one `[llm.slots]` key to a live `BoundModel`.
        key: The key to bind first; used as given when `routed` is None.
        routed: The routing decision, or None for a plain single-key binding.

    Returns:
        With no decision, `rebind(key)`. With one, a chain whose links follow `decision.order`,
        each link's own fallback replaced so the manifest's order never leaks in, and the head's
        effort set to the routed effort.
    """
    if routed is None:
        return rebind(key)
    # Each key resolves to its own manifest-order chain; keep only the head link of each and
    # stitch them back together in the routed order, last link first.
    heads = [dataclasses.replace(rebind(link), fallback=None) for link in routed.decision.order]
    chain = heads[-1]  # The weakest-ranked link ends the chain; its own fallback is already None.
    for head in reversed(heads[:-1]):
        chain = dataclasses.replace(head, fallback=chain)
    return dataclasses.replace(chain, effort=routed.effort)


async def record_routing(scope: RouteScope, routed: Routed, worker_id: WorkerId) -> None:
    """Record `llm.fallback` for a non-primary choice, and file a ForageRequest for a seat.

    Args:
        scope: The Warden, Cell and assignment that were routed.
        routed: The decision being acted on.
        worker_id: The sub-bee the decision binds; the event's subject.
    """
    slot = ModelSlot.from_wire(scope.assignment.slot)
    if routed.key != slot.manifest_key:
        await _record_fallback(scope, routed, worker_id)
    shortfall = routed.decision.forage_shortfall
    if shortfall is not None:
        # Filed and forgotten: the Fanner still makes the call on the chosen binding, so the
        # spawn never waits on the Queen's reply (ADR-0045).
        await _file_shortfall(scope, routed, shortfall)


def _inputs(scope: RouteScope) -> RoutingInputs | None:
    """Build the routing inputs for the scope's assignment, or None when routing is not wired."""
    deps = scope.deps
    if deps.map is None or deps.routing_rules is None:
        return None
    assignment = scope.assignment
    slot = ModelSlot.from_wire(assignment.slot)
    # A Warden handed no `[llm.slots]` row for the slot (a Virtual Cell composed with no rows, a
    # bare test) has no chain to reorder; routing would only raise, so it binds the key as before.
    if not any(row.key == slot.manifest_key for row in deps.bindings):
        return None
    base = RoutingInputs.from_map(
        deps.map,
        slot,
        Tempo.from_wire(assignment.tempo),
        deps.bindings,
        deps.routing_rules,
    )
    return dataclasses.replace(
        base,
        required_capabilities=_required_capabilities(assignment),
        cell_id=scope.cell_id,
        hosting=scope.hosting,
        local_providers=deps.local_providers,
    )


def _required_capabilities(assignment: TaskAssign) -> frozenset[str]:
    """Return the Forage-map capability flags the task's Exoskeleton makes a model need.

    Mirrors `hivemind.workers.tools.exoskeleton.offer`: a desktop's screen and a browser's
    screenshots are offered to a model that sees, and its listen tool to one that hears.
    """
    need = assignment.exoskeleton
    if need is None:
        return frozenset()  # A terminal-only task has nothing to see or hear.
    return frozenset({VISION, AUDIO} if need.audio else {VISION})


async def _record_fallback(scope: RouteScope, routed: Routed, worker_id: WorkerId) -> None:
    """Record one `llm.fallback`: the routed binding is not the slot's own."""
    deps = scope.deps
    assignment = scope.assignment
    slot = ModelSlot.from_wire(assignment.slot)
    provider = next((row.provider for row in deps.bindings if row.key == routed.key), None)
    passed_over: list[JsonValue] = [
        {"key": passed.key, "cause": passed.cause.value} for passed in routed.decision.passed_over
    ]
    payload: dict[str, JsonValue] = {
        "slot": assignment.slot,
        "provider": provider,
        "from_key": slot.manifest_key,
        "to_key": routed.key,
        "reason": routed.decision.reason.render(),
        "passed_over": passed_over,
        "task_id": assignment.task_id,
        "goal_id": assignment.goal_id,
    }
    event = LlmEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind="llm.fallback",
        subject_id=worker_id,
        slot=assignment.slot,
        provider=provider,
        payload=payload,
    )
    await deps.trail.record(event)


async def _file_shortfall(scope: RouteScope, routed: Routed, shortfall: ForageShortfall) -> None:
    """Send the Queen a SHARED_SEATS ForageRequest for the seat the chosen source lacks."""
    deps = scope.deps
    assignment = scope.assignment
    reason = (
        f"Routing bound slot {assignment.slot} to {routed.key} on source {shortfall.source_id}, "
        f"which has no free seat; {shortfall.seats_wanted} wanted."
    )
    request = forage_request(
        ForageRequestInputs(
            grant_id=scope.grant_id,
            kind=ForageRequestKind.SHARED_SEATS,
            wanted=ForageDelta(
                seats=shortfall.seats_wanted,
                source_id=shortfall.source_id,
                slot=None,
                minimum_grade=None,
            ),
            task_id=assignment.task_id,
            tempo=assignment.tempo,
        ),
        reason[:MAX_REASON_CHARS],
    )
    # A gone Queen link is logged by send_guarded, never raised: the request is advisory.
    await send_guarded(deps.queen_link, wrap(request, deps.hop, clock=deps.clock))
