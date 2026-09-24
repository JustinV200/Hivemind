"""Tests for hivemind.entrance.auth.confirm.flow: held until a person confirms, once.

The ADR-0033 path runs end to end first: a program's goal over its daily cap needs step-up, the
program cannot step up, the goal is held, and the console, logged in and stepped up, confirms it
and gets the held action back exactly once. Every refusal follows.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/confirm/flow.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.confirm.flow for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.entrance import (
    LOOPBACK,
    PASSWORD,
    AuthRig,
    admitted_console,
    admitted_program,
    auth_rig,
    make_session,
    program_login,
    sign_b64url,
    signed_request,
)
from pydantic import JsonValue

from hivemind.entrance.auth import (
    ActionKind,
    AuthenticatedSession,
    DeviceProof,
    GoalSpend,
    PendingStatus,
    StepUpReason,
    authenticate_request,
    cancel,
    confirm,
    expire_pending,
    hold,
    login_string,
    requires_step_up,
    step_up,
    step_up_challenge,
)
from hivemind.entrance.enrol import EnrolledDevice, revoke
from hivemind.entrance.errors import (
    ConfirmationRefusedError,
    PendingStatusConflictError,
    StepUpUnavailableError,
)
from hivemind.guard import CapabilitySet

_GOAL: dict[str, JsonValue] = {"goal": "rebuild the photo index", "budget_usd": 4.0}


def _session(device: EnrolledDevice, stepped_up: bool) -> AuthenticatedSession:
    """An authenticated session of ``device``, stepped up or not."""
    return AuthenticatedSession(make_session(device.id), device, CapabilitySet.empty(), stepped_up)


async def _stepped_up_console(auth: AuthRig) -> AuthenticatedSession:
    """Log the console in, step it up for real, and return its authenticated session."""
    console, signer = await admitted_console(auth)
    opened = await program_login(auth, console, signer)
    request = signed_request(opened.token, signer, auth.clock.now())
    session = await authenticate_request(auth.book, request, auth.clock.now())
    challenge = step_up_challenge(auth.deps, session)
    hive_id = auth.deps.records.identity.hive_id
    signature = sign_b64url(signer, login_string(hive_id, console.id, challenge.nonce))
    proof = DeviceProof(console.id, challenge.nonce, signature=signature)
    await step_up(auth.deps, session, proof, PASSWORD, LOOPBACK)
    request = signed_request(opened.token, signer, auth.clock.now())
    return await authenticate_request(auth.book, request, auth.clock.now())


async def test_a_programs_goal_over_its_cap_waits_for_a_person_and_runs_once() -> None:
    auth = await auth_rig()
    program, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, program, signer)
    request = signed_request(opened.token, signer, auth.clock.now())
    session = await authenticate_request(auth.book, request, auth.clock.now())
    spend = GoalSpend(budget_usd=4.0, spent_today_usd=3.0)  # A 5.00 cap: 7.00 would pass it.
    reason = requires_step_up(session, ActionKind.GOAL, spend, step_up_spend=5.0)
    with pytest.raises(StepUpUnavailableError):
        step_up_challenge(auth.deps, session)
    records = auth.deps.records

    pending_id = await hold(records, session.device, ActionKind.GOAL, _GOAL)
    console = await _stepped_up_console(auth)
    held = await confirm(records, pending_id, console)

    assert reason is StepUpReason.OVER_DAILY_CAP
    assert (held.device_id, held.action, held.payload, held.confirmed_by) == (
        program.id,
        ActionKind.GOAL,
        _GOAL,
        console.device.id,
    )
    with pytest.raises(ConfirmationRefusedError):
        await confirm(records, pending_id, console)
    assert (await auth.store.pending.get(pending_id)).status is PendingStatus.CONFIRMED


async def test_only_a_non_interactive_devices_ordinary_request_is_held() -> None:
    auth = await auth_rig()
    program, _ = await admitted_program(auth.enrolment)
    console, _ = await admitted_console(auth)
    records = auth.deps.records

    with pytest.raises(ConfirmationRefusedError, match="interactive"):
        await hold(records, console, ActionKind.GOAL, _GOAL)
    for action in (ActionKind.ABSCOND, ActionKind.STING_CUT, ActionKind.SUPERSEDURE):
        with pytest.raises(ConfirmationRefusedError):
            await hold(records, program, action, {})

    assert await auth.store.pending.list_by_status() == ()


async def test_only_an_interactive_stepped_up_session_confirms() -> None:
    auth = await auth_rig()
    program, _ = await admitted_program(auth.enrolment)
    console, _ = await admitted_console(auth)
    records = auth.deps.records
    pending_id = await hold(records, program, ActionKind.GOAL, _GOAL)

    for session in (_session(console, stepped_up=False), _session(program, stepped_up=True)):
        with pytest.raises(ConfirmationRefusedError):
            await confirm(records, pending_id, session)

    assert (await auth.store.pending.get(pending_id)).status is PendingStatus.PENDING


async def test_an_expired_or_orphaned_request_is_settled_instead_of_confirmed() -> None:
    auth = await auth_rig()
    program, _ = await admitted_program(auth.enrolment)
    revoked, _ = await admitted_program(auth.enrolment)
    console, _ = await admitted_console(auth)
    records = auth.deps.records
    late = await hold(records, program, ActionKind.GOAL, _GOAL, ttl=timedelta(minutes=1))
    orphan = await hold(records, revoked, ActionKind.GOAL, _GOAL)
    await revoke(auth.deps.enrolment, revoked.id, "human", cancel_goals=False)
    auth.clock.advance(61)

    for pending_id in (late, orphan):
        with pytest.raises(ConfirmationRefusedError):
            await confirm(records, pending_id, _session(console, stepped_up=True))

    assert (await auth.store.pending.get(late)).status is PendingStatus.EXPIRED
    assert (await auth.store.pending.get(orphan)).status is PendingStatus.CANCELLED


async def test_a_person_may_decline_a_held_request() -> None:
    auth = await auth_rig()
    program, _ = await admitted_program(auth.enrolment)
    console, _ = await admitted_console(auth)
    records = auth.deps.records
    pending_id = await hold(records, program, ActionKind.GOAL, _GOAL)

    with pytest.raises(ConfirmationRefusedError):
        await cancel(records, pending_id, _session(program, stepped_up=False))
    declined = await cancel(records, pending_id, _session(console, stepped_up=False))

    assert declined.status is PendingStatus.CANCELLED
    with pytest.raises(PendingStatusConflictError):
        await cancel(records, pending_id, _session(console, stepped_up=False))


async def test_the_sweep_expires_only_what_is_past_its_expiry() -> None:
    auth = await auth_rig()
    program, _ = await admitted_program(auth.enrolment)
    records = auth.deps.records
    short = await hold(records, program, ActionKind.GOAL, _GOAL, ttl=timedelta(minutes=1))
    long = await hold(records, program, ActionKind.NEW_NETWORK, {"network": "203.0.113.0/24"})
    auth.clock.advance(60)

    expired = await expire_pending(records)

    assert expired == 1
    assert (await auth.store.pending.get(short)).status is PendingStatus.EXPIRED
    assert (await auth.store.pending.get(long)).status is PendingStatus.PENDING
