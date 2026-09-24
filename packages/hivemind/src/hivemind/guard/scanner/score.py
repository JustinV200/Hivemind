"""Score a text against the untrusted-content patterns and map the score to a verdict: pure.

The scanner's decision (roadmap step 10.6b) is two pure steps kept apart from anything effectful:
`score_text` bounds the text to the scanner's input limit first (a hostile document never makes
matching the slow path), normalises it, asks every family's detector how often it fired and adds
up `weight x hits`; `decide` maps that total to PASS, LABEL or DROP against one Comb Shield tier's
thresholds (`[guard.untrusted_content]`), and `thresholds_for` picks those thresholds for a tier.
Keeping this pure means the invariant tests and the chaos seeds (roadmap 13.6) can exercise the
exact decision the scanner makes, without a trail, a key or a clock.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.scanner`. Called
    by `hivemind.guard.scanner.scanner.ContentScanner.scan`. Calls into `hivemind.cell`
    (CombShieldLevel), `hivemind.guard.capabilities` (CapabilitySet), `hivemind.manifest.schema.
    guard` (the thresholds' shape) and this package's `detectors`, `patterns` and `verdict`.

Key invariants:
    - Deterministic: the same text, patterns, targets and bound always give the same score.
    - Only the first `max_chars` characters are ever matched; `ScanScore.truncated` says so.
    - A score is never negative, and a family never adds more than `weight x max_hits`.

See Also:
    - hivemind.guard.scanner.detectors for how each family counts its hits.
    - hivemind.manifest.schema.guard for UntrustedContentSection and ScanThresholds.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import CombShieldLevel
from hivemind.guard.capabilities import CapabilitySet
from hivemind.guard.scanner.detectors import normalise
from hivemind.guard.scanner.patterns import CompiledPatterns
from hivemind.guard.scanner.verdict import ScanAction
from hivemind.manifest.schema.guard import ScanThresholds, UntrustedContentSection

__all__ = ["FamilyHit", "ScanScore", "decide", "score_text", "thresholds_for"]


@dataclass(frozen=True, slots=True)
class FamilyHit:
    """One family that fired on a text: how often, and what it added to the score."""

    family: str  # The family's table name.
    hits: int  # How often it fired, capped at its max_hits.
    points: float  # weight x hits.


@dataclass(frozen=True, slots=True)
class ScanScore:
    """A text's total score, every family that fired, and whether the text was cut short."""

    total: float  # The sum of every hit's points.
    hits: tuple[FamilyHit, ...]  # Only families that fired, in the pattern file's order.
    truncated: bool  # True when the text was longer than the bound and only its head was read.

    @property
    def families(self) -> tuple[str, ...]:
        """The names of the families that fired, sorted (what a verdict records)."""
        return tuple(sorted(hit.family for hit in self.hits))


def score_text(
    text: str, patterns: CompiledPatterns, targets: CapabilitySet | None, max_chars: int
) -> ScanScore:
    """Score `text` against every family, reading at most `max_chars` of it.

    Args:
        text: The outside text, whole; only its first `max_chars` characters are matched.
        patterns: The compiled pattern families (`load_scan_patterns`).
        targets: The reading bee's own capability set (its `net` scopes are its task's hosts);
            None when the text has no task to measure against, which silences the host family.
        max_chars: The scanner's input bound (`[guard.untrusted_content] max_scan_chars`).

    Returns:
        The total, the families that fired and whether the text was truncated.
    """
    # Bound first, normalise second: normalising a gigabyte just to throw most of it away would
    # make the scanner the slow path the bound exists to prevent.
    bounded = text[:max_chars]
    prepared = normalise(bounded)
    hits: list[FamilyHit] = []
    # Every family in file order; one that does not fire adds nothing and is not recorded.
    for family in patterns.families:
        count = family.detector.count(prepared, targets, family.max_hits)
        if count > 0:
            hits.append(FamilyHit(family=family.name, hits=count, points=family.weight * count))
    total = sum(hit.points for hit in hits)
    return ScanScore(total=total, hits=tuple(hits), truncated=len(text) > max_chars)


def decide(total: float, thresholds: ScanThresholds) -> ScanAction:
    """Map a score to PASS, LABEL or DROP against one tier's thresholds.

    Args:
        total: A text's score (`ScanScore.total`).
        thresholds: The tier's label and drop thresholds.

    Returns:
        DROP at or above `drop`, LABEL at or above `label`, PASS below both.
    """
    if total >= thresholds.drop:
        return ScanAction.DROP
    if total >= thresholds.label:
        return ScanAction.LABEL
    return ScanAction.PASS


def thresholds_for(section: UntrustedContentSection, tier: CombShieldLevel) -> ScanThresholds:
    """Return the thresholds `[guard.untrusted_content]` sets for `tier`.

    Args:
        section: The manifest's `[guard.untrusted_content]` table (or its defaults).
        tier: The Comb Shield tier of the Cell the reading bee runs on.

    Returns:
        That tier's label and drop thresholds.
    """
    # A table lookup, not a branch per tier: the manifest cannot name the enum (it sits below
    # hivemind.cell), so each tier's field is named here once.
    by_tier = {
        CombShieldLevel.MEADOW: section.meadow,
        CombShieldLevel.PROPOLIS: section.propolis,
        CombShieldLevel.NIGHT_VEIL: section.night_veil,
    }
    return by_tier[tier]
