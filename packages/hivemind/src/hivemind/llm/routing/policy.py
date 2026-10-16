"""Define choose_binding: pick which binding of a slot's fallback chain a bee calls first.

Routing is a pure function (codingrules section 8.3): it reads one ``RoutingInputs`` and returns a
``RoutingDecision``, touching no clock, no lock and no network, so the same inputs always give the
same answer and a test can drive it with plain values. It never invents a binding outside the
slot's own manifest fallback chain (the ``[llm.slots]`` rows reached from the slot's key); it
reorders that chain, chosen first, then the rest in rank order. Each binding is first judged
by ``hivemind.llm.routing.eligibility`` (grade at or over the floor, capabilities present,
within the latency budget and the spend cap, local when offline), then the eligible ones are
ranked by a sort key: known to Forage before unknown, with a free seat before none, in the Cell's
hosting plan chain (primary first) before outside it, running on the bee's own Cell before
elsewhere, cheaper, closer, and finally manifest order so a tie is never random. The ineligible
bindings follow, ranked by the same key, so the chain's tail (what the Fanner spills along) is as
well ordered as its head. When no binding is eligible routing still answers: it chooses the
best-ranked of them (the manifest primary when they tie) and the reason says it fell back, because
the Fanner (the seat meter every call passes through) never refuses a call and neither does
routing. A chosen source with no free seat is reported as a ``ForageShortfall`` so the caller can
file a ``ForageRequest`` rather than queue unseen.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.llm.routing``. Called by
    the Warden when it spawns or rebinds a sub-bee and by the Queen for her own episodes (later
    roadmap steps); the Fanner then spills along the returned ``order`` at call time. Calls into
    ``hivemind.llm.slots`` (``walk_chain``), ``hivemind.forage`` and the routing package's own
    modules only.

Key invariants:
    - ``decision.key`` is always a member of the slot's chain and ``decision.order`` is a
      permutation of it; the function never reads outside ``inputs``.
    - Raising the floor (``rules.raised_floor`` or a stricter accuracy bar) never lowers the
      grade of the chosen known source while any known binding stays eligible.
    - No ``provider.name`` or ``cell.kind`` branch: a source is local because its
      ``host_cell_id`` is the bee's Cell, and a provider is inside the Hive because the caller
      listed it in ``local_providers``.

See Also:
    - .claude/roadmap.md step 8.1 and docs/adr/0044 (routing as a pure function over Forage).
    - hivemind.llm.fanner.spill for the call-time spill rule that walks the reordered chain.
    - hivemind.queen.forage.hosting for the plan whose chain this ranks by.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.forage import ModelSource, SlotBinding
from hivemind.llm.routing.decision import (
    ForageShortfall,
    PassedOver,
    PassOverCause,
    RoutingDecision,
    RoutingReason,
)
from hivemind.llm.routing.effort import choose_effort
from hivemind.llm.routing.eligibility import effective_floor, ineligible_cause
from hivemind.llm.routing.inputs import RoutingInputs
from hivemind.llm.slots import walk_chain

__all__ = ["choose_binding"]

SEATS_WANTED = 1  # A single bee needs a single seat; a caller scales it for a batch.

# (unknown to Forage, no free seat, position in the hosting plan chain, not local to the bee's
# Cell, price per million tokens, measured latency, manifest position). Lower sorts first.
_RankKey = tuple[int, int, int, int, float, float, int]


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One chain binding with the Forage source it names and whether it may be chosen."""

    index: int  # Position in the slot's manifest chain; the final tiebreak.
    row: SlotBinding  # The `[llm.slots]` row.
    source: ModelSource | None  # The map source for the row's provider and model, if any.
    cause: PassOverCause | None  # Why it is ineligible; None when it may be chosen.


def choose_binding(inputs: RoutingInputs) -> RoutingDecision:
    """Choose which binding of the slot's fallback chain to call first.

    Args:
        inputs: The slot, tempo, required capabilities, Forage map sources, chain rows, hosting
            plan and rules, gathered in one value.

    Returns:
        The decision: chosen key, the chain reordered with it first, the effort, the reason as
        data, the bindings passed over and, when the chosen source has no free seat, the
        shortfall. When no binding is eligible it chooses the best-ranked ineligible one, the
        slot's own primary when they tie (``fell_back``).

    Raises:
        UnresolvableSlotError: ``inputs.bindings`` has no row for the slot's own key, or the
            chain cycles; the manifest validators make this unreachable for a loaded manifest.
    """
    chain = walk_chain(inputs.slot.manifest_key, inputs.bindings)
    floor = effective_floor(inputs)
    candidates = [_judge(index, row, floor, inputs) for index, row in enumerate(chain)]
    plan = _plan_positions(inputs)

    def rank(candidate: _Candidate) -> _RankKey:
        """Order candidates for these inputs; the one key both halves of the chain sort by."""
        return _rank_key(candidate, inputs, plan)

    eligible = sorted((c for c in candidates if c.cause is None), key=rank)
    # Ineligible bindings follow, ranked by the same key rather than manifest order: with none
    # eligible this is the whole chain and routing never refuses, so the one it falls back to
    # should still be the one with a seat, in the plan, local and cheap, not merely the first row.
    ranked = eligible + sorted((c for c in candidates if c.cause is not None), key=rank)
    chosen, fell_back = ranked[0], not eligible
    return RoutingDecision(
        key=chosen.row.key,
        order=tuple(c.row.key for c in ranked),
        effort=choose_effort(inputs.tempo, chosen.row.effort),
        reason=_reason(chosen, (floor, len(candidates), len(eligible), fell_back), inputs, plan),
        passed_over=_passed_over(ranked, chosen, fell_back),
        forage_shortfall=None if fell_back else _shortfall(chosen),
    )


