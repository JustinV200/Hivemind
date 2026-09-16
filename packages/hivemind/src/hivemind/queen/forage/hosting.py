"""Define write_hosting_plan: the Queen writes one Cell's HostingPlan from the map and the ledger.

Roadmap step 4.8: "per Cell, write a HostingPlan (per slot a primary source and a fallback chain,
plus a default) from the Forage map and ledger: the Cell's free VRAM against each model's
requirement, seat pressure on the Hive Stand, the measured distance from the Cell to each
candidate source against the task's tempo, the task's need to survive disconnection, and cost
caps; local sources first wherever the Cell has them; recorded as forage.plan_written with the
reason." `_decide_plan` is the pure half (codingrules section 8.3): given a Cell (a unit of
compute), every Forage map source, the shared pool's current seat headroom and every live grant's
spend cap, it ranks every slot's candidate sources and returns a `hivemind.forage.HostingPlan`
plus the `PlanReason` that explains it. `write_hosting_plan` is the effectful edge: it calls
`_decide_plan`, then writes the ledger's own copy (`ForageLedger.decisions.record_plan`) and
records `forage.plan_written` on the trail, in that order, before returning the plan.

`write_hosting_plan(cell, deps)` takes no explicit `TaskNeeds` (a specific task's own requirements,
`hivemind.cell.needs.TaskNeeds`): a plan is written once per Cell, ahead of any one task landing
there, so this module reads two proxies instead of a live task. Tempo: `hivemind.forage.tempo.
Tempo()` (NORMAL accuracy, no latency budget) -- the plan's own grade floor and distance check use
this default, and a task whose own Tempo demands more than a plan's primary can give still spills
through the Fanner at call time (codingrules section 8.10's own "spill-over is a Fanner rule").
Disconnection survival: `hivemind.cell.needs.TaskNeeds` (this dispatch's own file-list audit,
recorded in its report) carries no field naming whether a task must survive its Cell going
offline, and codingrules section 6.1 forbids branching on `cell.kind` directly ("branch on
capabilities instead" -- `scripts/check_no_kind_branches.py` enforces it), so this module reads
`cell.capabilities.can_host_model` instead: only a Cell that can run a local model server is ever
a Nuc candidate at all (codingrules section 8.10's own definition), so biasing local-first
specifically where that capability is true is the closest a Cell-wide plan can come to "survive
disconnection" without a live task's own preference; a future `TaskNeeds` field would let a
specific task override this Cell-wide default.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Called by whatever composition root or later tick writes a Cell's plan (a later
    dispatch's own wiring; see this module's report for the exact call site still to add). Calls
    into `hivemind.cell` (Cell), `hivemind.forage` (HostingPlan, ModelSource, SlotChain types,
    Tempo, grade_floor), `hivemind.queen.deps` (QueenDeps) and `hivemind.queen.trail`
    (record_forage_event) only.

Key invariants:
    - `_decide_plan` is pure: same inputs, same `HostingPlan`; no I/O, no trail write, no ledger
      write. `write_hosting_plan` is the only caller that performs either.
    - A slot with no eligible candidate source at all is left out of `plan.slots` (not planned
      with an empty chain, which `SourceChain.primary` -- a required field -- could not express);
      `plan.default` always names the single best candidate across every slot's own pool, so a
      slot the plan does not name still has somewhere to go.

See Also:
    - .claude/roadmap.md step 4.8 for the exact wording this module implements.
    - .claude/codingrules.md section 8.3 for the pure-decision, effectful-edge split.
    - .claude/codingrules.md section 8.10 for "a hosting plan, not a hosting flag".
    - docs/adr/0016-forage-two-pools-ceilings-and-hosting-plans.md for HostingPlan's own shape.
    - docs/adr/0023-forage-ledger-and-model-hosting-decisions.md for "hosting plans... written by
      the Queen from the map and ledger with a reason; headroom is derived on read, never stored".
    - hivemind.queen.forage.ceilings for set_ceilings/change_ceilings, this module's sibling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.cell import Cell
from hivemind.forage import HostingPlan, ModelSource, SlotPlan, SourceChain, Tempo, grade_floor
from hivemind.forage.map import SlotBinding
from hivemind.forage.slots import ModelSlot
from hivemind.queen.trail import record_forage_event
from waggle.envelope import wrap
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.forage import PlanWritten

if TYPE_CHECKING:
    # Only for the type hint below: see hivemind.queen.forage.grants's own module docstring note
    # on why QueenDeps/WardenLink cannot be real imports inside hivemind.queen.forage.
    from hivemind.queen.deps import QueenDeps, WardenLink

__all__ = ["PlanReason", "write_hosting_plan"]

_DEFAULT_TEMPO = Tempo()  # NORMAL accuracy, no latency budget; see the module docstring.
MAX_PLAN_FALLBACKS = 8  # Mirrors waggle.messages.forage.capacity.MAX_FALLBACKS.


@dataclass(frozen=True, slots=True)
class PlanReason:
    """The factors that decided a HostingPlan, as data (roadmap step 4.8's own words).

    Attributes:
        local_sources_available: How many of the Cell's own local sources were eligible for at
            least one slot.
        seat_pressure: True when the shared pool's own seat headroom was at or below zero,
            excluding non-local candidates wherever a local one existed.
        offline_survival_required: True when `cell.capabilities.can_host_model` (module
            docstring's own proxy: only a Nuc candidate can survive disconnection at all),
            preferring the Cell's own local sources.
        cost_capped: True when the goal-level spend cap was zero, excluding every non-free source.
        slots_planned: How many slots this plan named a chain for.
    """

    local_sources_available: int
    seat_pressure: bool
    offline_survival_required: bool
    cost_capped: bool
    slots_planned: int

    def render(self) -> str:
        """Render this reason as the bounded string `HostingPlan.reason` carries."""
        text = (
            f"{self.slots_planned} slot(s) planned; {self.local_sources_available} local "
            f"source(s) available; seat_pressure={self.seat_pressure}; "
            f"offline_survival_required={self.offline_survival_required}; "
            f"cost_capped={self.cost_capped}."
        )
        return text[:MAX_REASON_CHARS]


async def write_hosting_plan(
    cell: Cell, deps: QueenDeps, warden: WardenLink | None = None
) -> HostingPlan:
    """Write, store and record a Cell's HostingPlan (the effectful edge; see module docstring).

    Args:
        cell: The Cell this plan is for.
        deps: The Queen's collaborators; `map` and `ledger` are this decision's own inputs.
        warden: The Cell's own attached Warden, when known; its own `PlanWritten` is sent over
            this link before the plan is recorded (mirroring `hivemind.queen.forage.ceilings.
            set_ceilings`'s own write-then-send-then-record order). `None` (every pre-4.10-wiring
            caller, and any test that only cares about the decision itself) skips the send.

    Returns:
        The written plan, already recorded in the ledger and on the trail.
    """
    existing = deps.ledger.decisions.plan_for(cell.id)
    revision = 0 if existing is None else existing.revision + 1
    sources = deps.map.sources()
    inputs = _PlanInputs(
        sources=sources,
        bindings=deps.bindings,
        seat_headroom=deps.ledger.headroom().shared_seats,
        spend_cap_usd=deps.budgets.spend_cap_usd,
        revision=revision,
    )
    plan, _reason = _decide_plan(cell, inputs)
    await deps.ledger.decisions.record_plan(plan)
    if warden is not None:
        await _send_plan(warden, plan, sources, deps)
    await record_forage_event(
        deps, "forage.plan_written", cell.id, revision=plan.revision, slots=len(plan.slots)
    )
    return plan


async def _send_plan(
    warden: WardenLink, plan: HostingPlan, sources: tuple[ModelSource, ...], deps: QueenDeps
) -> None:
    """Send `plan` to `warden` as a PlanWritten (the send `write_hosting_plan` used to skip)."""
    by_id = {source.source_id: source for source in sources}
    message = PlanWritten(
        cell_id=plan.cell_id,
        revision=plan.revision,
        slots=tuple(slot_plan.to_wire(by_id) for slot_plan in plan.slots),
        default=plan.default.to_wire(by_id),
        reason=plan.reason,
    )
    await warden.transport.send(wrap(message, warden.hop, clock=deps.clock))


@dataclass(frozen=True, slots=True)
class _PlanInputs:
    """Group `_decide_plan`'s own inputs beyond `cell` (codingrules section 5.1's 4-param limit)."""

    sources: tuple[ModelSource, ...]
    bindings: tuple[SlotBinding, ...]
    seat_headroom: int
    spend_cap_usd: float
    revision: int


def _decide_plan(cell: Cell, inputs: _PlanInputs) -> tuple[HostingPlan, PlanReason]:
    """Pure decision: rank every slot's candidate sources and build the HostingPlan.

    Args:
        cell: The Cell this plan is for.
        inputs: Every Forage map source, the `[llm.slots]` bindings, the shared pool's own seat
            headroom, the goal-level spend cap and this plan's revision number.

    Returns:
        The plan, and the `PlanReason` that explains it.
    """
    slot_plans = _build_slot_plans(cell, inputs)
    default_chain = _build_chain(_rank(list(inputs.sources), cell, inputs), inputs.sources)
    local_count = sum(1 for s in inputs.sources if s.spec.host_cell_id == cell.id)
    reason = PlanReason(
        local_sources_available=local_count,
        seat_pressure=inputs.seat_headroom <= 0,
        offline_survival_required=cell.capabilities.can_host_model,
        cost_capped=_cost_capped(inputs),
        slots_planned=len(slot_plans),
    )
    plan = HostingPlan(
        cell_id=cell.id,
        revision=inputs.revision,
        slots=tuple(slot_plans),
        default=default_chain,
        reason=reason.render(),
    )
    return plan, reason


def _cost_capped(inputs: _PlanInputs) -> bool:
    """Return whether the spend cap actually excluded a candidate this decision considered."""
    if inputs.spend_cap_usd > 0:
        return False
    return any(s.spec.cost.cost_per_million_input_usd > 0 for s in inputs.sources)


def _build_slot_plans(cell: Cell, inputs: _PlanInputs) -> list[SlotPlan]:
    """Rank every slot's own candidates and build a SlotPlan for each with at least one."""
    slot_plans: list[SlotPlan] = []
    for slot in _slots_in(inputs.bindings):
        candidates = _candidates_for_slot(slot, inputs.bindings, inputs.sources)
        ranked = _rank(candidates, cell, inputs)
        if not ranked:
            continue
        slot_plans.append(SlotPlan(slot=slot, chain=_build_chain(ranked, inputs.sources)))
    return slot_plans


def _build_chain(ranked: list[ModelSource], sources: tuple[ModelSource, ...]) -> SourceChain:
    """Build a SourceChain from a ranked candidate list, falling back to any known source."""
    if not ranked:
        return SourceChain(primary=_fallback_source_id(sources), fallbacks=())
    return SourceChain(
        primary=ranked[0].source_id,
        fallbacks=tuple(s.source_id for s in ranked[1 : 1 + MAX_PLAN_FALLBACKS]),
    )


def _slots_in(bindings: tuple[SlotBinding, ...]) -> tuple[ModelSlot, ...]:
    """Return every ModelSlot named by `bindings`' own keys, in no particular order."""
    slots: list[ModelSlot] = []
    for binding in bindings:
        try:
            slots.append(ModelSlot.from_manifest_key(binding.key))
        except KeyError:
            continue  # A named fallback binding (e.g. "local_worker"), not a slot of its own.
    return tuple(slots)


def _candidates_for_slot(
    slot: ModelSlot, bindings: tuple[SlotBinding, ...], sources: tuple[ModelSource, ...]
) -> list[ModelSource]:
    """Return every source on the map that some binding in `slot`'s own fallback chain names."""
    by_key = {binding.key: binding for binding in bindings}
    candidates: list[ModelSource] = []
    seen_keys: set[str] = set()
    current: str | None = slot.manifest_key
    while current is not None and current not in seen_keys:
        seen_keys.add(current)
        binding = by_key.get(current)
        if binding is None:
            break
        candidates.extend(
            s
            for s in sources
            if s.spec.provider == binding.provider and s.spec.model == binding.model
        )
        current = binding.fallback
    return candidates


def _rank(candidates: list[ModelSource], cell: Cell, inputs: _PlanInputs) -> list[ModelSource]:
    """Rank candidates local-first, then by grade/distance/VRAM/seat fit, then by cost.

    A source is excluded outright when its declared VRAM requirement does not fit the Cell's own
    free VRAM (only checked for a source local to this Cell -- a shared source's VRAM is the
    remote Cell's own concern), when its cost is non-zero and the goal-level spend cap is zero, or
    when a local alternative exists and either the shared pool has no seat headroom left or the
    Cell needs to survive disconnection (`cell.capabilities.can_host_model`, module docstring's
    own proxy) -- in both cases a shared fallback would be reached for only while connected, which
    is exactly the situation this plan cannot rely on. A throttled or currently-exhausted source
    (`abundance.seats_free == 0`) is not excluded outright -- it may still be the only candidate --
    but always sorts behind one that is not.
    """
    free_vram = sum(gpu.vram_free_bytes for gpu in cell.capacity.host.gpus)
    floor = grade_floor(_DEFAULT_TEMPO.accuracy)
    eligible = [
        s
        for s in candidates
        if _fits_vram(s, cell, free_vram) and s.spec.grade >= floor and _fits_cost(s, inputs)
    ]
    if not eligible:
        return []
    has_local = any(s.spec.host_cell_id == cell.id for s in eligible)
    force_local = inputs.seat_headroom <= 0 or cell.capabilities.can_host_model
    if force_local and has_local:
        eligible = [s for s in eligible if s.spec.host_cell_id == cell.id]
    eligible.sort(key=lambda s: _rank_key(s, cell))
    return eligible


def _fits_vram(source: ModelSource, cell: Cell, free_vram: int) -> bool:
    """Return whether `source` fits the Cell's own free VRAM, when it is local and needs any."""
    if source.spec.host_cell_id != cell.id or source.spec.vram_bytes_required is None:
        return True  # Not local, or declares no VRAM requirement: nothing to check here.
    return source.spec.vram_bytes_required <= free_vram


def _fits_cost(source: ModelSource, inputs: _PlanInputs) -> bool:
    """Return whether `source` fits the goal-level spend cap: free, or the cap allows spend."""
    if inputs.spend_cap_usd > 0:
        return True
    return source.spec.cost.cost_per_million_input_usd <= 0


def _rank_key(source: ModelSource, cell: Cell) -> tuple[int, int, float, float]:
    """Sort key: local first, available seats before none, closer latency, then cheaper."""
    is_remote = 0 if source.spec.host_cell_id == cell.id else 1
    starved = 0 if source.abundance.seats_free > 0 else 1
    latency = source.distance.latency_s if source.distance is not None else float("inf")
    return (is_remote, starved, latency, source.spec.cost.cost_per_million_input_usd)


def _fallback_source_id(sources: tuple[ModelSource, ...]) -> str:
    """Return any source's id as a last-resort default chain primary, or raise if there is none.

    `HostingPlan.default.primary` is a required field: a Hive with zero Forage map sources cannot
    write a valid plan at all, which is a manifest-validation gap, not a decision this pure
    function can paper over.
    """
    if not sources:
        raise ValueError("Cannot write a HostingPlan for a Hive with no Forage map sources.")
    return sources[0].source_id
