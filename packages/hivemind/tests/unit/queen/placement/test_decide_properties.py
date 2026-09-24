"""Property tests for hivemind.queen.placement.decide: determinism, never a BLOCKed candidate.

Roadmap step 6.12 widens the fit property to the Exoskeleton (a Cell's optional display, input,
audio and browser attachment): over random capability reports, access levels and need shapes,
`decide` never lends a Real Cell attach could not equip, and never refuses one it could. The
oracle, `_attach_could_equip`, restates ADR-0031's attach rules on its own against the Cell's
access-level ceiling, so it checks `rules.exoskeleton_shortfall` rather than reusing it.

Fits into the Hive:
    Mirrors src/hivemind/queen/placement/decide.py (codingrules section 3), the hypothesis half
    of test_decide.py's example-based rule tests (roadmap step 5.7's own test list).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.placement.decide for the module under test.
    - .claude/codingrules.md section 14.3 for "property-based tests (hypothesis) for codecs...".
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md, "Attach is a pure plan", for
      the rules the oracle restates.
"""

from __future__ import annotations

from pathlib import Path

from builders.cells import make_capabilities
from builders.forage import make_footprint
from hypothesis import given
from hypothesis import strategies as st

from hivemind.cell import AccessLevel, CellCapabilities, CombShieldLevel, TaskNeeds
from hivemind.guard import Capability, ceiling_for
from hivemind.queen.placement import (
    ForageView,
    Inventory,
    PlacementError,
    PlacementPolicy,
    RealCandidate,
    ReuseReal,
    WaxMention,
    decide,
)
from waggle.ids import CellId, WardenId

_MAX_CANDIDATES = 5  # Generous enough to exercise ranking and elimination without slow examples.
# Every valid shape of TaskNeeds' Exoskeleton fields (audio never rides with browser_only).
_NEED_SHAPES = (
    TaskNeeds(),
    TaskNeeds(exoskeleton=True),
    TaskNeeds(exoskeleton=True, audio=True),
    TaskNeeds(exoskeleton=True, browser_only=True),
)


@st.composite
def _capabilities(draw: st.DrawFn) -> CellCapabilities:
    """Draw a Linux capability report with every Exoskeleton flag chosen independently."""
    return make_capabilities(
        has_display=draw(st.booleans()),
        has_audio=draw(st.booleans()),
        has_browser=draw(st.booleans()),
        can_start_display=draw(st.booleans()),
        real_display_allowed=draw(st.booleans()),
    )


@st.composite
def _real_candidates(draw: st.DrawFn) -> tuple[RealCandidate, ...]:
    """Draw a small, distinctly-ided list of RealCandidates with random fit/capacity/block flags."""
    count = draw(st.integers(min_value=0, max_value=_MAX_CANDIDATES))
    candidates = []
    for index in range(count):
        candidates.append(
            RealCandidate(
                warden_id=WardenId(f"warden_{index:03d}"),
                cell_id=CellId(f"cell_{index:03d}"),
                capabilities=draw(_capabilities()),
                comb_shield=CombShieldLevel.MEADOW,
                is_hive_stand=draw(st.booleans()),
                has_free_capacity=draw(st.booleans()),
                access_level=draw(st.sampled_from(AccessLevel)),
            )
        )
    return tuple(candidates)


@st.composite
def _blocked_subset(draw: st.DrawFn, candidates: tuple[RealCandidate, ...]) -> frozenset[CellId]:
    """Draw a random subset of `candidates`' own ids to mark BLOCKed."""
    ids = [candidate.cell_id for candidate in candidates]
    chosen = draw(st.lists(st.sampled_from(ids), unique=True) if ids else st.just([]))
    return frozenset(chosen)


@given(
    candidates=_real_candidates(),
    allow_hive_stand=st.booleans(),
    needs=st.sampled_from(_NEED_SHAPES),
    data=st.data(),
)
def test_decide_never_returns_a_blocked_or_non_fitting_real_candidate(
    candidates: tuple[RealCandidate, ...],
    allow_hive_stand: bool,
    needs: TaskNeeds,
    data: st.DataObject,
) -> None:
    blocked_ids = data.draw(_blocked_subset(candidates))
    blocked = {
        cell_id: WaxMention(id="wax_01ABCDEFGHJKMNPQRSTVWXYZ00", text="x")
        for cell_id in blocked_ids
    }
    inventory = Inventory(real=candidates, blocked=blocked)
    policy = PlacementPolicy(prefer="real", allow_hive_stand=allow_hive_stand)
    forage = ForageView(footprint=make_footprint())
    eligible = [
        candidate
        for candidate in candidates
        if candidate.cell_id not in blocked_ids
        and candidate.has_free_capacity
        and (allow_hive_stand or not candidate.is_hive_stand)
        and _attach_could_equip(needs, candidate)
    ]

    try:
        placement = decide(needs, inventory, forage, policy)
    except PlacementError:
        # Refusing is right only when no candidate clears every rule: rule 4c is never too strict.
        assert not eligible
        return

    # A ReuseReal Placement is the only possible outcome here (no Virtual backend exists at all),
    # and it is always the first eligible Cell in attachment order (no Cell Wax cautions here).
    assert isinstance(placement, ReuseReal)
    assert placement.cell_id == eligible[0].cell_id


@given(candidates=_real_candidates(), needs=st.sampled_from(_NEED_SHAPES), data=st.data())
def test_decide_is_deterministic(
    candidates: tuple[RealCandidate, ...], needs: TaskNeeds, data: st.DataObject
) -> None:
    blocked_ids = data.draw(_blocked_subset(candidates))
    blocked = {
        cell_id: WaxMention(id="wax_01ABCDEFGHJKMNPQRSTVWXYZ00", text="x")
        for cell_id in blocked_ids
    }
    inventory = Inventory(real=candidates, blocked=blocked)
    policy = PlacementPolicy()
    forage = ForageView(footprint=make_footprint())

    def _attempt() -> ReuseReal | None:
        try:
            placement = decide(needs, inventory, forage, policy)
        except PlacementError:
            return None
        assert isinstance(placement, ReuseReal)
        return placement

    first, second = _attempt(), _attempt()
    assert first == second


def _attach_could_equip(needs: TaskNeeds, candidate: RealCandidate) -> bool:
    """Restate ADR-0031's attach rules against the widest grant the Cell's own Warden could hold."""
    if not needs.exoskeleton:
        return True
    capabilities = candidate.capabilities
    ceiling = ceiling_for(
        candidate.access_level,
        Path("/scratch"),
        real_display=capabilities.real_display_allowed,
    )

    def granted(scope: str) -> bool:
        return ceiling.allows(Capability.parse(f"exoskeleton:{scope}"))

    if needs.browser_only:
        return capabilities.has_browser and granted("browser")
    lends_its_screen = (
        capabilities.has_display and capabilities.real_display_allowed and granted("real_display")
    )
    starts_a_display = capabilities.can_start_display and granted("display")
    hears = capabilities.has_audio and granted("audio")
    return (lends_its_screen or starts_a_display) and (hears or not needs.audio)