def _judge(index: int, row: SlotBinding, floor: int, inputs: RoutingInputs) -> _Candidate:
    """Pair a chain row with its Forage source and the verdict on whether it may be chosen."""
    source = _source_for(row, inputs)
    return _Candidate(index, row, source, ineligible_cause(row, source, inputs, floor))


def _source_for(row: SlotBinding, inputs: RoutingInputs) -> ModelSource | None:
    """Return the map source ``row`` names, preferring one that runs on the bee's own Cell."""
    matches = [
        s for s in inputs.sources if s.spec.provider == row.provider and s.spec.model == row.model
    ]
    # The same model can be on the map twice (a server per Cell); the bee's own Cell's copy is
    # the one it would actually reach first, so it stands for the row.
    local = [s for s in matches if _is_local(s, inputs)]
    return next(iter(local or matches), None)


def _is_local(source: ModelSource, inputs: RoutingInputs) -> bool:
    """Return whether ``source`` is served from the Cell the bee runs on."""
    return inputs.cell_id is not None and source.spec.host_cell_id == inputs.cell_id


def _plan_positions(inputs: RoutingInputs) -> dict[str, int]:
    """Map each source id in the hosting plan's chain for the slot to its position, primary 0."""
    if inputs.hosting is None:
        return {}
    for slot_plan in inputs.hosting.slots:
        if slot_plan.slot is inputs.slot:
            chain = (slot_plan.chain.primary, *slot_plan.chain.fallbacks)
            positions: dict[str, int] = {}
            for position, source_id in enumerate(chain):
                positions.setdefault(source_id, position)  # A repeated id keeps its best place.
            return positions
    return {}  # The plan names no chain for this slot: it has no opinion, nothing ranks by it.


def _starved(source: ModelSource) -> bool:
    """Return whether ``source`` has no seat to give: none free, or none offered at all."""
    return source.abundance.seats_free == 0 or source.spec.seats == 0


def _rank_key(candidate: _Candidate, inputs: RoutingInputs, plan: dict[str, int]) -> _RankKey:
    """Build the sort key that orders eligible candidates; lower is chosen first."""
    source = candidate.source
    if source is None:
        # Unknown to Forage: no seats, plan place, price or distance to compare, so it ranks after
        # every known candidate and keeps manifest order among its own kind.
        return (1, 0, 0, 0, 0.0, 0.0, candidate.index)
    price = (
        source.spec.cost.cost_per_million_input_usd + source.spec.cost.cost_per_million_output_usd
    )
    # An unmeasured source is as far as it could be: a measured one is a known quantity.
    latency = source.distance.latency_s if source.distance is not None else float("inf")
    return (
        0,
        int(_starved(source)),
        plan.get(source.source_id, len(plan)),
        int(not _is_local(source, inputs)),
        price,
        latency,
        candidate.index,
    )


def _reason(
    chosen: _Candidate,
    counts: tuple[int, int, int, bool],
    inputs: RoutingInputs,
    plan: dict[str, int],
) -> RoutingReason:
    """Describe the choice as data; ``counts`` is (floor, considered, eligible, fell_back)."""
    floor, considered, eligible, fell_back = counts
    source = chosen.source
    return RoutingReason(
        floor=floor,
        considered=considered,
        eligible=eligible,
        chosen_grade=None if source is None else source.effective_grade,
        local=source is not None and _is_local(source, inputs),
        planned=source is not None and source.source_id in plan,
        fell_back=fell_back,
    )


def _passed_over(
    ranked: list[_Candidate], chosen: _Candidate, fell_back: bool
) -> tuple[PassedOver, ...]:
    """List every binding not chosen that has a stated cause, in rank order.

    A fell-back primary is listed too: it was judged and failed, and the list says why.
    """
    passed: list[PassedOver] = []
    for candidate in ranked:
        cause = _pass_over_cause(candidate)
        # The chosen binding is not "passed over", except when it was chosen only because
        # nothing else was left.
        if cause is not None and (candidate is not chosen or fell_back):
            passed.append(PassedOver(candidate.row.key, cause))
    return tuple(passed)


def _pass_over_cause(candidate: _Candidate) -> PassOverCause | None:
    """Return the cause that demoted or removed ``candidate``, None for a plain eligible peer."""
    if candidate.cause is not None:
        return candidate.cause
    if candidate.source is None:
        return PassOverCause.NOT_ON_MAP
    return PassOverCause.NO_SEAT if _starved(candidate.source) else None


def _shortfall(chosen: _Candidate) -> ForageShortfall | None:
    """Return the seat the bee lacks on the chosen source, or None when it has one or is unknown."""
    source = chosen.source
    if source is None or not _starved(source):
        return None
    return ForageShortfall(source_id=source.source_id, seats_wanted=SEATS_WANTED)
