"""Test hivemind.cli.entrance.enrolments: invite, pending, approve and deny over a real hive serve.

Each scenario runs ``hive serve``'s own composition on a loopback port and types the commands at
the Hive Stand, as the console device; the device being admitted redeems its invite with the CLI's
own Landing Board client, exactly as ``hive remote enrol`` does.

Fits into the Hive:
    Mirrors src/hivemind/cli/entrance/enrolments.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import typer
from builders.entrance.auth import PASSWORD
from builders.entrance.stand import (
    json_of,
    redeem_invite,
    serving_stand,
    set_password,
    stand_manifest,
)

from hivemind.cli.entrance.enrolments import parse_capabilities, parse_expiry
from hivemind.entrance.auth import key_fingerprint

_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


async def test_an_invite_is_printed_once_with_its_link_hive_id_enrol_line_and_qr(
    tmp_path: Path,
) -> None:
    path = stand_manifest(tmp_path)
    await set_password(path)

    async with serving_stand(path) as (stand, _entrance):
        shown = await stand.entrance("invite", "--device", "Ada's laptop")
        as_json = json_of(await stand.entrance("invite", "--device", "phone", "--json"))

    assert shown.exit_code == 0, shown.output
    assert f"Hive: {stand.manifest.hive.id}" in shown.output
    assert "Link: http://localhost:" in shown.output and "/enrol#code=" in shown.output
    assert f"--hive {stand.manifest.hive.id} --code " in shown.output
    assert "--name 'Ada'\"'\"'s laptop'" in shown.output
    assert "█" in shown.output or "▀" in shown.output  # The QR code's blocks.
    assert set(as_json) == {"device_id", "code", "url", "expires_at", "hive_id"}
    assert as_json["url"].endswith(f"#code={as_json['code']}")
    assert PASSWORD not in shown.output


async def test_a_waiting_device_is_shown_by_fingerprint_then_approved_on_the_operators_terms(
    tmp_path: Path,
) -> None:
    path = stand_manifest(tmp_path)
    await set_password(path)

    async with serving_stand(path) as (stand, _entrance):
        invite = json_of(await stand.entrance("invite", "--device", "laptop", "--json"))
        key = await redeem_invite(stand, invite["code"])
        pending = await stand.entrance("pending")
        declined = await stand.entrance(
            "approve", key.device_id, "--spend-cap", "3", stdin=f"{PASSWORD}\nn\n"
        )
        approved = await stand.entrance(
            *("approve", key.device_id, "--spend-cap", "3", "--expires", "30d"),
            *("--capabilities", "entrance:submit,observe", "--interactive", "--yes"),
        )
        listed = json_of(await stand.entrance("devices", "--json"))

    fingerprint = key_fingerprint(key.signer.public_key_bytes)
    assert fingerprint in pending.output and "says it is: laptop" in pending.output
    assert declined.exit_code == 1 and "was not approved" in declined.output
    assert approved.exit_code == 0, approved.output
    assert fingerprint in approved.output and "passkey backup" in approved.output
    [device] = [row for row in listed["devices"] if row["id"] == key.device_id]
    assert device["status"] == "APPROVED" and device["interactive"] is True
    assert device["capabilities"] == ["entrance:submit", "observe"]
    assert device["spend_cap_usd_per_day"] == 3.0
    lapses = datetime.fromisoformat(device["expires_at"]) - datetime.now(UTC)
    assert timedelta(days=29) < lapses <= timedelta(days=30)


async def test_deny_refuses_a_waiting_device_and_approve_refuses_one_not_waiting(
    tmp_path: Path,
) -> None:
    path = stand_manifest(tmp_path)
    await set_password(path)

    async with serving_stand(path) as (stand, _entrance):
        invite = json_of(await stand.entrance("invite", "--device", "phone", "--json"))
        key = await redeem_invite(stand, invite["code"], name="phone")
        denied = await stand.entrance("deny", key.device_id, "--reason", "not mine")
        late = await stand.entrance("approve", key.device_id, "--spend-cap", "1", "--yes")
        pending = await stand.entrance("pending")

    assert denied.exit_code == 0 and f"Denied {key.device_id}" in denied.output
    assert late.exit_code == 1 and "is not waiting for approval" in late.output
    assert "No device is waiting for approval." in pending.output


def test_capabilities_are_read_comma_separated_or_repeated() -> None:
    assert parse_capabilities(None) is None
    assert parse_capabilities(["entrance:submit, observe", "entrance:answer", " "]) == [
        "entrance:submit",
        "observe",
        "entrance:answer",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("90d", _NOW + timedelta(days=90)),
        ("12h", _NOW + timedelta(hours=12)),
        ("2w", _NOW + timedelta(weeks=2)),
        ("45m", _NOW + timedelta(minutes=45)),
        ("2027-01-01", datetime(2027, 1, 1, tzinfo=UTC)),
        ("2027-01-01T08:30:00+02:00", datetime(2027, 1, 1, 6, 30, tzinfo=UTC)),
    ],
)
def test_an_expiry_is_a_duration_from_now_or_a_moment(text: str, expected: datetime) -> None:
    assert parse_expiry(text, _NOW) == expected


@pytest.mark.parametrize("text", ["soon", "0d", "2020-01-01", "-3d"])
def test_an_expiry_that_is_not_in_the_future_or_not_readable_is_refused(text: str) -> None:
    with pytest.raises(typer.BadParameter):
        parse_expiry(text, _NOW)
