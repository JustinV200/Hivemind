"""Test hivemind.cli.remote.certificates: a laptop keeps its mutual-TLS certificate, and uses it.

A Hive Stand in tunnel mode (``hive serve``'s own composition; the remote listener on loopback
behind TLS and mutual TLS, a stand-in tunnel client) lets a laptop's terminal do the whole thing
anywhere: enrol over loopback with the code, be approved, be refused at the remote listener's
handshake (``set-url`` and ``--ca-file``) while it holds no certificate, ``fetch`` the one issued
from its request back on loopback, read its inbox on the remote listener, and be refused at the
handshake again once the operator revokes it. ``import`` keeps a certificate the
operator carried to a laptop enrolled offline only when it certifies the laptop's own key, and
once the profile names a device, only one for that device. A loopback-only Hive issues no
certificate, and ``fetch`` says so.

Fits into the Hive:
    Mirrors src/hivemind/cli/remote/certificates.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from builders.entrance.auth import PASSWORD
from builders.entrance.mtls import tunnel_manifest
from builders.entrance.stand import (
    Stand,
    Terminal,
    json_of,
    laptop_terminal,
    serving_stand,
    set_password,
    stand_manifest,
)

from hivemind.cli.entrance import read_serve_record
from hivemind.cli.landing import certificate_request
from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.expose import issue_client_certificate, load_or_create_authority
from waggle.clock import SystemClock
from waggle.ids import HiveId, new_device_id
from waggle.signing import Ed25519Signer

_STDIN = f"{PASSWORD}\n"
_IN = {"stdin": _STDIN}  # The operator password, for every command that logs in.
_INBOX = ("inbox", "--remote", "--password-stdin")
_HIVE = HiveId("hive_01DXF6DT00S8CWQEAHWB40R349")  # The Hive an offline laptop names.


def _read(output: str) -> bool:
    """Whether an inbox listing was read: empty, or an Alarm (a heartbeat late on a busy host)."""
    return "Nothing waits on the human." in output or "alarm alarm_" in output


async def _enrol_on_loopback(stand: Stand, laptop: Terminal) -> str:
    """Invite, enrol the laptop with the code over loopback, approve it; return its id."""
    invite = json_of(await stand.entrance("invite", "--device", "laptop", "--json"))
    enrolled = await laptop.hive(
        "remote", "enrol", _origin(stand), "--code", invite["code"], "--name", "laptop"
    )
    assert enrolled.exit_code == 0, enrolled.output
    approved = await stand.entrance("approve", invite["device_id"], "--spend-cap", "5", "--yes")
    assert approved.exit_code == 0, approved.output
    return str(invite["device_id"])


async def test_a_laptop_is_admitted_with_its_certificate_only_and_refused_once_revoked(
    tmp_path: Path,
) -> None:
    path, tls = tunnel_manifest(tmp_path / "stand")
    await set_password(path)
    laptop = laptop_terminal(tmp_path / "laptop")

    async with serving_stand(path) as (stand, entrance):
        device_id = await _enrol_on_loopback(stand, laptop)
        remote = f"https://127.0.0.1:{entrance.listeners.remote_port}"
        await laptop.hive("remote", "set-url", remote, "--ca-file", str(tls.ca_path))
        bare = await laptop.hive(*_INBOX, **_IN)
        # Fetched over the listener it enrolled on: the remote one admits no certificate-less call.
        await laptop.hive("remote", "set-url", _origin(stand))
        fetched = await laptop.hive("remote", "certificate", "fetch", "--password-stdin", **_IN)
        moved = await laptop.hive("remote", "set-url", remote)
        inbox = await laptop.hive(*_INBOX, **_IN)
        profiles = await laptop.hive("remote", "profiles")
        revoked = await stand.entrance("revoke", device_id)
        refused = await laptop.hive(*_INBOX, **_IN)

    assert bare.exit_code == 1 and "hive remote certificate fetch" in bare.output, bare.output
    assert fetched.exit_code == 0 and f"device {device_id}" in fetched.output, fetched.output
    assert moved.exit_code == 0 and remote in moved.output, moved.output
    assert inbox.exit_code == 0 and _read(inbox.output), inbox.output
    assert "a client certificate" in profiles.output, profiles.output
    assert revoked.exit_code == 0, revoked.output
    assert refused.exit_code == 1 and "may have been refused (revoked" in refused.output


def _origin(stand: Stand) -> str:
    """The stand's loopback listener, where a laptop on the same host enrols."""
    record = read_serve_record(stand.db)
    assert record is not None
    return record.origin


async def test_import_keeps_only_a_certificate_for_this_devices_key_and_device(
    tmp_path: Path,
) -> None:
    laptop, request = laptop_terminal(tmp_path / "laptop"), tmp_path / "laptop.csr"
    made = await laptop.hive(
        *("remote", "enrol", "https://192.0.2.2:8711", "--offline", "--hive", _HIVE),
        *("--name", "field laptop", "--csr-out", str(request)),
    )
    # The operator's side: the Hive's authority signs what the laptop's request asked for.
    now, clock = datetime.now(UTC), SystemClock()
    authority = await load_or_create_authority(MemorySecretStore(), _HIVE, now)
    device_id, other_id = new_device_id(clock), new_device_id(clock)
    stranger = certificate_request(Ed25519Signer.generate(), "stranger").encode("ascii")
    written = {
        "foreign": issue_client_certificate(authority, stranger, device_id, now).pem,
        "ours": issue_client_certificate(authority, request.read_bytes(), device_id, now).pem,
        "renamed": issue_client_certificate(authority, request.read_bytes(), other_id, now).pem,
    }
    for name, pem in written.items():
        (tmp_path / f"{name}.crt").write_bytes(pem)

    imports = {
        name: await laptop.hive("remote", "certificate", "import", str(tmp_path / f"{name}.crt"))
        for name in ("foreign", "ours", "renamed")
    }

    assert made.exit_code == 0, made.output
    assert imports["foreign"].exit_code == 1 and "another key" in imports["foreign"].output
    assert imports["ours"].exit_code == 0 and f"device {device_id}" in imports["ours"].output
    assert imports["renamed"].exit_code == 1
    assert f"for device {other_id}, not this profile's {device_id}" in imports["renamed"].output


async def test_fetch_on_a_hive_that_issued_nothing_says_so(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)
    laptop = laptop_terminal(tmp_path / "laptop")

    async with serving_stand(path) as (stand, _entrance):
        device_id = await _enrol_on_loopback(stand, laptop)
        fetched = await laptop.hive("remote", "certificate", "fetch", "--password-stdin", **_IN)

    assert fetched.exit_code == 1
    assert f"Device {device_id} holds no client certificate" in fetched.output, fetched.output
