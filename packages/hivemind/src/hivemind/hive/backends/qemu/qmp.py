"""Send one QMP command to a running VM's control socket: the mechanism behind pause/resume/stop.

QEMU's own control protocol (QMP, the QEMU Machine Protocol) is what
`hivemind.hive.backends.qemu.process_runner.ProcessQemuRunner` uses instead of signals: `stop_vm`
sends `quit`, `pause_vm` sends `stop`, `resume_vm` sends `cont`, each over the Unix domain socket
`-qmp unix:<path>,server,nowait` opened it at start (`ProcessQemuRunner._build_qemu_args`). Split
out of that module into its own file purely for size (codingrules section 5.1's class-size limit:
`ProcessQemuRunner` itself has enough other responsibilities without this protocol's own
handshake), not because it is a second concept -- it is the same "talk to one VM's own QEMU
process" responsibility, just the one slice of it that speaks a line-based JSON protocol instead
of shelling out to a CLI tool.

Roadmap step 5.10 adds an optional `arguments` payload: `savevm`/`loadvm`
(`hivemind.hive.backends.qemu.process_runner.ProcessQemuRunner`) both go through QMP's
`human-monitor-command` (the QEMU-recommended way to run `savevm`/`loadvm`, which have never had a
dedicated QMP command of their own -- only the HMP, human monitor protocol, verbs), which needs a
`{"command-line": "savevm <tag>"}` argument the fixed `stop`/`cont`/`quit` commands never did.
`_build_payload` is split out as a pure function so the wire shape is testable with no socket at
all (this dev host is Windows, ADR-0026, where the Unix-socket half cannot run outside CI).

Roadmap step 5.11 (this branch) adds a second transport: a loopback TCP QMP endpoint, for a host
whose `asyncio` has no Unix-socket support at all (Windows, this dev host -- confirmed by running
`hasattr(asyncio, "open_unix_connection")` here, which returns False). `QmpAddress` is a small union
(`UnixQmpAddress` | `TcpQmpAddress`); `ProcessQemuRunner` decides which one a VM's own `-qmp`
argument uses (`_default_qmp_over_tcp` there, the same attribute probe this module uses) and passes
the matching address to every `qmp_execute` call for that VM.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.qemu`. Called by
    `hivemind.hive.backends.qemu.process_runner.ProcessQemuRunner`. Calls into
    `hivemind.hive.backends.qemu.runner` (QemuRunnerError) only.

Key invariants:
    - A missing or unreachable endpoint is a silent no-op, never `QemuRunnerError`: `stop_vm`'s own
      idempotency (`QemuRunnerPort`'s own key invariant) depends on "the VM is already gone" and
      "the QMP endpoint could not be reached" being indistinguishable from here.
    - Which transport a `UnixQmpAddress` gets is decided by probing for the
      `asyncio.open_unix_connection` attribute, never by checking `sys.platform` (this module's
      own roadmap-5.11 addition): the probe is what is actually true on this process's own
      asyncio build, and it is what a test can monkeypatch instead of faking the OS name.

See Also:
    - hivemind.hive.backends.qemu.runner for QemuRunnerError, the one exception this module raises.
    - hivemind.hive.backends.qemu.process_runner for ProcessQemuRunner, this module's one caller.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hivemind.hive.backends.qemu.runner import QemuRunnerError

__all__ = ["QmpAddress", "TcpQmpAddress", "UnixQmpAddress", "qmp_execute"]

# What a connector hands back: the same pair every asyncio open_*_connection call returns.
_Connection = tuple[asyncio.StreamReader, asyncio.StreamWriter]


@dataclass(frozen=True, slots=True)
class UnixQmpAddress:
    """A VM's QMP endpoint reached over a Unix domain socket (available off Windows)."""

    path: Path


