"""Assign and change a Honey Store label, deterministically, from provenance and policy alone.

`HoneyClearance` (`hivemind.cell.tiers`) is the data-sensitivity label every memory tier carries,
not only Honey: `C0` (public), `C1` (internal) or `C2` (personal or sensitive). This module is the
whole of ADR-0031's labelling rule, pure and with no I/O: `intake_floor`/`intake_label` decide what
a fresh deposit is labelled; `raise_label` is the one direction ripening may move a label on its
own; `reader_ceiling` decides the highest label a request may actually see; `check_lowering` is
the one check standing between a lowering and `LabelLoweringError`, so the store (which trusts its
caller ran this first) never has to re-derive the rule itself. `held_by_floor_alone` is ADR-0034's
half of that rule that needs no model: whether only the Real Cell floor holds a label up.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    by `hivemind.honey_store.nectar` (intake, for `intake_label`), by `hivemind.honey_store.
    ripening` (`raise_label`, and `held_by_floor_alone` to decide a short deposit is still worth a
    model reading), by whatever assembles a query's clearance ceiling (`reader_ceiling`), by
    `hivemind.honey_store.lowering` (`held_by_floor_alone`, `HUMAN_ONLY_ORIGINS`) and by
    `hive honey relabel` and the judge-reviewed lowering (`check_lowering`). It lives here, not in
    `lowering`, because `lowering` imports `ripening`. Calls into `hivemind.cell` (HoneyClearance,
    CombShieldLevel), `hivemind.honey_store.errors` (LabelLoweringError), `hivemind.honey_store.
    models.nectar` (Nectar, NectarOrigin) and `hivemind.manifest.schema.security` (ClearanceMatrix,
    for the wire-form matrix a Hive Manifest carries) only.

Key invariants:
    - Every comparison here is by `.rank`, never by member identity or wire value (codingrules
      section 9: "totally ordered... compare rank, never the values").
    - `raise_label` never returns a clearance below either input; `intake_label` never returns a
      clearance below `intake_floor`'s own result.
    - `check_lowering` raises unless `target.rank < current.rank` AND an approver was given; the
      store trusts the caller ran it and never re-derives the rule (ADR-0031).
    - `held_by_floor_alone` is False whenever any labelling fact it reads is unknown (a row from
      before ADR-0034), so such a row is never proposed for lowering.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the labelling rule this implements.
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the lowering rule.
    - .claude/codingrules.md section 8.9 for "a model may raise a label; only a judge verdict or a
      human may lower one."
    - hivemind.cell.tiers for HoneyClearance/CombShieldLevel and their own from_wire/to_wire.
    - hivemind.manifest.schema.security for ClearanceMatrix, the `[honey.clearance.matrix]` shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.errors import LabelLoweringError
from hivemind.honey_store.models.nectar import Nectar, NectarOrigin
from hivemind.manifest.schema.security import ClearanceMatrix
from waggle.messages import CombShieldLevel as WireCombShieldLevel

__all__ = [
    "HUMAN_ONLY_ORIGINS",
    "LabelApprover",
    "check_lowering",
    "held_by_floor_alone",
    "intake_floor",
    "intake_label",
    "raise_label",
    "reader_ceiling",
]

# A tier with no row in the manifest's matrix still needs a ceiling (roadmap 7.2 D2 brief): the
# same default the manifest itself ships (hivemind.manifest.schema.security._DEFAULT_CLEARANCE_
# MATRIX), restated here so reader_ceiling never depends on that module's private default table.
_DEFAULT_CEILING_WITHOUT_ROW: dict[CombShieldLevel, HoneyClearance] = {
    CombShieldLevel.MEADOW: HoneyClearance.C2,
    CombShieldLevel.PROPOLIS: HoneyClearance.C2,
    CombShieldLevel.NIGHT_VEIL: HoneyClearance.C1,
}

# ADR-0034: their C2 is the content's own nature -- a person's words, or observations of the
# operator's machine -- so only the human ever lowers them.
HUMAN_ONLY_ORIGINS = frozenset({NectarOrigin.HUMAN, NectarOrigin.WATCH})


class LabelApprover(Enum):
    """Who may lower a Honey Store label; codingrules 8.9's "a judge verdict or a human"."""

    JUDGE = "JUDGE"  # An independent ModelSlot.JUDGE review, its own rubric, no shared context.
    HUMAN = "HUMAN"  # The operator's own command.


def intake_floor(origin: NectarOrigin, from_borrowed_cell: bool) -> HoneyClearance:
    """Compute the clearance floor a fresh deposit may never fall below (ADR-0031).

    Args:
        origin: How the deposit reached the store.
        from_borrowed_cell: Whether it was gathered on a Real (borrowed) Cell (`Cell.is_borrowed`).

    Returns:
        `C2` for HUMAN and WATCH origins and for anything gathered on a borrowed Cell, whatever
        route it took (a bee's deposit, a task's outcome, aged Bee Bread, cleared Cell Wax);
        `C0` otherwise.
    """
    # ADR-0031: the floor follows where the material came from, not how it reached the store; a
    # Real Cell's Bee Bread or Cell Wax is as much the operator's own machine as a bee's deposit.
    if from_borrowed_cell or origin in (NectarOrigin.HUMAN, NectarOrigin.WATCH):
        return HoneyClearance.C2
    return HoneyClearance.C0


