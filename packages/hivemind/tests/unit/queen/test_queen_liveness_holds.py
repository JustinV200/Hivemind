"""Tests for the Queen's liveness: a Cell the Hive itself paused is never judged unreachable.

Overwintering (ADR-0029) `docker pause`s a Virtual Cell, or stops its VM, so its Warden cannot
beat; a real Docker run showed a Cell that heartbeated just before its pause drawing
`CELL_UNREACHABLE` about 40 s into it, for silence the Hive had caused itself. The Queen now
reads the Cells she holds paused from the lifecycle's own dormant list, never judges their
Wardens while the hold lasts, and gives a resumed one a whole window from the moment it
resumed. A Warden that falls silent for any other reason is judged exactly as before. Every test
runs on a FakeClock (builders' defaults: a 5 s manifest interval and a miss limit of 3, so a 15 s
window).

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/liveness.py (codingrules section 3); split by feature (14.2)
    from test_queen_liveness_cadence.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.liveness for check_liveness and the hold it keeps per Warden.
    - hivemind.hive.lifecycle for the dormant list the Queen reads the holds from.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

import pytest
from builders.cells import make_cell
from builders.queen import make_queen_deps

from hivemind.cell import CombShieldLevel
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.inbox import Pulse
from hivemind.queen.placement import DormantCandidate
from hivemind.queen.ticks.liveness import WardenLiveness, check_liveness, record_heartbeat
from hivemind.supervision import AlarmKind
from waggle.clock import FakeClock
from waggle.ids import CellId, WardenId, new_warden_id

_WINDOW_S = 15.0  # The builders' 5 s manifest interval times their miss limit of 3.
_PAUSE_S = 600.0  # Ten minutes Overwintered: forty windows of silence.


def _dormant(cell_id: CellId) -> DormantCandidate:
    """The lifecycle's own entry for an Overwintered Cell."""
    return DormantCandidate(
        cell_id=cell_id, warden_id=None, image="hivecell", comb_shield=CombShieldLevel.MEADOW
    )


def _static(deps: QueenDeps, *held: CellId) -> QueenDeps:
    """`deps` whose static dormant list holds `held`."""
    return dataclasses.replace(deps, dormant_cells=tuple(_dormant(cell) for cell in held))


def _live(deps: QueenDeps, *held: CellId) -> QueenDeps:
    """`deps` whose live dormant feed (the lifecycle's own, in production) holds `held`."""

    async def source() -> tuple[DormantCandidate, ...]:
        return tuple(_dormant(cell) for cell in held)

    return dataclasses.replace(deps, dormant_cell_source=source)


def _second_link(deps: QueenDeps, first: WardenLink) -> WardenLink:
    """Another Warden on a Cell of its own, over the first link's transport (never used here)."""
    warden_id = new_warden_id(deps.clock)
    return dataclasses.replace(first, warden_id=warden_id, cell=make_cell(clock=deps.clock))


def _unreachable(inbox: HumanInbox) -> list[WardenId]:
    """Every CELL_UNREACHABLE Alarm escalated to the human, by origin."""
    return [WardenId(a.origin) for a in inbox.alarms if a.kind is AlarmKind.CELL_UNREACHABLE]


@pytest.mark.parametrize("holding", [_static, _live])
async def test_a_paused_cells_warden_is_never_judged_while_a_silent_one_still_is(
    holding: Callable[..., QueenDeps],
) -> None:
    clock = FakeClock()
    base, link, _end = make_queen_deps(clock)
    other = _second_link(base, link)
    deps = holding(base, link.cell.id)  # Only the first Warden's Cell is Overwintered.
    liveness: dict[WardenId, WardenLiveness] = {}
    inbox = HumanInbox()
    # Both beat once, just before the pause, and then fall silent.
    for warden in (link, other):
        record_heartbeat(deps, liveness, warden.warden_id, Pulse(clock.now(), 5.0))

    clock.advance(_PAUSE_S)
    await check_liveness(deps, (link, other), liveness, inbox)
    await check_liveness(deps, (link, other), liveness, inbox)

    assert _unreachable(inbox) == [other.warden_id]  # The Hive's own pause is no outage.
    held = liveness[link.warden_id]
    assert (held.held, held.is_offline, held.missed_heartbeats) == (True, False, 0)
    assert liveness[other.warden_id].is_offline


async def test_a_resumed_cells_warden_gets_a_whole_window_from_its_resume() -> None:
    clock = FakeClock()
    base, link, _end = make_queen_deps(clock)
    liveness: dict[WardenId, WardenLiveness] = {}
    inbox = HumanInbox()
    record_heartbeat(base, liveness, link.warden_id, Pulse(clock.now(), 5.0))
    clock.advance(_PAUSE_S)
    await check_liveness(_static(base, link.cell.id), (link,), liveness, inbox)

    # Resumed: the lifecycle no longer lists it, and its last beat is ten minutes old.
    await check_liveness(base, (link,), liveness, inbox)
    clock.advance(_WINDOW_S - 1.0)
    await check_liveness(base, (link,), liveness, inbox)
    assert _unreachable(inbox) == []
    assert not liveness[link.warden_id].held

    # Still silent a whole window after its resume: now it is unreachable, and said so once.
    clock.advance(1.0)
    await check_liveness(base, (link,), liveness, inbox)
    await check_liveness(base, (link,), liveness, inbox)
    assert _unreachable(inbox) == [link.warden_id]


async def test_a_heartbeat_while_held_keeps_the_hold() -> None:
    # A backend whose pause does not stop the Cell (the fake one) lets its Warden go on beating.
    clock = FakeClock()
    base, link, _end = make_queen_deps(clock)
    deps = _static(base, link.cell.id)
    liveness: dict[WardenId, WardenLiveness] = {}
    inbox = HumanInbox()
    record_heartbeat(deps, liveness, link.warden_id, Pulse(clock.now(), 5.0))
    await check_liveness(deps, (link,), liveness, inbox)

    clock.advance(_WINDOW_S)
    record_heartbeat(deps, liveness, link.warden_id, Pulse(clock.now(), 5.0))
    clock.advance(_PAUSE_S)
    await check_liveness(deps, (link,), liveness, inbox)

    assert liveness[link.warden_id].held
    assert _unreachable(inbox) == []