@dataclass(frozen=True, slots=True)
class TcpQmpAddress:
    """A VM's QMP endpoint reached over loopback TCP (Windows, or an explicit override).

    Attributes:
        host: Always a loopback address (`"127.0.0.1"`): a Virtual Cell's own Warden never listens
            (ADR-0027), and neither does a VM's QMP endpoint -- it is reachable only from the same
            host `ProcessQemuRunner` runs on.
        port: The TCP port `qemu-system-x86_64` was started with (`-qmp tcp:<host>:<port>,...`).
    """

    host: str
    port: int


QmpAddress = UnixQmpAddress | TcpQmpAddress


async def qmp_execute(
    address: QmpAddress,
    command: str,
    *,
    subject: str,
    arguments: Mapping[str, Any] | None = None,
) -> None:
    """Send one QMP `command` to `address`; a missing or unreachable endpoint is a no-op.

    Args:
        address: Where to connect (`ProcessQemuRunner`'s own `-qmp` argument for this VM).
        command: The QMP command name (`"quit"`, `"stop"`, `"cont"`, `"human-monitor-command"`).
        subject: A short description of the VM, for `QemuRunnerError`'s own message (e.g.
            `"cell cell_01H...!r"`).
        arguments: The command's own `"arguments"` payload, when it takes one (roadmap step 5.10:
            `"human-monitor-command"` needs `{"command-line": "savevm <tag>"}`); omitted from the
            wire payload entirely when None, matching every pre-5.10 call site's own shape.

    Raises:
        QemuRunnerError: `address` is a `UnixQmpAddress` and this host's `asyncio` has no
            Unix-socket transport at all (module docstring's own probe), or QMP reached the VM and
            reported the command itself failed.
    """
    connector = _connector_for(address)
    if connector is None:
        raise QemuRunnerError(
            f"cannot send QMP {command!r} to {subject}: this host's asyncio has no Unix-socket "
            "QMP transport, and no TCP QMP endpoint was configured for this VM."
        )
    try:
        reader, writer = await connector()
    except OSError:
        return  # No endpoint to talk to: this VM is already gone (idempotency, module docstring).
    try:
        await reader.readline()  # The greeting QMP sends on connect; drained, unused.
        writer.write(b'{"execute": "qmp_capabilities"}\n')
        await writer.drain()
        await reader.readline()  # The capabilities-negotiation reply.
        writer.write(_build_payload(command, arguments))
        await writer.drain()
        reply = json.loads(await reader.readline())
        if "error" in reply:
            raise QemuRunnerError(f"QMP {command!r} on {subject} failed: {reply['error']}")
    finally:
        writer.close()


def _connector_for(address: QmpAddress) -> Callable[[], Awaitable[_Connection]] | None:
    """Return a zero-argument connector for `address`, or None when nothing can reach it.

    A `TcpQmpAddress` always has one: loopback TCP works through `asyncio.open_connection` on
    every platform. A `UnixQmpAddress` only has one when this host's asyncio actually exposes
    `open_unix_connection` (module docstring's own probe, not an OS-name check), so the same
    branch is exercised by a test that monkeypatches the attribute away instead of faking
    `sys.platform`.
    """
    if isinstance(address, TcpQmpAddress):
        host, port = address.host, address.port
        return lambda: asyncio.open_connection(host, port)
    open_unix_connection = getattr(asyncio, "open_unix_connection", None)
    if open_unix_connection is None:
        return None
    path = str(address.path)
    return lambda: open_unix_connection(path)


def _build_payload(command: str, arguments: Mapping[str, Any] | None) -> bytes:
    """Build the newline-terminated JSON line `qmp_execute` writes for `command`/`arguments`.

    Pulled out as a pure function so the wire shape is unit-testable with no socket at all: every
    other line in this module needs a real (or scripted) QMP endpoint to exercise.
    """
    payload: dict[str, Any] = {"execute": command}
    if arguments is not None:
        payload["arguments"] = dict(arguments)
    return json.dumps(payload).encode("utf-8") + b"\n"
