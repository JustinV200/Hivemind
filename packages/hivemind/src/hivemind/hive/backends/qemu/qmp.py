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
all (this dev host is Windows, ADR-0026, where the socket half cannot run outside CI).

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.backends.qemu`. Called by
    `hivemind.hive.backends.qemu.process_runner.ProcessQemuRunner`. Calls into
    `hivemind.hive.backends.qemu.runner` (QemuRunnerError) only.

Key invariants:
    - A missing or unreachable socket is a silent no-op, never `QemuRunnerError`: `stop_vm`'s own
      idempotency (`QemuRunnerPort`'s own key invariant) depends on "the VM is already gone" and
      "the QMP endpoint could not be reached" being indistinguishable from here.
    - `asyncio.open_unix_connection` is called only inside the `sys.platform != "win32"` branch:
      it does not exist in typeshed's own stub for Windows, and this is the one guard that lets
      mypy's platform narrowing check this module cleanly on every CI runner, mirroring
      `hivemind.cell.local.releaser`'s own `sys.platform` guard for a different POSIX-only call.

See Also:
    - hivemind.hive.backends.qemu.runner for QemuRunnerError, the one exception this module raises.
    - hivemind.hive.backends.qemu.process_runner for ProcessQemuRunner, this module's one caller.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hivemind.hive.backends.qemu.runner import QemuRunnerError

__all__ = ["qmp_execute"]


async def qmp_execute(
    socket_path: Path,
    command: str,
    *,
    subject: str,
    arguments: Mapping[str, Any] | None = None,
) -> None:
    """Send one QMP `command` to `socket_path`; a missing or unreachable socket is a no-op.

    Args:
        socket_path: The VM's own QMP Unix socket (`ProcessQemuRunner`'s own
            `-qmp unix:<path>,server,nowait` argument).
        command: The QMP command name (`"quit"`, `"stop"`, `"cont"`, `"human-monitor-command"`).
        subject: A short description of the VM, for `QemuRunnerError`'s own message (e.g.
            `"cell cell_01H...!r"`).
        arguments: The command's own `"arguments"` payload, when it takes one (roadmap step 5.10:
            `"human-monitor-command"` needs `{"command-line": "savevm <tag>"}`); omitted from the
            wire payload entirely when None, matching every pre-5.10 call site's own shape.

    Raises:
        QemuRunnerError: The platform has no Unix-socket support (Windows; see this module's own
            key invariant), or QMP reached the VM and reported the command itself failed.
    """
    if sys.platform == "win32":
        raise QemuRunnerError(
            f"cannot send QMP {command!r} to {subject}: Unix-socket QMP is not supported on "
            "Windows; a TCP QMP endpoint is needed there (not yet built)."
        )
    else:
        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
        except OSError:
            return  # No socket to talk to: this VM is already gone (idempotency, module docstring).
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


def _build_payload(command: str, arguments: Mapping[str, Any] | None) -> bytes:
    """Build the newline-terminated JSON line `qmp_execute` writes for `command`/`arguments`.

    Pulled out as a pure function so the wire shape is unit-testable with no socket at all: every
    other line in this module needs a real (or scripted) QMP endpoint to exercise.
    """
    payload: dict[str, Any] = {"execute": command}
    if arguments is not None:
        payload["arguments"] = dict(arguments)
    return json.dumps(payload).encode("utf-8") + b"\n"
