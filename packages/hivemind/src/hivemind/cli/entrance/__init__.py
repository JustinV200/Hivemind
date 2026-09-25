"""Keep the Hive Entrance from the Hive Stand: ``hive entrance ...``, and the serve lock.

The Hive Entrance is the Hive's one HTTP door, and on the Hive Stand (the machine the Queen, the
orchestrator, runs on) the operator keeps it with ``hive entrance``: the operator password, device
enrolment and standing, and the door's mode and exposure (roadmap 10.8, ADR-0041). Every command
that decides something acts as the Hive Stand's console device over the loopback listener of a
running ``hive serve``, logging in with the console key the password opens, so every decision is
the Landing Board's own route and leaves the same trail as any device's; only the operator's
password and the console's offline unlock touch the Entrance tables directly. This package also
holds the serve lock and record (``serving``), by which ``hive serve`` stays one per Hive and the
console commands find its listener.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli``. ``app`` is registered on the root ``hive``
    application by ``hivemind.cli.app``; ``serving`` is also used by ``hivemind.cli.compose.
    entrance``. Calls into ``hivemind.cli.landing``, ``hivemind.entrance`` and the Hive's stores.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No command here writes the Entrance tables while ``hive serve`` could be relying on what it
      holds in memory: those steps hold the serve lock.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md.
    - hivemind.cli.landing for the device client the console uses.

Public API:
    - app: the ``hive entrance`` typer group (group).
    - console_session, offline_console, entrance_tables, run_console, run_offline, refusing,
      Stand, ConsoleCommand, ConsoleUnavailableError, MANIFEST, YES, OFFLINE_HOLDER: acting as
      the console (console).
    - hold_serve_lock, publish_serve_record, read_serve_record, ServeRecord, HiveBusyError,
      SERVE_HOLDER, LOCK_SUFFIX, RECORD_SUFFIX: the serve lock and record (serving).
    - device_lines, device_row, devices_table, invite_lines, stamp: what is printed (render).
"""

from hivemind.cli.entrance.console import (
    MANIFEST,
    OFFLINE_HOLDER,
    YES,
    ConsoleCommand,
    ConsoleUnavailableError,
    Stand,
    console_session,
    entrance_tables,
    offline_console,
    refusing,
    run_console,
    run_offline,
)
from hivemind.cli.entrance.group import app
from hivemind.cli.entrance.render import (
    device_lines,
    device_row,
    devices_table,
    invite_lines,
    stamp,
)
from hivemind.cli.entrance.serving import (
    LOCK_SUFFIX,
    RECORD_SUFFIX,
    SERVE_HOLDER,
    HiveBusyError,
    ServeRecord,
    hold_serve_lock,
    publish_serve_record,
    read_serve_record,
)

__all__ = [
    "LOCK_SUFFIX",
    "MANIFEST",
    "OFFLINE_HOLDER",
    "RECORD_SUFFIX",
    "SERVE_HOLDER",
    "YES",
    "ConsoleCommand",
    "ConsoleUnavailableError",
    "HiveBusyError",
    "ServeRecord",
    "Stand",
    "app",
    "console_session",
    "device_lines",
    "device_row",
    "devices_table",
    "entrance_tables",
    "hold_serve_lock",
    "invite_lines",
    "offline_console",
    "publish_serve_record",
    "read_serve_record",
    "refusing",
    "run_console",
    "run_offline",
    "stamp",
]
