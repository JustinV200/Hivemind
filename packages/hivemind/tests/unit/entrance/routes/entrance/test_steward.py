"""Test the steward route: a remote steward approves after full step-up, within its own set.

ADR-0041 and roadmap 10.5d: with ``[entrance] steward_devices`` on, a device holding
``entrance:steward`` may approve a pending device through its own route on the remote listener,
only after full step-up, only up to its own set inside the device ceiling, never granting
stewardship itself, and never more spend per day than its own. A device without stewardship is
refused, and with the switch off the route does not exist.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import RIG_SECTION, ProgramGrant, RigOptions, ServingRig, serving

from hivemind.entrance.enrol import DeviceStatus
from waggle.ids import DeviceId

# A person holds this device, so it may step up; it approves from the remote listener.
_STEWARD = ProgramGrant(
    capabilities=("observe", "entrance:submit", "entrance:steward"),
    interactive=True,
    remote=True,
)
_SWITCH_ON = RigOptions(
    remote=True, section=RIG_SECTION.model_copy(update={"steward_devices": True})
)
_REFUSED = "hivemind.entrance.steward_grant_refused"


async def _pending(rig: ServingRig) -> str:
    """Mint an invite at the Hive Stand and redeem it remotely; return the PENDING device's id."""
    console, session = await rig.console_session()
    invite = await console.call(session, "POST", "/v1/entrance/invites", {"label": "tablet"})
    assert invite.status_code == 201, invite.text
    key = await rig.client(remote=True).enrol(invite.json()["code"])
    return key.device_id


def _approval(device_id: str) -> str:
    """The steward route for one pending device."""
    return f"/v1/entrance/steward/{device_id}/approve"


def _asking(*capabilities: str, cap: float = 1.0) -> dict[str, object]:
    """An approval body asking for ``capabilities`` and a daily cap of ``cap`` USD."""
    return {"name": "tablet", "capabilities": list(capabilities), "spend_cap_usd_per_day": cap}


async def _status(rig: ServingRig, device_id: str) -> DeviceStatus:
    """The device's stored status."""
    stored = await rig.store.get_device(DeviceId(device_id))
    assert stored is not None
    return stored.status


async def test_a_steward_is_refused_until_it_has_stepped_up() -> None:
    async with serving(_SWITCH_ON) as rig:
        steward, session = await rig.program(_STEWARD)
        waiting = await _pending(rig)

        refused = await steward.call(session, "POST", _approval(waiting), _asking("observe"))
        status = await _status(rig, waiting)

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"] == "hivemind.entrance.step_up_required"
    assert status is DeviceStatus.PENDING


async def test_a_stepped_up_steward_grants_only_its_own_set_and_never_stewardship() -> None:
    async with serving(_SWITCH_ON) as rig:
        steward, session = await rig.program(_STEWARD)
        await steward.step_up(session)
        beyond, onward, within = [await _pending(rig) for _ in range(3)]

        # entrance:answer is inside the ceiling but not the steward's own; stewardship is its own.
        refusals = [
            await steward.call(session, "POST", _approval(beyond), _asking("entrance:answer")),
            await steward.call(session, "POST", _approval(onward), _asking("entrance:steward")),
        ]
        granted = await steward.call(
            session, "POST", _approval(within), _asking("observe", "entrance:submit")
        )
        stored = await rig.store.get_device(DeviceId(within))
        left = [await _status(rig, device) for device in (beyond, onward)]

    assert [(r.status_code, r.json()["error"]) for r in refusals] == [(403, _REFUSED)] * 2
    assert left == [DeviceStatus.PENDING, DeviceStatus.PENDING]
    assert granted.status_code == 200, granted.text
    assert stored is not None and stored.status is DeviceStatus.APPROVED
    assert set(stored.capabilities) == {"observe", "entrance:submit"}


async def test_a_steward_may_not_grant_more_spend_per_day_than_its_own() -> None:
    async with serving(_SWITCH_ON) as rig:
        steward, session = await rig.program(_STEWARD)
        await steward.step_up(session)
        waiting = await _pending(rig)
        own = _STEWARD.spend_cap_usd_per_day

        refused = await steward.call(
            session, "POST", _approval(waiting), _asking("observe", cap=own + 1)
        )
        status = await _status(rig, waiting)

    assert (refused.status_code, refused.json()["error"]) == (403, _REFUSED)
    assert status is DeviceStatus.PENDING


async def test_a_device_without_stewardship_is_refused_the_route() -> None:
    async with serving(_SWITCH_ON) as rig:
        device, session = await rig.program(ProgramGrant(interactive=True, remote=True))
        await device.step_up(session)
        waiting = await _pending(rig)

        refused = await device.call(session, "POST", _approval(waiting), _asking("observe"))
        status = await _status(rig, waiting)

    assert refused.status_code == 403, refused.text
    assert status is DeviceStatus.PENDING


async def test_with_the_switch_off_the_steward_route_does_not_exist() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        steward, session = await rig.program(_STEWARD)
        waiting = await _pending(rig)

        missing = await steward.call(session, "POST", _approval(waiting), _asking("observe"))

    assert missing.status_code == 404
