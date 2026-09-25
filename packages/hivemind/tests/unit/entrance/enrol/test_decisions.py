"""Tests for hivemind.entrance.enrol.decisions: approving and denying a pending request.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/decisions.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.decisions for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.entrance import (
    APPROVAL_TTL,
    Enrolment,
    admitted,
    approval,
    entry_event,
    make_device,
    memory_enrolment,
    mint,
    redeem_browser,
)
from pydantic import ValidationError

from hivemind.entrance.enrol import (
    ApprovalRequest,
    DeviceStatus,
    GrantChange,
    approve,
    deny,
    regrant,
)
from hivemind.entrance.errors import (
    CapabilityCeilingError,
    ConsoleProtectedError,
    DeviceStatusConflictError,
    InvalidApprovalError,
)
from hivemind.guard import InvalidCapabilityError, load_guard_policy, proposed_set

_CONSOLE_ID = "device_01M221E4C10R4XDPNQNRX85AAA"  # Stands for the approving console's id.


@pytest.fixture
def rig() -> Enrolment:
    """An enrolment rig over in-memory tables."""
    return memory_enrolment()


# ──────────────────────────────────────────────────────────────────────────────
# Approval
# ──────────────────────────────────────────────────────────────────────────────


async def test_approve_binds_everything_the_operator_chose(rig: Enrolment) -> None:
    pending = await admitted(rig, DeviceStatus.PENDING)
    expires_at = rig.clock.now() + APPROVAL_TTL
    request = approval(
        name="build bot",
        capabilities=("entrance:submit", "observe"),
        spend_cap_usd_per_day=2.5,
        expires_at=expires_at,
        interactive=True,
        actor=_CONSOLE_ID,
    )

    approved = await approve(rig.deps, pending.id, request)

    assert approved.status is DeviceStatus.APPROVED
    assert (approved.name, approved.capabilities) == ("build bot", ("entrance:submit", "observe"))
    assert (approved.spend_cap_usd_per_day, approved.expires_at) == (2.5, expires_at)
    assert (approved.interactive, approved.approved_at) == (True, rig.clock.now())
    assert approved.public_key == pending.public_key
    (event,) = await rig.events("guard.entrance_approved")
    assert event.actor == _CONSOLE_ID
    assert event.payload["expires_at"] == expires_at.isoformat()


async def test_approve_records_the_event_and_tells_every_other_device(rig: Enrolment) -> None:
    pending = await admitted(rig, DeviceStatus.PENDING)

    await approve(rig.deps, pending.id, approval(capabilities=("observe",)))

    (event,) = await rig.events("guard.entrance_approved")
    assert (event.subject_id, event.actor) == (pending.id, "human")
    assert event.payload == {
        "fingerprint": pending.fingerprint,
        "capability_count": 1,
        "spend_cap_usd_per_day": 5.0,
        "expires_at": None,
        "interactive": False,
    }
    assert rig.notifier.notices[-1].event_id == event.id
    assert rig.offboarder.offboarded == []


async def test_approve_naming_nothing_grants_the_proposed_set(rig: Enrolment) -> None:
    pending = await admitted(rig, DeviceStatus.PENDING)

    approved = await approve(rig.deps, pending.id, approval())

    assert approved.capabilities == proposed_set(load_guard_policy()).as_strings()


async def test_a_passkey_device_is_interactive_whatever_the_request_says(rig: Enrolment) -> None:
    redemption = await redeem_browser(rig, await mint(rig))

    approved = await approve(rig.deps, redemption.device_id, approval(interactive=False))

    assert approved.interactive is True


async def test_a_capability_beyond_the_ceiling_refuses_the_approval_and_writes_nothing(
    rig: Enrolment,
) -> None:
    pending = await admitted(rig, DeviceStatus.PENDING)
    recorded = await rig.events()

    with pytest.raises(CapabilityCeilingError, match="supersede"):
        await approve(rig.deps, pending.id, approval(capabilities=("observe", "supersede")))

    assert await rig.store.get_device(pending.id) == pending
    assert await rig.events() == recorded


async def test_a_string_that_is_not_a_capability_refuses_the_approval(rig: Enrolment) -> None:
    pending = await admitted(rig, DeviceStatus.PENDING)

    with pytest.raises(InvalidCapabilityError):
        await approve(rig.deps, pending.id, approval(capabilities=("observe:all",)))
    assert (await rig.store.get_device(pending.id)).status is DeviceStatus.PENDING


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(seconds=-1)])
async def test_an_approval_that_would_already_have_expired_is_refused(
    rig: Enrolment, offset: timedelta
) -> None:
    pending = await admitted(rig, DeviceStatus.PENDING)

    with pytest.raises(InvalidApprovalError, match=pending.id):
        await approve(rig.deps, pending.id, approval(expires_at=rig.clock.now() + offset))


async def test_only_a_pending_device_is_approved(rig: Enrolment) -> None:
    minted = await mint(rig)
    approved = await admitted(rig)

    with pytest.raises(DeviceStatusConflictError):
        await approve(rig.deps, minted.device_id, approval())
    with pytest.raises(DeviceStatusConflictError):
        await approve(rig.deps, approved.id, approval())


@pytest.mark.parametrize(
    "overrides",
    [
        {"spend_cap_usd_per_day": -1.0},
        {"spend_cap_usd_per_day": float("inf")},
        {"actor": "system"},  # An approval is never the system's own decision.
        {"actor": "worker_01M221E4C10R4XDPNQNRX85AAA"},
        {"name": "phone\x1b[2J"},
        {"name": ""},
        {"capabilities": ("Observe",)},
        {"capabilities": ("observe",) * 65},
        {"unexpected": True},
    ],
)
def test_an_approval_request_refuses_what_an_operator_cannot_mean(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        approval(**overrides)


def test_an_approval_request_accepts_the_operator_or_a_device_as_actor() -> None:
    assert approval(actor="human").actor == "human"
    assert approval(actor=_CONSOLE_ID).actor == _CONSOLE_ID
    assert ApprovalRequest.model_validate_json(approval().model_dump_json()) == approval()


# ──────────────────────────────────────────────────────────────────────────────
# Denial
# ──────────────────────────────────────────────────────────────────────────────


async def test_deny_refuses_a_pending_device_with_the_operators_reason(rig: Enrolment) -> None:
    pending = await admitted(rig, DeviceStatus.PENDING)

    denied = await deny(rig.deps, pending.id, "human", "not one of mine")

    assert denied.status is DeviceStatus.DENIED
    (event,) = await rig.events("guard.entrance_denied")
    assert (event.subject_id, event.payload) == (pending.id, {"reason": "not one of mine"})
    assert rig.notifier.notices[-1].event_id == event.id


@pytest.mark.parametrize("reason", ["", "x" * 201, "line\nbreak", "rtl‮override"])
async def test_a_denial_reason_must_be_short_display_text(rig: Enrolment, reason: str) -> None:
    pending = await admitted(rig, DeviceStatus.PENDING)

    with pytest.raises(ValidationError):
        await deny(rig.deps, pending.id, "human", reason)
    assert (await rig.store.get_device(pending.id)).status is DeviceStatus.PENDING


async def test_only_a_pending_device_is_denied(rig: Enrolment) -> None:
    approved = await admitted(rig)

    with pytest.raises(DeviceStatusConflictError):
        await deny(rig.deps, approved.id, "human", "too late")


# ──────────────────────────────────────────────────────────────────────────────
# Re-granting an approved device
# ──────────────────────────────────────────────────────────────────────────────


async def test_regrant_replaces_the_set_and_cap_and_records_a_fresh_approval(
    rig: Enrolment,
) -> None:
    device = await admitted(rig)
    change = GrantChange(
        capabilities=("observe", "entrance:push"), spend_cap_usd_per_day=1.5, actor=_CONSOLE_ID
    )

    updated = await regrant(rig.deps, device.id, change)

    assert set(updated.capabilities) == {"observe", "entrance:push"}
    assert updated.spend_cap_usd_per_day == 1.5
    event = (await rig.events("guard.entrance_approved"))[-1]
    assert (event.subject_id, event.actor) == (device.id, _CONSOLE_ID)
    assert event.payload["regrant"] is True
    assert rig.notifier.notices[-1].event_id == event.id


async def test_regrant_without_a_cap_keeps_the_current_one(rig: Enrolment) -> None:
    device = await admitted(rig)
    change = GrantChange(capabilities=("observe",), actor=_CONSOLE_ID)

    updated = await regrant(rig.deps, device.id, change)

    assert updated.spend_cap_usd_per_day == device.spend_cap_usd_per_day


async def test_regrant_refuses_a_capability_beyond_the_device_ceiling(rig: Enrolment) -> None:
    device = await admitted(rig)
    change = GrantChange(capabilities=("supersede",), actor=_CONSOLE_ID)

    with pytest.raises(CapabilityCeilingError):
        await regrant(rig.deps, device.id, change)


async def test_regrant_refuses_a_device_that_is_not_approved(rig: Enrolment) -> None:
    pending = await admitted(rig, DeviceStatus.PENDING)
    change = GrantChange(capabilities=("observe",), actor=_CONSOLE_ID)

    with pytest.raises(DeviceStatusConflictError):
        await regrant(rig.deps, pending.id, change)


async def test_regrant_leaves_the_console_alone(rig: Enrolment) -> None:
    console = make_device(
        rig.clock, DeviceStatus.APPROVED, loopback_bound=True, interactive=True, expires_at=None
    )
    await rig.store.put_device(console, entry_event(console, rig.clock))
    change = GrantChange(capabilities=("observe",), actor=_CONSOLE_ID)

    with pytest.raises(ConsoleProtectedError):
        await regrant(rig.deps, console.id, change)
