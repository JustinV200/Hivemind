"""Assemble ``hive entrance``: every command that administers the Hive Entrance from the Hive Stand.

The Hive Entrance is the Hive's one HTTP door (ADR-0032), and on the Hive Stand (the machine the
Queen, the orchestrator, runs on) ``hive entrance`` is how the operator keeps it: the password
(``operator``), who may come in (``invite``, ``register``, ``pending``, ``approve``, ``deny``,
``devices``, ``revoke``, ``steward``, ``unlock``) and how wide the door is (``reduce``, ``open``,
``status``, ``expose``). Every command that decides something acts as the console device over the
loopback listener of a running ``hive serve`` (``hivemind.cli.entrance.console``); only ``operator
password`` and ``unlock --console`` touch the Entrance tables directly, and ``--reset`` and ``unlock
--console`` only while ``hive serve`` is stopped. This module only registers the commands; each
lives in the module named for what it keeps.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.entrance``. Its ``app`` is added to the root
    ``hive`` application by ``hivemind.cli.app``. Calls into this package's command modules only.

Key invariants:
    - Every console command is registered with the class that carries ``--manifest`` and
      ``--password-stdin`` (``ConsoleCommand``, or ``ApproveCommand`` for ``approve``).

See Also:
    - .claude/roadmap.md step 10.8 for the command list.
"""

from __future__ import annotations

import typer

from hivemind.cli.entrance.console import ConsoleCommand
from hivemind.cli.entrance.devices import (
    devices_command,
    revoke_command,
    steward_command,
    unlock_command,
)
from hivemind.cli.entrance.door import (
    expose_command,
    open_command,
    reduce_command,
    status_command,
)
from hivemind.cli.entrance.enrolments import (
    ApproveCommand,
    approve_command,
    deny_command,
    invite_command,
    pending_command,
    register_command,
)
from hivemind.cli.entrance.operators import app as operator_app

__all__ = ["app"]

app = typer.Typer(
    name="entrance",
    help="Keep the Hive Entrance from the Hive Stand: the password, the devices, the door.",
    no_args_is_help=True,
)

# The operator's password (and the refused second operator).
app.add_typer(operator_app, name="operator")

# Who may come in: every decision is the console device on the loopback listener.
app.command("invite", cls=ConsoleCommand)(invite_command)
app.command("register", cls=ConsoleCommand)(register_command)
app.command("pending", cls=ConsoleCommand)(pending_command)
app.command("approve", cls=ApproveCommand)(approve_command)
app.command("deny", cls=ConsoleCommand)(deny_command)
app.command("devices", cls=ConsoleCommand)(devices_command)
app.command("revoke", cls=ConsoleCommand)(revoke_command)
app.command("steward", cls=ConsoleCommand)(steward_command)
app.command("unlock", cls=ConsoleCommand)(unlock_command)

# How wide the door is; expose alone needs no password and starts nothing.
app.command("reduce", cls=ConsoleCommand)(reduce_command)
app.command("open", cls=ConsoleCommand)(open_command)
app.command("status", cls=ConsoleCommand)(status_command)
app.command("expose")(expose_command)
