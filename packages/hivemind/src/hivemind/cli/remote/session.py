"""Log this laptop in to a remote Hive as its enrolled device, and turn refusals into one line.

``hive run --remote`` and ``hive inbox --remote`` act as the laptop's enrolled device (ADR-0041):
``remote_session`` reads the profile, opens its device key from the laptop's secret store,
connects to the profile's Entrance (TLS verified against the system's store or the profile's own
CA, presenting the device's client certificate when it holds one, which a listener under mutual
TLS demands), and logs in with the key plus the operator's password, logging out however the
block ends.
``run_remote`` wraps a command's work in it: the password from a hidden prompt or
``--password-stdin``, the whole conversation in one event loop, and every refusal as one stderr
line and exit 1 (a device pending approval, locked or revoked, a wrong password, a request held
for a person's confirmation, a view the device may not read). ``REMOTE`` and ``PROFILE`` are the
options ``hive run`` and ``hive inbox`` carry to choose this path.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.remote``. Used by
    ``hivemind.cli.remote.commands`` (for ``hive run`` and ``hive inbox``). Calls into
    ``hivemind.cli.landing`` and ``hivemind.cli.remote.profiles``.

Key invariants:
    - The session token, the password and the unwrapped key live only for one command.
    - Every failure a command could meet on purpose ends as one line, never a traceback.

See Also:
    - hivemind.cli.landing.client for the login and step-up.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import NoReturn

import typer
from pydantic import SecretStr, ValidationError

from hivemind.cli.landing import (
    CarriedOption,
    ClientCertificate,
    DeviceKey,
    EntranceAddress,
    LandingClient,
    LandingError,
    SignedIn,
    describe,
    open_http,
    read_password,
    signed_in,
)
from hivemind.cli.remote.profiles import DEFAULT_PROFILE, ProfileStore, RemoteProfile
from hivemind.common.errors import HiveMindError
from waggle.clock import Clock, SystemClock
from waggle.signing import Ed25519Signer

REMOTE = CarriedOption(
    key="hivemind.cli.remote",
    decls=("--remote",),
    help="Act on a remote Hive through its Entrance, as this device (hive remote enrol).",
    is_flag=True,
)
PROFILE = CarriedOption(
    key="hivemind.cli.remote.profile",
    decls=("--profile",),
    help=f"The remote Hive profile to use with --remote (default: {DEFAULT_PROFILE}).",
    default=DEFAULT_PROFILE,
)

__all__ = ["PROFILE", "REMOTE", "remote_session", "run_remote"]


@asynccontextmanager
async def remote_session(
    store: ProfileStore, profile_name: str, password: SecretStr, clock: Clock
) -> AsyncIterator[SignedIn]:
    """Log the profile's device in to its Hive for the block; log out on the way out.

    Args:
        store: This user's remote profiles and keys.
        profile_name: Which Hive.
        password: The operator's password.
        clock: Stamps every signed request.

    Yields:
        The device, logged in.

    Raises:
        LandingError: No such profile or key, a profile still waiting for its certificate, or
            the Entrance refused or did not answer.
    """
    profile = store.load(profile_name)
    if profile.device_id is None:
        raise LandingError(
            f"Profile {profile_name!r} was enrolled offline and waits for its certificate: "
            f"hive remote certificate import FILE --profile {profile_name}."
        )
    signer = await store.signer(profile)
    address = _address(store, profile, signer)
    async with open_http(address) as http:
        client = LandingClient(http, address, profile.hive_id, clock)
        async with signed_in(client, DeviceKey(profile.device_id, signer), password) as board:
            yield board


def _address(store: ProfileStore, profile: RemoteProfile, signer: Ed25519Signer) -> EntranceAddress:
    """The profile's Entrance, presenting the device's client certificate when it holds one."""
    address = profile.address()
    pem = store.certificate(profile.name)
    if pem is None:
        return address
    return dataclasses.replace(address, client=ClientCertificate(pem, signer))


def run_remote[T](
    verb: str, profile_name: str, from_stdin: bool, work: Callable[[SignedIn], Awaitable[T]]
) -> T:
    """Run ``work`` logged in as the profile's device, or print why not and exit 1.

    Args:
        verb: The command, for the refusal line (``"run --remote"``).
        profile_name: Which Hive.
        from_stdin: ``--password-stdin`` was given.
        work: What to do once logged in.

    Returns:
        What ``work`` returned.

    Raises:
        typer.Exit: 1 after one stderr line, for any refusal.
    """
    store = ProfileStore.for_user()
    try:
        # Loaded before the password is asked for: no profile means nothing to type it for.
        store.load(profile_name)
        password = read_password(from_stdin)

        async def logged_in() -> T:
            """Open the session and run the work inside it."""
            async with remote_session(store, profile_name, password, SystemClock()) as board:
                return await work(board)

        return asyncio.run(logged_in())
    except (HiveMindError, ValidationError, OSError, sqlite3.Error) as exc:
        # Typed failures only: every refusal raised on purpose, a value the contract refused, a
        # file or socket that failed.
        _refuse(verb, exc)


def _refuse(verb: str, exc: Exception) -> NoReturn:
    """Print why ``hive <verb>`` could not act, naming a refused value by its field only."""
    typer.echo(f"hive {verb} refused: {describe(exc)}", err=True)
    raise typer.Exit(code=1) from exc
