"""Provide SessionWire: what the operations of one device session share, and the needs they state.

A `PollenSession` is a thin object over three groups of operations (run a command, move a file,
manage a background process), and each of them does the same four things around a request: say
what capabilities the request needs, ask the Guard, open a conversation on the device's link and
record the command, and turn the device's refusals into the errors a `CellSession` promises. This
module holds what they share. `SessionWire` bundles the link, the lease, the guard and the path
rules, so each operation takes one argument instead of four. `exec_needs`, `read_needs` and
`write_needs` state the capabilities of gateway contract section 10 from a resolved path (an
`exec` need is the program exactly as given; a write outside scratch also needs
`cell:outside_scratch`). `translated` is the context manager that raises a mapped error `from` the
device's own, and `command_for` builds the `DeviceCommand` the Guard and the trail see.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.session`.
    Used by the session's exec, file and process modules and built by `PollenSession`. Calls into
    hivemind.guard (the capability grammar), hivemind.cell, the gateway link and the sibling
    modules of this package.

Key invariants:
    - A capability scope is built from text the caller already resolved; one that is not a valid
      scope (an empty program name) is a denial, never an unhandled validation error.
    - A mapped error is always raised `from` the device's, so a trace shows both.
    - Nothing here sends a frame: it prepares, and the operations send.

See Also:
    - docs/waggle/gateway-contract.md section 10 for what each request needs.
    - hivemind.swarm.session.guard for the DeviceCommand and the guard that checks it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from pydantic import ValidationError

from hivemind.cell.lease import RealCellLease
from hivemind.guard import Capability, CapabilityFamily
from hivemind.swarm.errors import CommandDeniedError, DeviceRequestError
from hivemind.swarm.gateway.link import DeviceLink
from hivemind.swarm.session.codes import ErrorContext, translate
from hivemind.swarm.session.guard import CommandGuard, DeviceCommand
from hivemind.swarm.session.paths import DevicePaths

__all__ = [
    "SessionWire",
    "command_for",
    "exec_needs",
    "read_needs",
    "translated",
    "write_needs",
]


@dataclass(frozen=True, slots=True)
class SessionWire:
    """Everything an operation of one device session works with.

    Attributes:
        link: The device's connection; requests go over it and answers come back.
        lease: The lease the session runs in; its holder is the sender of every request.
        guard: Checks and records each command.
        paths: Resolves and judges the lease's device paths.
    """

    link: DeviceLink
    lease: RealCellLease
    guard: CommandGuard
    paths: DevicePaths

    @property
    def sender(self) -> str:
        """The bee address session traffic comes from: the lease's holder, the device's Warden."""
        return str(self.lease.holder)

    def error_context(
        self, path: str | None = None, argv: tuple[str, ...] = (), needed: str = ""
    ) -> ErrorContext:
        """Describe a request for the errors translated from the device's refusal of it."""
        return ErrorContext(
            scratch_dir=self.lease.scratch_root,
            lease_id=self.lease.id,
            path=path,
            argv=argv,
            needed=needed,
        )


@contextmanager
def translated(context: ErrorContext) -> Iterator[None]:
    """Turn a device's refusal inside the block into the error a `CellSession` promises.

    Args:
        context: What the failing request was.

    Raises:
        Exception: The mapped error, from the `DeviceRequestError`, when its code has a mapping;
            otherwise the `DeviceRequestError` itself, unchanged.
    """
    try:
        yield
    except DeviceRequestError as error:
        mapped = translate(error, context)
        if mapped is error:
            raise
        raise mapped from error


def capability(family: CapabilityFamily, scope: str, lease_id: str) -> Capability:
    """Build one needed capability, or refuse the command whose scope is not a valid one."""
    try:
        return Capability(family=family, scope=scope)
    except ValidationError as exc:
        # An empty program name or path can never be held, so it is simply not allowed; the
        # scope is not echoed, because it may be the very text that is wrong.
        raise CommandDeniedError(lease_id, f"{family.value}:<invalid scope>") from exc


def exec_needs(wire: SessionWire, program: str, cwd: str | None) -> tuple[Capability, ...]:
    """State what running `program` in `cwd` needs: `exec`, and the outside-scratch reach if any."""
    needs = [capability(CapabilityFamily.EXEC, program, wire.lease.id)]
    if cwd is not None and not wire.paths.is_inside_scratch(cwd):
        needs.append(capability(CapabilityFamily.CELL_OUTSIDE_SCRATCH, cwd, wire.lease.id))
    return tuple(needs)


def read_needs(wire: SessionWire, resolved: str) -> tuple[Capability, ...]:
    """State what reading a resolved path needs: `fs:read` on it, wherever it is."""
    return (capability(CapabilityFamily.FS_READ, resolved, wire.lease.id),)


def write_needs(wire: SessionWire, resolved: str) -> tuple[Capability, ...]:
    """State what writing or deleting a resolved path needs; outside scratch it needs reach too."""
    needs = [capability(CapabilityFamily.FS_WRITE, resolved, wire.lease.id)]
    if not wire.paths.is_inside_scratch(resolved):
        needs.append(capability(CapabilityFamily.CELL_OUTSIDE_SCRATCH, resolved, wire.lease.id))
    return tuple(needs)


def command_for(
    wire: SessionWire,
    operation: str,
    needs: tuple[Capability, ...],
    argv: tuple[str, ...] = (),
) -> DeviceCommand:
    """Describe a command for the Guard: identifiers, the program's base name and a count only.

    Args:
        wire: The session the command runs in.
        operation: `exec`, `put_file`, `get_file`, `delete_file`, `start` or `stop`.
        needs: The capabilities it needs.
        argv: The command line of an exec or a start; only its program's base name and the number
            of arguments leave this function.

    Returns:
        The `DeviceCommand`, flagged `outside_scratch` when any need is the outside-scratch one.
    """
    outside = any(need.family is CapabilityFamily.CELL_OUTSIDE_SCRATCH for need in needs)
    return DeviceCommand(
        operation=operation,
        lease_id=wire.lease.id,
        needs=needs,
        program=wire.paths.basename(argv[0]) if argv else None,
        argument_count=max(len(argv) - 1, 0),
        outside_scratch=outside,
        task_id=wire.lease.task_id,
        worker_id=None,
    )
