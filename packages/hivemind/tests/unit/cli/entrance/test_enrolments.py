"""Test hivemind.cli.entrance.enrolments: invite, register, pending, approve, deny on hive serve.

Each scenario runs ``hive serve``'s own composition on a loopback port and types the commands at
the Hive Stand, as the console device; the device being admitted redeems its invite with the CLI's
own Landing Board client, exactly as ``hive remote enrol`` does. Where the Hive runs its own
certificate authority (a stand in tunnel mode: TLS and mutual TLS on loopback), a device the
operator registers offline is approved with its certificate written owner-only for the device to
import, and a browser's approval writes its PKCS#12 bundle owner-only and prints the passphrase
once; a bundle with nowhere to go stops the approval before anything is issued.

Fits into the Hive:
    Mirrors src/hivemind/cli/entrance/enrolments.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import typer
from builders.entrance.auth import PASSWORD
from builders.entrance.landing import LandingClient as BrowserClient
from builders.entrance.mtls import tunnel_manifest
from builders.entrance.stand import (
    Stand,
    json_of,
    redeem_invite,
    serving_stand,
    set_password,
    stand_manifest,
)
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from hivemind.cli.entrance.enrolments import parse_capabilities, parse_expiry
from hivemind.cli.landing import certificate_facts, certificate_request
from hivemind.entrance.auth import SoftPasskey, key_fingerprint
from hivemind.entrance.runtime import HiveEntrance
from waggle.clock import SystemClock
from waggle.signing import Ed25519Signer

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


async def test_a_device_registered_offline_is_approved_with_its_certificate_written_out(
    tmp_path: Path,
) -> None:
    path, _tls = tunnel_manifest(tmp_path / "stand")
    await set_password(path)
    signer, request = Ed25519Signer.generate(), tmp_path / "laptop.csr"
    request.write_text(certificate_request(signer, "field laptop"), encoding="ascii")
    certificate = tmp_path / "laptop.crt"

    async with serving_stand(path) as (stand, _entrance):
        registered = await stand.entrance(
            *("register", "--name", "field laptop", "--csr", str(request)),
            *("--public-key", signer.public_key_bytes.hex()),
        )
        device_id = registered.output.split("Registered ")[1].split()[0]
        pending = await stand.entrance("pending")
        approved = await stand.entrance(
            *("approve", device_id, "--spend-cap", "5", "--yes"),
            *("--certificate-out", str(certificate)),
        )

    assert registered.exit_code == 0, registered.output
    assert f"hive entrance approve {device_id}" in registered.output
    assert "client certificate: requested" in pending.output, pending.output
    assert approved.exit_code == 0 and "Client certificate: serial" in approved.output
    assert f"hive remote certificate import {certificate}" in approved.output
    facts = certificate_facts(certificate.read_bytes(), signer, datetime.now(UTC))
    assert facts.device_id == device_id and facts.serial in approved.output
    assert _owner_only(certificate)


async def test_a_browser_approved_under_mutual_tls_gets_its_bundle_and_passphrase_once(
    tmp_path: Path,
) -> None:
    path, _tls = tunnel_manifest(tmp_path / "stand")
    await set_password(path)
    bundle, nowhere = tmp_path / "phone.p12", tmp_path / "missing" / "phone.p12"

    async with serving_stand(path) as (stand, entrance):
        device_id = await _enrol_browser(stand, entrance)
        stopped = await stand.entrance(
            "approve", device_id, "--spend-cap", "5", "--yes", "--bundle-out", str(nowhere)
        )
        approved = await stand.entrance(
            "approve", device_id, "--spend-cap", "5", "--yes", "--bundle-out", str(bundle)
        )

    # Refused as a bad --bundle-out before anything was issued: the device was still waiting.
    assert stopped.exit_code == 2 and "Invalid value" in stopped.output, stopped.output
    assert approved.exit_code == 0, approved.output
    passphrase = approved.output.split("shown this once: ")[1].split()[0]
    key, certificate, _ = pkcs12.load_key_and_certificates(bundle.read_bytes(), passphrase.encode())
    assert key is not None and certificate is not None
    named = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    assert named == device_id
    assert approved.output.count(passphrase) == 1
    assert _owner_only(bundle)


async def _enrol_browser(stand: Stand, entrance: HiveEntrance) -> str:
    """Redeem an invite as a browser with a passkey on the loopback listener; return its id."""
    invite = json_of(await stand.entrance("invite", "--device", "phone", "--json"))
    port = entrance.listeners.loopback_port
    # The browser's page is served from loopback by name, which is the passkey's origin.
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as http:
        browser = BrowserClient(http, stand.manifest.hive.id, SystemClock())
        return await browser.enrol_browser(invite["code"], SoftPasskey(f"http://localhost:{port}"))


def _owner_only(path: Path) -> bool:
    """Whether only the file's owner may read it (always true where POSIX bits do not apply)."""
    return sys.platform == "win32" or stat.S_IMODE(path.stat().st_mode) == 0o600


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
