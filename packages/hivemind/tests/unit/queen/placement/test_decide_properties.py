"""Property tests for hivemind.queen.placement.decide: determinism, never a BLOCKed candidate.

Fits into the Hive:
    Mirrors src/hivemind/queen/placement/decide.py (codingrules section 3), the hypothesis half
    of test_decide.py's example-based rule tests (roadmap step 5.7's own test list).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.placement.decide for the module under test.
    - .claude/codingrules.md section 14.3 for "property-based tests (hypothesis) for codecs...".
"""

from __future__ import annotations

from builders.cells import make_capabilities
from builders.forage import make_footprint
from hypothesis import given
from hypothesis import strategies as st

from hivemind.cell import CombShieldLevel, TaskNeeds
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
                capabilities=make_capabilities(),
                comb_shield=CombShieldLevel.MEADOW,
                is_hive_stand=draw(st.booleans()),
                has_free_capacity=draw(st.booleans()),
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
    data=st.data(),
)
def test_decide_never_returns_a_blocked_or_non_fitting_real_candidate(
    candidates: tuple[RealCandidate, ...], allow_hive_stand: bool, data: st.DataObject
) -> None:
    blocked_ids = data.draw(_blocked_subset(candidates))
    blocked = {
        cell_id: WaxMention(id="wax_01ABCDEFGHJKMNPQRSTVWXYZ00", text="x")
        for cell_id in blocked_ids
    }
    inventory = Inventory(real=candidates, blocked=blocked)
    policy = PlacementPolicy(prefer="real", allow_hive_stand=allow_hive_stand)
    forage = ForageView(footprint=make_footprint())

    try:
        placement = decide(TaskNeeds(), inventory, forage, policy)
    except PlacementError:
        return  # Nothing fit; nothing further to check.

    # A ReuseReal Placement is the only possible outcome here (no Virtual backend exists at all).
    assert isinstance(placement, ReuseReal)
    assert placement.cell_id not in blocked_ids
    chosen = next(c for c in candidates if c.cell_id == placement.cell_id)
    assert chosen.has_free_capacity
    assert allow_hive_stand or not chosen.is_hive_stand


@given(candidates=_real_candidates(), data=st.data())
def test_decide_is_deterministic(
    candidates: tuple[RealCandidate, ...], data: st.DataObject
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
            placement = decide(TaskNeeds(), inventory, forage, policy)
        except PlacementError:
            return None
        assert isinstance(placement, ReuseReal)
        return placement

    first, second = _attempt(), _attempt()
    assert first == second
