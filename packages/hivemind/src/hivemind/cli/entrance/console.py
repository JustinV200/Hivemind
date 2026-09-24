"""Act as the Hive Stand's console: over a running serve's loopback listener, or offline.

On the Hive Stand (the machine the Queen, the orchestrator, runs on) ``hive entrance ...`` is the
operator at the keyboard, and the Hive Entrance knows that operator only as the **console
device** (ADR-0033): an Ed25519 key wrapped under the operator password in the Hive's secret store,
recorded APPROVED and loopback-bound. ``console_session`` is how every administering command
enters: it finds the running ``hive serve`` by its serve record, opens the console key with the
password, looks up the console's record for that key, and logs in over the loopback listener
exactly as any device would, so every decision goes through the Landing Board's own routes and
leaves the same trail. ``offline_console`` is the other door, for the two operations ADR-0033 runs
with ``hive serve`` stopped (``--reset``, ``unlock --console``): it holds the serve lock for the
block, so no serve can start meanwhile, and hands over the Entrance tables directly.
``run_console`` and ``run_offline`` wrap a command's work in either, turning every refusal into one
stderr line and exit 1 (codingrules 10: a command body is where errors become output).

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.entrance``. Used by every ``hive entrance``
    command. Calls into ``hivemind.cli.landing`` (the device client), ``hivemind.cli.stores``
    (the manifest), ``hivemind.entrance.enrol`` (the console key and record), the Entrance
    tables, the secret store and ``hivemind.cli.entrance.serving``.

Key invariants:
    - A console command never writes the Entrance tables itself; only an offline step does, and
      only while it holds the serve lock.
    - The unwrapped console key and the password live only for the command, in memory.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the console
      device and the offline operations.
    - hivemind.cli.entrance.serving for the serve lock and record.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import typer
from pydantic import SecretStr, ValidationError

from hivemind.cli.entrance.serving import hold_serve_lock, read_serve_record
from hivemind.cli.landing import (
    PASSWORD_STDIN,
    CarriedOption,
    DeviceKey,
    LandingClient,
    LandingError,
    SignedIn,
    carried_command,
    carried_flag,
    carried_path,
    describe,
    entrance_address,
    open_http,
    read_password,
    signed_in,
)
from hivemind.cli.stores import DEFAULT_MANIFEST, load_manifest_or_exit
from hivemind.common.errors import HiveMindError
from hivemind.common.secrets import FileSecretStore
from hivemind.common.sqlite import connect
from hivemind.entrance.auth import PasswordHasher
from hivemind.entrance.enrol import (
    OPERATOR_ACTOR,
    ConsoleDeps,
    DeviceStatus,
    EntranceIdentity,
    console_record,
    unlock_console_key,
)
from hivemind.entrance.store import SqliteEntranceStore
from hivemind.manifest import HiveManifest
from hivemind.pheromone import apply_pheromone_migrations
from waggle.clock import Clock, SystemClock
from waggle.signing import Ed25519Signer

OFFLINE_HOLDER = "an offline hive entrance step"  # What the serve lock says while one runs.
MANIFEST = CarriedOption(
    key="hivemind.cli.entrance.manifest",
    decls=("--manifest",),
    help=f"The Hive Manifest TOML file (default: {DEFAULT_MANIFEST}).",
    default=str(DEFAULT_MANIFEST),
)
YES = CarriedOption(
    key="hivemind.cli.entrance.yes",
    decls=("--yes",),
    help="Do not ask for confirmation.",
    is_flag=True,
)
# Every console command: its own options, then the manifest and --password-stdin.
ConsoleCommand = carried_command(MANIFEST, PASSWORD_STDIN)

__all__ = [
    "MANIFEST",
    "OFFLINE_HOLDER",
    "YES",
    "ConsoleCommand",
    "ConsoleUnavailableError",
    "Stand",
    "console_session",
    "entrance_tables",
    "offline_console",
    "refusing",
    "run_console",
    "run_offline",
]


class ConsoleUnavailableError(LandingError):
    """Raise when the console cannot act: no ``hive serve`` running, or the console is locked."""


@dataclass(frozen=True, slots=True)
class Stand:
    """What one ``hive entrance`` command acts with: the loaded manifest and the password.

    Attributes:
        manifest: The Hive Manifest, loaded.
        password: The operator password, held as a secret for this command only.
    """

    manifest: HiveManifest
    password: SecretStr

    @property
    def db(self) -> Path:
        """The Hive's database file, resolved."""
        return self.manifest.resolve_path(self.manifest.hive.db)

    @property
    def secrets(self) -> FileSecretStore:
        """The Hive's secret store, where the console key lives wrapped."""
        return FileSecretStore(self.manifest.resolve_path(self.manifest.hive.secrets_dir))