def intake_label(
    declared: HoneyClearance | None,
    origin: NectarOrigin,
    from_borrowed_cell: bool,
    default_label: HoneyClearance,
) -> HoneyClearance:
    """Compute a fresh deposit's stored clearance: the declared label raised to the intake floor.

    Args:
        declared: The depositor's own declared label, or None when it declared none.
        origin: How the deposit reached the store.
        from_borrowed_cell: Whether it was gathered on a Real (borrowed) Cell.
        default_label: What to use in place of `declared` when it is None
            (`[honey.clearance] default_label`).

    Returns:
        The higher of (`declared`, or `default_label` when `declared` is None) and
        `intake_floor(origin, from_borrowed_cell)`.
    """
    base = declared if declared is not None else default_label
    return raise_label(base, intake_floor(origin, from_borrowed_cell))


def held_by_floor_alone(nectar: Nectar) -> bool:
    """Say whether only the Real Cell floor holds `nectar`'s label up, so a judge may lower it.

    The half of ADR-0034's eligibility that needs no model reading, whatever the Nectar's state:
    `hivemind.honey_store.lowering.lowering_target` adds the rest (ripened, and a Ripener reading
    below the label). Ripening asks it first, so a deposit too short to summarise is still read
    by the model when that reading is the only way its label could ever come down.

    Args:
        nectar: The stored Nectar, as the store reads it now.

    Returns:
        True when it is untainted, not from a Night Veil Cell, not of HUMAN or WATCH origin, its
        declared and floor labels are both known, the floor reaches its label and its declared
        label does not; False otherwise.
    """
    # A tainted row is suspect, a Night Veil export stays exactly as it crossed, and a person's
    # words or the operator's own machine are C2 by nature: none of them is the floor's doing.
    if nectar.tainted or nectar.origin_tier is CombShieldLevel.NIGHT_VEIL:
        return False
    if nectar.origin in HUMAN_ONLY_ORIGINS:
        return False
    declared, floor = nectar.declared_clearance, nectar.floor_clearance
    # A row from before ADR-0034 has facts nobody knows: never lower it.
    if declared is None or floor is None:
        return False
    # The floor reaches the label and no depositor did.
    return floor.rank >= nectar.clearance.rank > declared.rank


def raise_label(current: HoneyClearance, proposed: HoneyClearance) -> HoneyClearance:
    """Return the higher of two clearances; the only direction ripening may move one on its own.

    Args:
        current: The item's current clearance.
        proposed: A candidate clearance (a floor, a ripener's own raise, a dedupe merge).

    Returns:
        Whichever of `current`/`proposed` ranks higher; `current` when they rank equal.
    """
    return proposed if proposed.rank > current.rank else current


def reader_ceiling(
    requested: HoneyClearance,
    principal: HoneyClearance,
    tier: CombShieldLevel,
    matrix: Mapping[WireCombShieldLevel, ClearanceMatrix],
) -> HoneyClearance:
    """Compute the highest clearance a request may actually see (ADR-0031).

    Args:
        requested: The highest label the caller asked for (`HoneyQuery.max_clearance`).
        principal: The asking bee's own task/goal clearance ceiling.
        tier: The Comb Shield tier of the Cell the request runs on.
        matrix: `[honey.clearance.matrix]`, wire-form keyed (a Hive Manifest's own shape); convert
            `tier` with `.to_wire()` before looking it up.

    Returns:
        The lowest of `requested`, `principal` and the tier's own ceiling: the highest label in
        `matrix[tier].read`, or the ADR-0031 default (`C1` for NIGHT_VEIL, `C2` otherwise) when
        `matrix` carries no row for `tier`.
    """
    row = matrix.get(tier.to_wire())
    tier_ceiling = _tier_ceiling(tier, row)
    return min((requested, principal, tier_ceiling), key=lambda clearance: clearance.rank)


def check_lowering(
    current: HoneyClearance, target: HoneyClearance, approver: LabelApprover | None
) -> None:
    """Raise unless `target` is a genuine lowering with a named approver (codingrules 8.9).

    The store trusts the caller ran this first (`HoneyStore.lower_clearance`'s own contract): it
    never re-derives the rule, only persists the already-checked result.

    Args:
        current: The item's current clearance.
        target: The proposed, lower clearance.
        approver: Who approved the lowering, or None when nobody did.

    Raises:
        LabelLoweringError: `target` does not rank strictly below `current`, or `approver` is None.
    """
    if target.rank >= current.rank or approver is None:
        raise LabelLoweringError(
            current.name, target.name, approver.name if approver is not None else None
        )


def _tier_ceiling(tier: CombShieldLevel, row: ClearanceMatrix | None) -> HoneyClearance:
    """Return a tier's own read ceiling: the highest label in its row, or ADR-0031's default."""
    if row is None or not row.read:
        # No manifest row for this tier, or an explicitly empty one: ADR-0031's stated default.
        return _DEFAULT_CEILING_WITHOUT_ROW[tier]
    readable = (HoneyClearance.from_wire(wire) for wire in row.read)
    return max(readable, key=lambda clearance: clearance.rank)
