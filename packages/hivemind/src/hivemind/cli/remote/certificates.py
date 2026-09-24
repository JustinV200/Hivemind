"""Provide ``hive remote certificate fetch|import``: keep this device's mutual-TLS certificate.

A Hive whose remote listener demands client certificates (``lan`` and ``tunnel``, and ``vpn`` when
the operator turns mutual TLS on) admits this laptop only with the certificate its Hive signed at
approval (ADR-0033), and the laptop keeps it beside its profile (``profiles``), presenting it on
every call (``hivemind.cli.landing.certificate``). A laptop that enrolled over a listener it can
reach ``fetch``-es it from there once approved (``GET /v1/devices/me/certificate``, logged in as
the device). A laptop that could reach no enrolment listener (``hive remote enrol --offline``)
``import``-s the file the operator wrote at approval; the certificate names the device, which
completes the profile. Either way the certificate is checked before it is kept: it must certify
this device's own key, be current, and name the profile's device.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.remote``. ``app`` is added to ``hive remote`` as
    ``certificate`` by ``hivemind.cli.remote.commands``. Calls into ``hivemind.cli.landing``,
    ``hivemind.cli.remote.profiles`` and ``.session``, and the Landing Board's models.

Key invariants:
    - Only a certificate for this device's key and device id is ever kept.
    - Nothing here prints or writes the private key; the certificate is public.

See Also:
    - hivemind.entrance.routes.devices for the route a device fetches its certificate from.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from hivemind.cli.landing import (
    PASSWORD_STDIN,
    CertificateFacts,
    LandingError,
    SignedIn,
    carried_command,
    carried_flag,
    carried_text,
    certificate_facts,
    describe,
)
from hivemind.cli.remote.profiles import DEFAULT_PROFILE, ProfileStore
from hivemind.cli.remote.session import PROFILE, run_remote
from hivemind.common.errors import HiveMindError
from hivemind.entrance.models import CertificateView
from waggle.clock import SystemClock

CertificateCommand = carried_command(PROFILE, PASSWORD_STDIN)

app = typer.Typer(
    name="certificate",
    help="Keep this device's mutual-TLS client certificate: fetch it, or import it.",
    no_args_is_help=True,
)

__all__ = ["app"]


@app.command("fetch", cls=CertificateCommand)
def fetch_command(ctx: typer.Context) -> None:
    """Fetch this device's certificate from its Hive (after approval), and keep it."""
    profile_name = carried_text(ctx, PROFILE) or DEFAULT_PROFILE

    async def fetch(board: SignedIn) -> tuple[bytes, CertificateFacts]:
        """Read the device's own certificate and check it against the key it logged in with."""
        issued = await board.call("GET", "/v1/devices/me/certificate", None, CertificateView)
        pem = issued.certificate_pem.encode("ascii")
        signer = board.session.credential.key.signer
        return pem, certificate_facts(pem, signer, SystemClock().now())

    pem, facts = run_remote(
        "remote certificate fetch", profile_name, carried_flag(ctx, PASSWORD_STDIN), fetch
    )
    _keep(profile_name, pem, facts)


@app.command("import", cls=CertificateCommand)
def import_command(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="The certificate file the operator wrote (PEM).")],
) -> None:
    """Import the certificate the operator wrote at approval (a device enrolled offline)."""
    profile_name = carried_text(ctx, PROFILE) or DEFAULT_PROFILE
    store = ProfileStore.for_user()
    try:
        pem = path.read_bytes()
        profile = store.load(profile_name)
        signer = asyncio.run(store.signer(profile))
        facts = certificate_facts(pem, signer, SystemClock().now())
        # The certificate names the device: it completes an offline profile, or must match.
        if profile.device_id is None:
            store.update(profile.model_copy(update={"device_id": facts.device_id}))
        elif profile.device_id != facts.device_id:
            raise LandingError(
                f"That certificate is for device {facts.device_id}, not this profile's "
                f"{profile.device_id}."
            )
    except (HiveMindError, ValidationError, OSError, sqlite3.Error) as exc:
        _refuse("remote certificate import", exc)
    _keep(profile_name, pem, facts)


def _keep(profile_name: str, pem: bytes, facts: CertificateFacts) -> None:
    """Keep a checked certificate beside its profile and say what it is."""
    try:
        path = ProfileStore.for_user().save_certificate(profile_name, pem)
    except (HiveMindError, OSError) as exc:
        _refuse("remote certificate", exc)
    typer.echo(f"Kept the client certificate of device {facts.device_id} at {path}.")
    until = facts.not_after.isoformat(timespec="seconds")
    typer.echo(f"  serial {facts.serial}, fingerprint {facts.fingerprint}, until {until}")
    typer.echo("Every call to the Hive presents it from now on.")


def _refuse(verb: str, exc: Exception) -> NoReturn:
    """Print why ``hive <verb>`` could not act, and exit 1."""
    typer.echo(f"hive {verb} refused: {describe(exc)}", err=True)
    raise typer.Exit(code=1) from exc