def run_console[T](
    ctx: typer.Context, verb: str, work: Callable[[SignedIn, Stand], Awaitable[T]]
) -> T:
    """Run ``work`` as the console over the running serve's loopback listener, or exit 1.

    Args:
        ctx: The command's context, carrying ``--manifest`` and ``--password-stdin``.
        verb: The command's name, for the refusal line (``"approve"``).
        work: What to do once logged in.

    Returns:
        What ``work`` returned.

    Raises:
        typer.Exit: 1 with one stderr line when anything refused; 2 for a bad manifest.
    """
    stand = _stand(ctx, verb)

    async def logged_in() -> T:
        """Open the console session and run the work inside it."""
        async with console_session(stand, SystemClock()) as board:
            return await work(board, stand)

    return refusing(verb, lambda: asyncio.run(logged_in()))


def run_offline[T](
    ctx: typer.Context, verb: str, work: Callable[[ConsoleDeps, Stand], Awaitable[T]]
) -> T:
    """Run ``work`` on the Entrance tables directly, holding the serve lock, or exit 1.

    Args:
        ctx: The command's context, carrying ``--manifest`` and ``--password-stdin``.
        verb: The command's name, for the refusal line.
        work: What to do with the tables, the secret store and the password.

    Returns:
        What ``work`` returned.

    Raises:
        typer.Exit: 1 with one stderr line when ``hive serve`` runs or anything refused.
    """
    stand = _stand(ctx, verb)

    async def held() -> T:
        """Open the tables under the serve lock and run the work."""
        async with offline_console(stand.manifest, SystemClock()) as deps:
            return await work(deps, stand)

    return refusing(verb, lambda: asyncio.run(held()))


@asynccontextmanager
async def console_session(stand: Stand, clock: Clock) -> AsyncIterator[SignedIn]:
    """Log the console in on the running serve's loopback listener for the block.

    Args:
        stand: The manifest and the operator password.
        clock: Stamps every signed request.

    Yields:
        The console, logged in; logged out when the block exits.

    Raises:
        ConsoleUnavailableError: No ``hive serve`` published its listener, or the console is
            locked.
        KeyUnwrapError: The password does not open the console key.
        OperatorNotInitialisedError: There is no console yet.
        LandingRefusedError: The login was refused.
        EntranceUnreachableError: The published listener does not answer.
    """
    record = read_serve_record(stand.db)
    if record is None:
        raise ConsoleUnavailableError(
            "hive serve is not running for this Hive; the console acts through its loopback "
            "listener, so start it first (hive serve --manifest ...)."
        )
    # Latency: one Argon2id derivation (about 0.1 s) to open the key, then one local read.
    signer = await unlock_console_key(
        stand.secrets, PasswordHasher(), stand.password.get_secret_value()
    )
    console_id = await _console_id(stand, signer, clock)
    address = entrance_address(record.origin)
    async with open_http(address) as http:
        client = LandingClient(http, address, stand.manifest.hive.id, clock)
        async with signed_in(client, DeviceKey(console_id, signer), stand.password) as board:
            yield board


