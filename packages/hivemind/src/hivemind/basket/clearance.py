"""Decide a blob's label at deposit and whether a Cell may receive it: pure rules, no I/O.

`HoneyClearance` (`hivemind.cell.tiers`) is the data-sensitivity label every tier carries: `C0`
public, `C1` internal, `C2` personal or sensitive. A blob's label is the higher of what its
depositor declared and a floor set by where it came from: anything produced on a borrowed (Real)
Cell is `C2`, because it describes the operator's own machine. A Cell then receives a blob only if
its Comb Shield tier's ceiling reaches that label, so nothing from a laptop ships to a Night Veil
Cell, which takes nothing above `C1`. The floor restates `hivemind.honey_store.clearance.
intake_floor` and the ceiling restates the Night Veil rule of `reader_ceiling`, because Layer 2
siblings never import each other; `tests/unit/honey_store/test_clearance.py` is the source of truth
for the Honey side and this module's test cites it clause by clause.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket`. Called by
    `hivemind.basket.deposits` (`blob_clearance`, `receiver_ceiling` for the Night Veil export
    rule) and `hivemind.basket.transfer.receiver` (`receipt_refusal`); `may_receive` is the
    boolean form for a Warden deciding whether to ask for a blob at all. Calls into
    `hivemind.cell` and `hivemind.basket.models` only.

Key invariants:
    - Every comparison is by `.rank`, never by member identity or wire value (codingrules 9).
    - `blob_clearance` never returns a label below the declared one or below `blob_floor`.
    - A tainted blob is refused to every Cell: taint is checked before clearance can matter.
    - The ceiling table is fixed data here, not read from the manifest: a blob's label is a
      property of the Hive's own security model, not a per-Hive setting.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the Basket's use of the rule.
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the labelling rule restated here.
    - tests/unit/honey_store/test_clearance.py for the rule's source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from hivemind.basket.errors import RefusalReason
from hivemind.basket.models import Blob, BlobState
from hivemind.cell import CombShieldLevel, HoneyClearance, OsFamily

__all__ = [
    "Refusal",
    "blob_clearance",
    "blob_floor",
    "may_receive",
    "receipt_refusal",
    "receiver_ceiling",
]

# What a Cell at each Comb Shield tier may hold. Night Veil is the only tier that is capped: its
# execution records never outlive teardown, so it must never be handed Royal (C2) data (ADR-0035).
# Read-only so no caller can widen the table at run time.
_TIER_CEILINGS = MappingProxyType(
    {
        CombShieldLevel.MEADOW: HoneyClearance.C2,
        CombShieldLevel.PROPOLIS: HoneyClearance.C2,
        CombShieldLevel.NIGHT_VEIL: HoneyClearance.C1,
    }
)


@dataclass(frozen=True, slots=True)
class Refusal:
    """One by-rule reason a Cell may not receive a blob: a closed code and a sentence."""

    code: RefusalReason  # Which rule: "tainted", "clearance", "platform" or "retired".
    reason: str  # A full sentence naming the blob by hash; never its bytes.


def blob_floor(from_borrowed_cell: bool, tier: CombShieldLevel) -> HoneyClearance:
    """Compute the label a blob may never fall below, from where it was produced.

    Args:
        from_borrowed_cell: Whether the bytes were produced on a Real (borrowed) Cell.
        tier: The Comb Shield tier of the origin Cell. It does not move the floor: a Night Veil
            Cell is always Virtual, so the borrowed case never meets it, and the Night Veil rule
            is a ceiling on export (`receiver_ceiling`), not a floor. It is a parameter so the
            call site reads the same as `blob_clearance`.

    Returns:
        `C2` for anything produced on a borrowed Cell, `C0` otherwise: exactly
        `honey_store.clearance.intake_floor` for a non-human, non-watch origin.
    """
    # ADR-0035: the floor follows where the material came from. A borrowed Cell's output is the
    # operator's own machine, whatever route it took to the Basket.
    del tier
    return HoneyClearance.C2 if from_borrowed_cell else HoneyClearance.C0


def blob_clearance(
    declared: HoneyClearance | None, from_borrowed_cell: bool, tier: CombShieldLevel
) -> HoneyClearance:
    """Compute a blob's stored label: the declared label raised to the provenance floor.

    Args:
        declared: The depositor's own label, or None when it declared none.
        from_borrowed_cell: Whether the bytes were produced on a Real (borrowed) Cell.
        tier: The origin Cell's Comb Shield tier.

    Returns:
        The higher of `declared` (C0 when None) and `blob_floor`, by rank.
    """
    floor = blob_floor(from_borrowed_cell, tier)
    base = declared if declared is not None else HoneyClearance.C0
    # The label only ever moves up from what the depositor said (honey_store.clearance.raise_label).
    return base if base.rank > floor.rank else floor


def receiver_ceiling(tier: CombShieldLevel) -> HoneyClearance:
    """Return the highest label a Cell at `tier` may hold, and a Cell at that tier may export.

    Args:
        tier: The Comb Shield tier of the Cell.

    Returns:
        `C1` for Night Veil, `C2` for Meadow and Propolis. The same table bounds what a Night Veil
        Cell may deposit (ADR-0035's Night Veil export rule), so one function serves both ways.
    """
    return _TIER_CEILINGS[tier]


def receipt_refusal(
    blob: Blob, ceiling: HoneyClearance, resident_platform: OsFamily
) -> Refusal | None:
    """Find the first by-rule reason a Cell may not receive `blob`, or None when it may.

    Args:
        blob: The blob as described by a verified manifest.
        ceiling: The highest label the receiving Cell may hold (`receiver_ceiling` of its tier).
        resident_platform: The receiving Cell's operating system family.

    Returns:
        The first failing rule, in the order retired, tainted, clearance, platform; None if the
        Cell may receive it. Room on disk is a residency question, answered elsewhere.
    """
    # A retired blob is withdrawn; shipping it would resurrect something the Hive pulled.
    if blob.state is BlobState.RETIRED:
        return Refusal("retired", f"blob {blob.sha256} is retired.")
    # A tainted blob came from an isolated or quarantined source and is never shipped or loaded.
    if blob.tainted:
        return Refusal("tainted", f"blob {blob.sha256} is tainted and is never shipped.")
    if blob.clearance.rank > ceiling.rank:
        return Refusal(
            "clearance",
            f"blob {blob.sha256} is {blob.clearance.value}, above the receiving Cell's "
            f"{ceiling.value} ceiling.",
        )
    # Empty tags mean "any platform"; otherwise the Cell's own OS must be named.
    if blob.platform_tags and resident_platform not in blob.platform_tags:
        return Refusal(
            "platform",
            f"blob {blob.sha256} supports {[tag.value for tag in blob.platform_tags]}, "
            f"not {resident_platform.value}.",
        )
    return None


def may_receive(blob: Blob, tier: CombShieldLevel, resident_platform: OsFamily) -> tuple[bool, str]:
    """Say whether a Cell at `tier` on `resident_platform` may receive `blob`.

    Args:
        blob: The blob as described by a verified manifest.
        tier: The receiving Cell's Comb Shield tier.
        resident_platform: The receiving Cell's operating system family.

    Returns:
        `(True, "")` when it may; `(False, sentence)` naming the first rule that forbids it.
    """
    refusal = receipt_refusal(blob, receiver_ceiling(tier), resident_platform)
    return (True, "") if refusal is None else (False, refusal.reason)
