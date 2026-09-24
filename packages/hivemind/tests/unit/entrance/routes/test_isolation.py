"""Test hivemind.entrance.routes.isolation: the human's isolate and lift levers, over real sockets.

Roadmap step 10.6a (ADR-0035). ``POST /v1/cells/{cell_id}/isolate`` and ``/lift`` need an
interactive device inside its step-up window holding ``entrance:steward``: without step-up both
answer ``403 step_up_required`` and nothing changes; a program, which no person types at, can
never step up and is refused outright (nothing is held for it); a device without stewardship is
refused whatever it did. After step-up the console isolates the Queen's attached Cell through her
door (``cell.isolated`` on her trail) and lifts it again (``cell.isolation_lifted``); a second lift
has nothing to lift (``409``). A steward device on the remote listener pulls the same levers.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import ProgramGrant, RigOptions, ServingRig, serving

from hivemind.pheromone import TrailQuery
from hivemind.queen.isolation import ISOLATED_KIND, LIFTED_KIND

_BODY = {"reason": "The operator saw an odd burst on this Cell."}
_STEWARD = ("observe", "entrance:steward")


def _cell(rig: ServingRig) -> str:
    """The Queen's one attached Warden's Cell."""
    return rig.queen.wardens[0].cell.id


async def test_both_levers_refuse_a_session_that_has_not_stepped_up() -> None:
    async with serving() as rig:
        console, session = await rig.console_session()
        cell = _cell(rig)

        isolate = await console.call(session, "POST", f"/v1/cells/{cell}/isolate", _BODY)
        lift = await console.call(session, "POST", f"/v1/cells/{cell}/lift")
        isolated = await rig.deps.trail.query(TrailQuery(kind=ISOLATED_KIND))

    for refused in (isolate, lift):
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"].endswith("step_up_required")
        assert refused.json()["pending_id"] is None  # Nothing is held: only a person may do it.
    assert isolated == ()


async def test_after_step_up_the_console_isolates_and_lifts_through_the_queens_door() -> None:
    async with serving() as rig:
        console, session = await rig.console_session()
        cell = _cell(rig)
        await console.step_up(session)

        isolate = await console.call(session, "POST", f"/v1/cells/{cell}/isolate", _BODY)
        isolated = await rig.deps.trail.query(TrailQuery(kind=ISOLATED_KIND, subject_id=cell))
        lift = await console.call(session, "POST", f"/v1/cells/{cell}/lift")
        again = await console.call(session, "POST", f"/v1/cells/{cell}/lift")
        lifted = await rig.deps.trail.query(TrailQuery(kind=LIFTED_KIND, subject_id=cell))

    assert isolate.status_code == 200, isolate.text
    assert isolate.json()["isolated"] and isolate.json()["event_id"] == isolated[0].id
    assert isolated[0].payload["ordered_by"] == "human"
    assert isolated[0].payload["device_id"] == session.key.device_id
    assert lift.status_code == 200, lift.text
    assert lift.json()["isolated_event_id"] == isolated[0].id
    assert [event.id for event in lifted] == [lift.json()["event_id"]]
    assert again.status_code == 409  # Nothing left to lift; nothing recorded.


async def test_a_program_can_never_pull_either_lever() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=_STEWARD))
        cell = _cell(rig)

        isolate = await client.call(session, "POST", f"/v1/cells/{cell}/isolate", _BODY)
        lift = await client.call(session, "POST", f"/v1/cells/{cell}/lift")

    assert (isolate.status_code, lift.status_code) == (403, 403)
    assert isolate.json()["error"].endswith("step_up_required")


async def test_a_device_without_stewardship_is_refused() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(interactive=True))
        await client.step_up(session)

        isolate = await client.call(session, "POST", f"/v1/cells/{_cell(rig)}/isolate", _BODY)

    assert isolate.status_code == 403
    assert not isolate.json()["error"].endswith("step_up_required")


async def test_a_steward_device_pulls_the_levers_on_the_remote_listener() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        grant = ProgramGrant(capabilities=_STEWARD, interactive=True, remote=True)
        client, session = await rig.program(grant)
        cell = _cell(rig)
        await client.step_up(session)

        isolate = await client.call(session, "POST", f"/v1/cells/{cell}/isolate", _BODY)
        lift = await client.call(session, "POST", f"/v1/cells/{cell}/lift")

    assert (isolate.status_code, lift.status_code) == (200, 200), (isolate.text, lift.text)
