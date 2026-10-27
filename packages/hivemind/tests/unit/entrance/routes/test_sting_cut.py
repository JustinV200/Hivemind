"""Test hivemind.entrance.routes.isolation.sting_cut: the human's Sting Cut, over real sockets.

Roadmap step 13.4a. ``POST /v1/cells/{target}/sting-cut`` is break-glass: without step-up it
answers ``403 step_up_required``; stepped up but without the typed phrase (or with the wrong one) it
answers ``403 break_glass_refused``; nothing is cut either way. Stepped up and with the phrase, the
console cuts the Queen's attached Cell through her door, named by a lease on it (the newest lease,
found even when older ones fill the query's page): `cell.sting_cut` on her trail names the human
and the device.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.entrance.serving import ServingRig, serving

from hivemind.pheromone import CellEvent, TrailQuery
from hivemind.queen.sting_cut import STING_CUT_KIND
from waggle.ids import CellId, new_cell_id, new_event_id, new_lease_id

_PHRASE = "Sting cut  this cell"  # As a phone capitalises it and a thumb doubles a space.


def _body(phrase: str | None) -> dict[str, str]:
    """A cut's body with `phrase`, or none at all."""
    body = {"reason": "The operator saw a forged frame from this Cell."}
    return body if phrase is None else {**body, "phrase": phrase}


async def _lease_on(rig: ServingRig, cell: CellId) -> str:
    """Record a lease on `cell`, as its Warden's `cell.leased` does."""
    lease_id = new_lease_id(rig.deps.clock)
    event = CellEvent(
        id=new_event_id(rig.deps.clock),
        hive_id=rig.deps.identity.hive_id,
        node_id=rig.deps.identity.node_id,
        at=rig.deps.clock.now(),
        actor="system",
        kind="cell.leased",
        subject_id=cell,
        payload={"lease_id": lease_id},
    )
    await rig.deps.trail.record(event)
    return lease_id


async def test_a_cut_needs_the_step_up_and_then_the_typed_phrase() -> None:
    async with serving() as rig:
        console, session = await rig.console_session()
        path = f"/v1/cells/{rig.queen.wardens[0].cell.id}/sting-cut"

        unstepped = await console.call(session, "POST", path, _body(_PHRASE))
        await console.step_up(session)
        missing = await console.call(session, "POST", path, _body(None))
        wrong = await console.call(session, "POST", path, _body("cut it"))
        cuts = await rig.deps.trail.query(TrailQuery(kind=STING_CUT_KIND))

    assert unstepped.status_code == 403 and unstepped.json()["error"].endswith("step_up_required")
    assert missing.status_code == 422  # The phrase is a required field of the body.
    assert wrong.status_code == 403 and wrong.json()["error"].endswith("break_glass_refused")
    assert cuts == ()


async def test_after_step_up_the_phrase_cuts_the_cell_a_lease_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A one-row page: an oldest-first lookup would see only the older lease on another Cell.
    monkeypatch.setattr("hivemind.queen.sting_cut.door.MAX_QUERY_LIMIT", 1)
    async with serving() as rig:
        console, session = await rig.console_session()
        await _lease_on(rig, new_cell_id(rig.deps.clock))
        lease_id = await _lease_on(rig, rig.queen.wardens[0].cell.id)
        await console.step_up(session)

        cut = await console.call(session, "POST", f"/v1/cells/{lease_id}/sting-cut", _body(_PHRASE))
        cuts = await rig.deps.trail.query(TrailQuery(kind=STING_CUT_KIND))

    assert cut.status_code == 200, cut.text
    [event] = cuts
    assert cut.json()["cut"] and cut.json()["event_id"] == event.id
    assert (event.payload["cut_by"], event.payload["device_id"]) == ("human", session.key.device_id)