@asynccontextmanager
async def offline_console(manifest: HiveManifest, clock: Clock) -> AsyncIterator[ConsoleDeps]:
    """Hold the serve lock and open the Entrance tables for an offline step.

    Args:
        manifest: The Hive Manifest, loaded.
        clock: Stamps every record and event.

    Yields:
        What the Entrance's console operations run on, recording as the operator (``human``).

    Raises:
        HiveBusyError: ``hive serve`` (or another offline step) holds the Hive.
    """
    db = manifest.resolve_path(manifest.hive.db)
    # Held before anything is opened or asked for: a running serve refuses the step at once.
    with hold_serve_lock(db, OFFLINE_HOLDER):
        async with entrance_tables(manifest, clock) as deps:
            yield deps


@asynccontextmanager
async def entrance_tables(manifest: HiveManifest, clock: Clock) -> AsyncIterator[ConsoleDeps]:
    """Open the Entrance tables and the secret store as the operator's console operations need.

    Args:
        manifest: The Hive Manifest, loaded.
        clock: Stamps every record and event.

    Yields:
        The tables (one connection, closed on exit), the secret store, a hasher and the identity
        events carry, recorded as the operator (``human``).
    """
    connection = connect(manifest.resolve_path(manifest.hive.db))
    try:
        # The Entrance tables record on the trail, so its tables come first on a fresh Hive (the
        # operator may set the password before the Hive ever ran). Latency: local migrations,
        # a no-op once applied.
        await asyncio.to_thread(apply_pheromone_migrations, connection, clock)
        store = await SqliteEntranceStore.create(connection, clock)
        identity = EntranceIdentity(
            hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor=OPERATOR_ACTOR
        )
        secrets = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
        yield ConsoleDeps(store, secrets, PasswordHasher(), clock, identity)
    finally:
        connection.close()


async def _console_id(stand: Stand, signer: Ed25519Signer, clock: Clock) -> str:
    """Find the console's device id for the key the password opened; refuse a locked console."""
    connection = connect(stand.db)
    try:
        # Latency: one local read of the device table; a read, never a write, while serve runs.
        store = await SqliteEntranceStore.create(connection, clock)
        console = await console_record(store, signer)
    finally:
        connection.close()
    if console.status is DeviceStatus.LOCKED:
        raise ConsoleUnavailableError(
            "The Hive Stand console is locked; stop hive serve, then run hive entrance unlock "
            "--console with the operator password."
        )
    return str(console.id)


def _stand(ctx: typer.Context, verb: str) -> Stand:
    """Load the manifest and read the password, or exit: 2 for a bad manifest, 1 otherwise."""
    manifest = load_manifest_or_exit(carried_path(ctx, MANIFEST) or DEFAULT_MANIFEST)
    try:
        password = read_password(carried_flag(ctx, PASSWORD_STDIN))
    except LandingError as exc:
        _refuse(verb, exc)
    return Stand(manifest, password)


def refusing[T](verb: str, run: Callable[[], T]) -> T:
    """Run ``run``; turn every typed refusal into one stderr line and exit 1.

    Args:
        verb: The command's name after ``hive entrance``, for the refusal line.
        run: The command's work.

    Returns:
        What ``run`` returned.

    Raises:
        typer.Exit: 1, after one stderr line, for any refusal the work raised on purpose.
    """
    try:
        return run()
    except (HiveMindError, ValidationError, OSError, sqlite3.Error) as exc:
        # Typed failures only: a HiveMindError is every refusal the Entrance or the CLI raises on
        # purpose, a ValidationError a value the contract refused, the rest a file, a socket or
        # the database.
        _refuse(verb, exc)


def _refuse(verb: str, exc: Exception) -> NoReturn:
    """Print why ``hive entrance <verb>`` could not act, and exit 1."""
    typer.echo(f"hive entrance {verb} refused: {describe(exc)}", err=True)
    raise typer.Exit(code=1) from exc
