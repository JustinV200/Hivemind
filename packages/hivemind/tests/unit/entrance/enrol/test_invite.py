"""Tests for hivemind.entrance.enrol.invite: minting and cancelling invites, the code's forms.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/invite.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.invite for the module under test.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from builders.entrance import INVITE_TTL, ORIGIN, Enrolment, admitted, memory_enrolment, mint
from pydantic import ValidationError

from hivemind.entrance.auth import sha256_hex
from hivemind.entrance.enrol import (
    INVITE_PATH,
    DeviceStatus,
    cancel_invite,
    canonical_invite_code,
    invite_code_hash,
    invite_url,
    mint_invite,
    new_invite_code,
)
from hivemind.entrance.errors import DeviceStatusConflictError
from hivemind.pheromone import TrailQuery

# 26 base32 characters in groups of four: six groups of four and a final pair.
_CODE_SHAPE = re.compile(r"[A-Z2-7]{4}(-[A-Z2-7]{4}){5}-[A-Z2-7]{2}")


@pytest.fixture
def rig() -> Enrolment:
    """An enrolment rig over in-memory tables."""
    return memory_enrolment()


def test_a_new_code_is_128_random_bits_grouped_in_fours() -> None:
    codes = {new_invite_code() for _ in range(20)}

    assert len(codes) == 20
    assert all(_CODE_SHAPE.fullmatch(code) for code in codes)


@pytest.mark.parametrize("typed", [str.lower, lambda code: code.replace("-", ""), " ".join])
def test_a_code_typed_another_way_restores_to_its_grouped_form(
    typed: Callable[[str], str],
) -> None:
    code = new_invite_code()
    variant = typed(code)

    assert canonical_invite_code(variant) == code
    assert invite_code_hash(variant) == sha256_hex(code.encode("utf-8"))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ABCD-EFGH",  # Too short.
        "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-Y0",  # 0 is not in base32's alphabet.
        "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ",  # The spare bits of the last character are set.
        "ÄBCD-EFGH-IJKL-MNOP-QRST-UVWX-YA",
    ],
)
def test_text_that_is_not_a_code_is_refused_without_being_repeated(text: str) -> None:
    with pytest.raises(ValueError, match="invite code") as excinfo:
        canonical_invite_code(text)

    assert text == "" or text not in str(excinfo.value)


def test_the_link_carries_the_code_in_its_fragment() -> None:
    assert invite_url("https://hive.example.ts.net/", "ABCD") == (
        f"https://hive.example.ts.net{INVITE_PATH}#code=ABCD"
    )


async def test_mint_records_an_invited_device_and_only_the_codes_hash(rig: Enrolment) -> None:
    minted = await mint(rig, "phone")

    device = await rig.store.get_device(minted.device_id)
    invite = await rig.store.get_invite(invite_code_hash(minted.code))
    assert (device.status, device.name) == (DeviceStatus.INVITED, "phone")
    assert device.expires_at == minted.expires_at == rig.clock.now() + INVITE_TTL
    assert (invite.device_id, invite.label, invite.used_at) == (device.id, "phone", None)
    assert _CODE_SHAPE.fullmatch(minted.code)
    assert minted.code not in device.model_dump_json() + invite.model_dump_json()


async def test_mint_shows_the_link_and_both_qr_codes_once(rig: Enrolment) -> None:
    minted = await mint(rig)

    assert minted.url == f"{ORIGIN}{INVITE_PATH}#code={minted.code}"
    assert minted.qr.svg.startswith("<svg")
    assert {"█", "▀", "▄"} & set(minted.qr.terminal)
    assert minted.code not in repr(minted)
    assert minted.code not in minted.qr.svg


async def test_mint_records_the_invited_event_and_notifies(rig: Enrolment) -> None:
    minted = await mint_invite(rig.deps, "laptop", actor="human")

    (event,) = await rig.events()
    assert (event.kind, event.subject_id, event.actor) == (
        "guard.entrance_invited",
        minted.device_id,
        "human",
    )
    assert event.payload == {"label": "laptop", "expires_at": minted.expires_at.isoformat()}
    assert minted.code not in event.model_dump_json()
    assert [(notice.device_id, notice.event_id) for notice in rig.notifier.notices] == [
        (minted.device_id, event.id)
    ]


@pytest.mark.parametrize("label", ["", "x" * 65, "phone\n", "pho‮ne"])
async def test_a_bad_label_is_refused_before_anything_is_written(
    rig: Enrolment, label: str
) -> None:
    with pytest.raises(ValidationError):
        await mint(rig, label)

    assert await rig.store.list_devices() == ()
    assert await rig.events() == ()


async def test_cancel_withdraws_an_unredeemed_invite(rig: Enrolment) -> None:
    minted = await mint(rig)

    cancelled = await cancel_invite(rig.deps, minted.device_id, "human")

    assert cancelled.status is DeviceStatus.REVOKED
    (event,) = await rig.trail.query(TrailQuery(kind="guard.entrance_revoked"))
    assert (event.subject_id, event.payload) == (minted.device_id, {"reason": "invite_cancelled"})
    assert rig.notifier.notices[-1].event_id == event.id
    assert rig.offboarder.offboarded == []


async def test_cancel_refuses_a_device_that_already_redeemed(rig: Enrolment) -> None:
    pending = await admitted(rig, DeviceStatus.PENDING)

    with pytest.raises(DeviceStatusConflictError):
        await cancel_invite(rig.deps, pending.id, "human")
    assert (await rig.store.get_device(pending.id)).status is DeviceStatus.PENDING
