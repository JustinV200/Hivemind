"""Test hivemind.entrance.intake: every way a goal comes in is weighed and committed one way.

A goal counts against its device's day at its own budget or the manifest's per-goal cap (a refused
one not at all); a held payload becomes a request from its device, whose approved set is its
ceiling (none for the Hive Stand's console); and a held goal is committed exactly once under the
id minted when it was held, however often it is carried out.

Fits into the Hive:
    Mirrors src/hivemind/entrance/intake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import GOAL_SPEND_CAP_USD, serving
from pydantic import JsonValue

from hivemind.entrance.intake import goal_request, goal_spend, submit_held_goal
from hivemind.queen import GoalRequestQuery, GoalSource
from hivemind.queen.intake import new_goal_request_id
from waggle.clock import Clock
from waggle.ids import DeviceId


def _held(clock: Clock, **fields: JsonValue) -> dict[str, JsonValue]:
    """A goal's held payload, as the goals route and the voice door build one."""
    return {
        "id": new_goal_request_id(clock),
        "text": "Tidy the garden notes.",
        "budget_usd": None,
        "comb_shield": None,
        "clearance": "C1",
        **fields,
    }


async def test_a_goal_counts_at_its_budget_or_the_per_goal_cap_against_its_devices_day() -> None:
    async with serving() as rig:
        client, session = await rig.program()
        await client.call(session, "POST", "/v1/goals", {"text": "one", "budget_usd": 0.5})
        await client.call(session, "POST", "/v1/goals", {"text": "two"})
        device = await rig.store.get_device(DeviceId(session.key.device_id))

        spend = await goal_spend(rig.entrance.services, device, None)

    assert spend.budget_usd == GOAL_SPEND_CAP_USD
    assert spend.spent_today_usd == 0.5 + GOAL_SPEND_CAP_USD


async def test_a_held_payload_becomes_a_request_capped_by_its_device_but_not_the_console() -> None:
    async with serving() as rig:
        _client, session = await rig.program()
        program = await rig.store.get_device(DeviceId(session.key.device_id))
        console = await rig.store.get_device(DeviceId(rig.console.device_id))
        spoken = _held(rig.clock, source="spoken", needs_confirmation=True)

        from_program = goal_request(rig.entrance.services, program, spoken)
        from_console = goal_request(rig.entrance.services, console, _held(rig.clock))

    assert set(from_program.capabilities or ()) == set(program.capabilities)
    assert (from_program.source, from_program.needs_confirmation) == (GoalSource.SPOKEN, True)
    assert from_console.capabilities is None


async def test_a_held_goal_is_committed_once_under_its_minted_id() -> None:
    async with serving() as rig:
        _client, session = await rig.program()
        device = await rig.store.get_device(DeviceId(session.key.device_id))
        held = _held(rig.clock)

        first = await submit_held_goal(rig.entrance.services, device, held)
        again = await submit_held_goal(rig.entrance.services, device, held)
        stored = await rig.deps.goal_requests.list_requests(GoalRequestQuery(limit=10))

    assert first == again == held["id"]
    assert [request.id for request in stored] == [first]
