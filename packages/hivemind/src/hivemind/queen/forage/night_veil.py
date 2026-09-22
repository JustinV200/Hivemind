"""Define night_veil_local_only and restrict_to_local: Night Veil's local-only hosting-plan checks.

Roadmap step 5.7a / ADR-0030: "every model slot in the hosting plan must resolve to local
providers only (no Hive Stand or hosted fallback)... A plan that cannot be made local-only fails
placement; it never spills." A `hivemind.forage.HostingPlan`'s own `SourceChain` names sources by
id only (its own module docstring: "the receiver resolves ids against its own Forage map"), so
telling a local source from a hosted one, or one hosted on the Hive Stand rather than on the Cell
the plan is for, needs the `hivemind.forage.ForageMap` alongside the plan: a source is local to a
Cell only if its `ModelSourceSpec.host_cell_id` equals that Cell's own id, never merely "some
Cell" (the Hive Stand is itself just another `host_cell_id` value, not a special case this module
has to name). Both functions are pure (codingrules section 8.3): `night_veil_local_only` only
reads, `restrict_to_local` only builds a new `HostingPlan` value, and neither performs I/O or reads
a store -- the caller (a report item, since it sits in `hivemind.queen.dispatcher`, outside this
dispatch's file list) is responsible for writing the restricted plan back to the ledger and the
trail exactly as `hivemind.queen.forage.hosting.write_hosting_plan` already does for an ordinary
plan.

A sibling of `hosting.py`, not an addition to it (codingrules section 5.2's "a module that outgrows
5.1 splits into a sibling"): `hosting.py` was already within reach of its own 200-line target, and
this concept -- restricting an already-written plan to Night Veil's own rule -- is distinct from
writing one in the first place.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.forage`
    sub-package. Read by `hivemind.queen.placement.policy.check_night_veil`'s own caller (which
    builds a `hivemind.queen.placement.policy.NightVeilHostingView` from
    `night_veil_local_only`'s result) and by whatever writes a Night Veil Cell's own `HostingPlan`
    once it is provisioned (a report item: the call site sits in `hivemind.queen.dispatcher`).
    Calls into `hivemind.forage` (HostingPlan, ModelSource, SlotPlan, SourceChain), `hivemind.
    forage.map` (ForageMap) and `waggle.ids` only.

Key invariants:
    - `night_veil_local_only` is pure: no I/O, no store, no clock; given the same `plan` and
      `forage_map` it always returns the same tuple.
    - A source id `forage_map` does not know about is treated as non-local (never crashes): a
      hosting plan and the map it was built from can drift apart between the write and this check
      running, and "unknown, so definitely not provably local" is the fail-closed reading ADR-0030
      demands ("a plan that cannot be made local-only fails placement; it never spills").
    - `restrict_to_local` never returns a `HostingPlan` with an empty `SourceChain.primary` for any
      slot: a slot that would be left with no local source is dropped from `plan.slots` entirely
      when `plan.default` remains local (so a caller has somewhere to fall back to for it), and
      `NightVeilPlanError` is raised only when `plan.default` itself cannot be made local -- there
      would then be nothing for *any* unnamed slot to use either.

See Also:
    - docs/adr/0030-night-veil-retention-and-clearance-boundary.md for the local-only rule this
      module implements.
    - .claude/roadmap.md step 5.7a for this module's own roadmap bullet.
    - hivemind.queen.forage.hosting for write_hosting_plan, the module that writes the plan this
      one restricts.
    - hivemind.forage.models.pools for HostingPlan, SlotPlan and SourceChain.
    - hivemind.forage.map for ForageMap, read here only through its synchronous `get`.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import ConflictError
from hivemind.forage import HostingPlan, SlotPlan, SourceChain
from hivemind.forage.errors import UnknownSourceError
from hivemind.forage.map import ForageMap
from waggle.ids import CellId

_DEFAULT_SLOT_NAME = "default"  # How the default chain names itself in a violation string.

__all__ = ["NightVeilPlanError", "night_veil_local_only", "restrict_to_local"]


class NightVeilPlanError(ConflictError):
    """Raise when a Night Veil HostingPlan cannot be made local-only at all.

    Only for the case `night_veil_local_only`/`restrict_to_local` cannot recover from by simply
    dropping a slot: `plan.default` itself has no local source left, so no slot the plan does not
    name explicitly would have anywhere to go (ADR-0030: "a plan that cannot be made local-only
    fails placement; it never spills").
    """

    code: ClassVar[str] = "hivemind.queen.forage.night_veil_plan_error"


def night_veil_local_only(plan: HostingPlan, forage_map: ForageMap) -> tuple[str, ...]:
    """Return one violation string per slot (default chain included) that is not local-only.

    Args:
        plan: The Cell's own HostingPlan to check; `plan.cell_id` is what "local" is measured
            against.
        forage_map: Resolves each chain's source ids to a `ModelSource`, for its own
            `spec.host_cell_id`.

    Returns:
        One human-readable string per violating slot (or `"default"` for `plan.default`), each
        naming a source in that slot's chain that is hosted (no `host_cell_id`) or hosted on a
        different Cell (the Hive Stand included, since it is simply another Cell here). Empty
        when every chain is entirely local.
    """
    violations: list[str] = []
    for slot_plan in plan.slots:
        reason = _non_local_reason(slot_plan.chain, plan.cell_id, forage_map)
        if reason is not None:
            violations.append(f"slot {slot_plan.slot.value}: {reason}")
    default_reason = _non_local_reason(plan.default, plan.cell_id, forage_map)
    if default_reason is not None:
        violations.append(f"slot {_DEFAULT_SLOT_NAME}: {default_reason}")
    return tuple(violations)


def restrict_to_local(plan: HostingPlan, forage_map: ForageMap, cell_id: CellId) -> HostingPlan:
    """Strip every non-local source from `plan`, dropping a slot left with none.

    Args:
        plan: The plan to restrict; unchanged, a new `HostingPlan` is returned.
        forage_map: Resolves each chain's source ids to a `ModelSource`, for its own
            `spec.host_cell_id`.
        cell_id: The Cell `plan` is for; must equal `plan.cell_id` (defensive: a caller building
            this from the wrong Cell's plan is a bug worth failing loudly on).

    Returns:
        A new `HostingPlan`, revision and reason preserved apart from a short appended note, with
        every chain restricted to local-only sources and any slot left with none removed.

    Raises:
        NightVeilPlanError: `cell_id` does not match `plan.cell_id`, or `plan.default` itself has
            no local source left (nothing left for an unnamed slot to fall back to either).
    """
    if cell_id != plan.cell_id:
        raise NightVeilPlanError(
            f"restrict_to_local called with cell_id={cell_id!r} but plan.cell_id="
            f"{plan.cell_id!r}; these must match."
        )
    default_local = _local_only_chain(plan.default, cell_id, forage_map)
    if default_local is None:
        raise NightVeilPlanError(
            f"NIGHT_VEIL Cell {cell_id}'s default hosting chain has no local source left; "
            "the plan cannot be made local-only."
        )
    restricted_slots = tuple(
        SlotPlan(slot=slot_plan.slot, chain=local_chain)
        for slot_plan in plan.slots
        if (local_chain := _local_only_chain(slot_plan.chain, cell_id, forage_map)) is not None
    )
    return plan.model_copy(
        update={
            "slots": restricted_slots,
            "default": default_local,
            "reason": f"{plan.reason} Restricted to local-only sources for NIGHT_VEIL."[:2000],
        }
    )


def _non_local_reason(chain: SourceChain, cell_id: CellId, forage_map: ForageMap) -> str | None:
    """Return why `chain` is not entirely local to `cell_id`, or None once every source is."""
    for source_id in (chain.primary, *chain.fallbacks):
        if not _is_local(source_id, cell_id, forage_map):
            return f"source {source_id!r} is not local to Cell {cell_id}"
    return None


def _local_only_chain(
    chain: SourceChain, cell_id: CellId, forage_map: ForageMap
) -> SourceChain | None:
    """Return `chain` restricted to local-only source ids, or None if that leaves no primary."""
    local_ids = [
        source_id
        for source_id in (chain.primary, *chain.fallbacks)
        if _is_local(source_id, cell_id, forage_map)
    ]
    if not local_ids:
        return None  # Nothing local in this chain at all: the caller drops the slot.
    return SourceChain(primary=local_ids[0], fallbacks=tuple(local_ids[1:]))


def _is_local(source_id: str, cell_id: CellId, forage_map: ForageMap) -> bool:
    """Return whether `source_id` is local to `cell_id` (fail-closed on an unknown id)."""
    try:
        source = forage_map.get(source_id)
    except UnknownSourceError:
        # A hosting plan and the map it was built from can drift apart between the write and this
        # check running; "unknown, so not provably local" is ADR-0030's own fail-closed reading.
        return False
    return source.spec.host_cell_id == cell_id
