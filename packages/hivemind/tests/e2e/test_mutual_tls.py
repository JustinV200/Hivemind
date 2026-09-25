"""End-to-end: mutual-TLS device certificates on ``hive serve``'s remote listener, on real sockets.

In ``lan`` and ``tunnel`` modes the remote listener completes a handshake only with a client
certificate from the Hive's own authority (ADR-0041). ``hive serve``'s own composition runs the
Hive Stand (a real Queen over a scripted provider, the Hive's SQLite file) with the remote listener
bound to a private address this machine really has, behind a server certificate from a throwaway
authority the laptop pins with ``--ca-file``; a second config directory is the laptop. Covered:
a laptop enrolled on loopback gets its certificate at approval, fetches it, moves to the remote
listener and runs a goal a Drone finishes; a laptop without a certificate is refused at the
handshake; after ``hive entrance revoke`` the laptop's next handshake is refused; a laptop that
could reach no enrolment listener is registered offline at the Hive Stand, imports the
certificate the operator wrote, and reads its inbox over mutual TLS; and an operator who turns
mutual TLS on for ``vpn`` (off by default there) gets the same working flow.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "lan and tunnel".
    - builders.entrance.mtls for the address, the throwaway authority and the manifest.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.entrance.auth import PASSWORD
from builders.entrance.goals import goal_responder
from builders.entrance.mtls import PrivateAddress, mtls_manifest, private_address
from builders.entrance.stand import (
    Stand,
    Terminal,
    json_of,
    laptop_terminal,
    serving_stand,
    set_password,
)
from typer.testing import Result

from hivemind.cli.entrance import read_serve_record
from hivemind.entrance.runtime import HiveEntrance

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        private_address() is None, reason="this machine has no private IPv4 address to expose"
    ),
]

_STDIN = f"{PASSWORD}\n"
_HANDSHAKE_REFUSED = "did not answer"  # How the CLI reports a handshake the listener refused.


def _place() -> PrivateAddress:
    """The private address the remote listener binds (the module is skipped without one)."""
    place = private_address()
    assert place is not None
    return place


async def test_a_laptop_with_its_certificate_runs_a_goal_and_is_refused_without_or_revoked(
    tmp_path: Path,
) -> None:
    path, tls = mtls_manifest(tmp_path / "stand", _place())
    await set_password(path)
    laptop = laptop_terminal(tmp_path / "laptop")

    async with serving_stand(path, goal_responder()) as (stand, entrance):
        remote = _remote_origin(entrance)
        device_id = await _enrol_on_loopback(stand, laptop, "default")
        approved = await stand.entrance("approve", device_id, "--spend-cap", "20", "--yes")
        fetched = await laptop.hive("remote", "certificate", "fetch", "--password-stdin", **_IN)
        moved = await laptop.hive("remote", "set-url", remote, "--ca-file", str(tls.ca_path))
        ran = await laptop.hive(*_RUN, "write a haiku about bees", "--timeout", "60", **_IN)
        # A second device, approved but never given its certificate: refused at the handshake.
        bare_id = await _enrol_on_loopback(stand, laptop, "bare")
        await stand.entrance("approve", bare_id, "--spend-cap", "5", "--yes")
        await laptop.hive("remote", "set-url", remote, "--ca-file", str(tls.ca_path), *_BARE)
        bare = await laptop.hive(*_INBOX, *_BARE, **_IN)
        # A third holds its certificate: the rebuilt list must refuse the revoked one alone.
        spare_id = await _enrol_on_loopback(stand, laptop, "spare")
        await stand.entrance("approve", spare_id, "--spend-cap", "5", "--yes")
        await laptop.hive("remote", "certificate", "fetch", "--password-stdin", *_SPARE, **_IN)
        await laptop.hive("remote", "set-url", remote, "--ca-file", str(tls.ca_path), *_SPARE)
        revoked = await stand.entrance("revoke", device_id)
        after = await laptop.hive(*_INBOX, **_IN)
        spare = await laptop.hive(*_INBOX, *_SPARE, **_IN)
        tasks = await stand.terminal.hive("tasks", "list", "--manifest", str(path))

    assert "Client certificate: serial" in approved.output, approved.output
    assert fetched.exit_code == 0 and f"device {device_id}" in fetched.output, fetched.output
    assert moved.exit_code == 0, moved.output
    assert ran.exit_code == 0 and " finished at " in ran.output, ran.output
    assert "SUCCEEDED" in tasks.output, tasks.output
    assert bare.exit_code == 1 and _HANDSHAKE_REFUSED in bare.output, bare.output
    assert "hive remote certificate fetch" in bare.output
    assert revoked.exit_code == 0, revoked.output
    assert after.exit_code == 1 and _HANDSHAKE_REFUSED in after.output, after.output
    assert "may have been refused (revoked" in after.output
    assert spare.exit_code == 0 and _read(spare.output), spare.output


async def test_a_laptop_registered_offline_imports_its_certificate_and_reads_its_inbox(
    tmp_path: Path,
) -> None:
    path, tls = mtls_manifest(tmp_path / "stand", _place())
    await set_password(path)
    laptop = laptop_terminal(tmp_path / "laptop")
    request = tmp_path / "laptop.csr"
    certificate = tmp_path / "laptop.crt"

    async with serving_stand(path) as (stand, entrance):
        hive_id = stand.manifest.hive.id
        made = await laptop.hive(
            *("remote", "enrol", _remote_origin(entrance), "--offline", "--hive", hive_id),
            *("--name", "field laptop", "--ca-file", str(tls.ca_path), "--csr-out", str(request)),
        )
        public_key = made.output.split("--public-key ")[1].split()[0]
        registered = await stand.entrance(
            "register", "--name", "field laptop", "--public-key", public_key, "--csr", str(request)
        )
        device_id = registered.output.split("Registered ")[1].split()[0]
        approved = await stand.entrance(
            *("approve", device_id, "--spend-cap", "5", "--yes"),
            *("--certificate-out", str(certificate)),
        )
        imported = await laptop.hive("remote", "certificate", "import", str(certificate))
        inbox = await laptop.hive(*_INBOX, **_IN)

    assert made.exit_code == 0 and "nothing was sent" in made.output, made.output
    assert registered.exit_code == 0 and device_id.startswith("device_"), registered.output
    assert approved.exit_code == 0 and certificate.exists(), approved.output
    assert imported.exit_code == 0 and f"device {device_id}" in imported.output, imported.output
    assert inbox.exit_code == 0 and _read(inbox.output), inbox.output


async def test_mutual_tls_turned_on_for_vpn_admits_a_device_with_its_certificate(
    tmp_path: Path,
) -> None:
    # vpn asks for no certificate by default; this operator turns it on, and it works end to end.
    path, tls = mtls_manifest(
        tmp_path / "stand", _place(), expose="vpn", extra="mutual_tls = true\n"
    )
    await set_password(path)
    laptop = laptop_terminal(tmp_path / "laptop")

    async with serving_stand(path) as (stand, entrance):
        plan = await stand.entrance("status", "--json")
        device_id = await _enrol_on_loopback(stand, laptop, "default")
        await stand.entrance("approve", device_id, "--spend-cap", "5", "--yes")
        await laptop.hive("remote", "certificate", "fetch", "--password-stdin", **_IN)
        remote = _remote_origin(entrance)
        await laptop.hive("remote", "set-url", remote, "--ca-file", str(tls.ca_path))
        inbox = await laptop.hive(*_INBOX, **_IN)

    assert json_of(plan)["plan"]["mode"] == "vpn"
    assert inbox.exit_code == 0 and _read(inbox.output), inbox.output


_IN = {"stdin": _STDIN}  # The operator password, on stdin, for every command that logs in.
_RUN = ("run", "--remote")
_INBOX = ("inbox", "--remote", "--password-stdin")
_BARE = ("--profile", "bare")
_SPARE = ("--profile", "spare")


def _read(output: str) -> bool:
    """Whether an inbox listing was read: empty, or an Alarm (a heartbeat late on a busy host)."""
    return "Nothing waits on the human." in output or "alarm alarm_" in output


def _remote_origin(entrance: HiveEntrance) -> str:
    """The remote listener's origin, by the address its certificate names."""
    port = entrance.listeners.remote_port
    assert port is not None, "the remote listener must be serving"
    return f"https://{_place().address}:{port}"


async def _enrol_on_loopback(stand: Stand, laptop: Terminal, profile: str) -> str:
    """Invite at the Stand and enrol the laptop over loopback with the code; return the id."""
    invite = json_of(await stand.entrance("invite", "--device", profile, "--json"))
    record = read_serve_record(stand.db)
    assert record is not None
    enrolled: Result = await laptop.hive(
        *("remote", "enrol", record.origin, "--code", invite["code"], "--name", profile),
        *("--profile", profile),
    )
    assert enrolled.exit_code == 0, enrolled.output
    return str(invite["device_id"])
