"""Tests for hivemind.queen.forage.deliveries: a Warden's ceilings and plan, sent until taken.

The defect these pin down: the Queen sent a Warden its first Ceilings and its Cell's HostingPlan
once, and a send that found the link closed was never tried again (phase-7 handoff, section 8
item 5), so the Warden ran with neither. Every scenario holds the Queen's end of the link down
for the first send, the way a dropped WebSocket refuses frames, and checks that the next dispatch
pass delivers what is in force (a re-attach's resend is `test_resend.py`'s).

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/deliveries.py (codingrules section 3), with its caller, the
    dispatch pass.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.deliveries for the module under test.
    - tests/unit/queen/forage/test_ceilings.py for the first send itself.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator

from builders.queen import make_queen_deps

from hivemind.forage.models.pools import Ceilings
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.forage import redeliver_owed, set_ceilings
from hivemind.queen.forage.hosting import write_hosting_plan
from waggle.envelope import Envelope
from waggle.errors import ConnectionLostError
from waggle.transport.base import Transport


class _DroppableEnd:
    """The Queen's end of a Warden link that refuses every send while `down`, like a lost socket.

    Owns its own mutable state in place (codingrules section 8.5): the test flips `down`.
    """

    def __init__(self, inner: Transport) -> None:
        """Wrap `inner`, the Queen's real end of the link; starts down."""
        self._inner = inner
        self.down = True

    @property
    def is_connected(self) -> bool:
        """Whether the link would take a frame right now."""
        return not self.down and self._inner.is_connected

    async def connect(self) -> None:
        """Nothing to dial: the pair is in-process."""

    async def send(self, envelope: Envelope) -> None:
        """Hand `envelope` to the link, or raise as a dropped link does while `down`."""
        if self.down:
            raise ConnectionLostError("The test is holding this link down.")
        await self._inner.send(envelope)

    def receive(self) -> AsyncIterator[Envelope]:
        """Read the Warden's frames from the link."""
        return self._inner.receive()

    async def close(self) -> None:
        """Close the Queen's end."""
        await self._inner.close()


def _ceilings() -> Ceilings:
    """A small, valid set of ceilings."""
    return Ceilings(
        max_sub_bees=2,
        model_vram_bytes=1_000,
        model_disk_bytes=2_000,
        loadable_sources=(),
        exportable_seats=1,
    )


async def _provisioned_while_down(
    deps: QueenDeps, link: WardenLink
) -> tuple[WardenLink, _DroppableEnd]:
    """Set `link`'s ceilings and write its plan while its link refuses frames."""
    end = _DroppableEnd(link.transport)
    down = dataclasses.replace(link, transport=end)
    await set_ceilings(down, _ceilings(), deps)
    await write_hosting_plan(down.cell, deps, down)
    return down, end


async def test_a_refused_first_send_leaves_the_warden_owed() -> None:
    deps, link, _warden_end = make_queen_deps()

    down, _end = await _provisioned_while_down(deps, link)

    assert deps.ledger.decisions.owed() == {down.warden_id}


async def test_the_next_pass_delivers_both_once_the_link_takes_frames() -> None:
    deps, link, warden_end = make_queen_deps()
    down, end = await _provisioned_while_down(deps, link)
    end.down = False

    await redeliver_owed(deps, (down,))

    await warden_end.pump_until(lambda: bool(warden_end.plans_written))
    [ceilings] = warden_end.ceilings_sets
    assert (ceilings.holder, ceilings.revision) == (link.warden_id, 0)
    assert warden_end.plans_written[0].cell_id == link.cell.id
    assert deps.ledger.decisions.owed() == frozenset()


async def test_a_link_still_down_keeps_the_warden_owed() -> None:
    deps, link, _warden_end = make_queen_deps()
    down, _end = await _provisioned_while_down(deps, link)

    await redeliver_owed(deps, (down,))

    assert deps.ledger.decisions.owed() == {down.warden_id}


async def test_a_detached_warden_is_owed_nothing_more() -> None:
    deps, link, _warden_end = make_queen_deps()
    await _provisioned_while_down(deps, link)

    await redeliver_owed(deps, ())

    assert deps.ledger.decisions.owed() == frozenset()


async def test_every_dispatch_pass_redelivers_what_is_owed() -> None:
    deps, link, warden_end = make_queen_deps()
    down, end = await _provisioned_while_down(deps, link)
    end.down = False

    await dispatch_ready(deps, (down,))

    await warden_end.pump_until(lambda: bool(warden_end.plans_written))
    assert len(warden_end.ceilings_sets) == 1
