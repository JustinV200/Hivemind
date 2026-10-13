"""Provide CommandGuard and EnforcedCommandGuard: the Hive's first wall before a device command.

Every command a `PollenSession` sends is a signed instruction to someone else's machine, so it
passes the Guard (the Hive's capability check, ADR-0039) at the `DEVICE_COMMAND` enforcement point
before a byte leaves the Hive Stand. A session does not know how the check is made; it describes
what it is about to do as a `DeviceCommand` (the operation, the capabilities it needs, and only
identifiers and counts besides) and asks a `CommandGuard` to `authorise` it, then to `record_sent`
once it has an envelope id. `EnforcedCommandGuard` is the real guard: it asks the Hive's `Enforcer`
about each needed capability against the Warden's held set, with the node's own access level in
the context so the access-level rule caps the set (a read-only device runs no command at all), and
it writes the `swarm.command_sent` trail event. The Pollen Packet then checks the same request
against its own granted capabilities, so a refusal here and a refusal there are two independent
walls (gateway contract section 10).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.session`.
    Built by the composition root for each device Warden and handed to `SwarmSource`, which gives
    it to every `PollenSession`; called by the session's exec, file and process operations. Calls
    into hivemind.guard (Enforcer, the policy models, the capability grammar) and the swarm
    recorder.

Key invariants:
    - A command is authorised before it is recorded and recorded before it is sent: a refusal
      sends nothing, and a send that failed after recording still left its trail row.
    - `swarm.command_sent` names the lease, the request id, the operation, the program's base
      name, the number of arguments, whether it touched outside scratch, and the task and worker;
      never an argument, a path, a file's content or an environment value (codingrules 12, 15).
    - A refusal is `guard.denied` on the trail (the Enforcer wrote it) and a `CommandDeniedError`
      to the caller; the guard itself never retries or widens.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the Guard.
    - docs/waggle/gateway-contract.md section 10 for the device's own capability check.
    - hivemind.guard.enforcer for the Enforcer whose decision this wraps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from hivemind.guard import (
    Capability,
    CapabilitySet,
    EnforcementPoint,
    Enforcer,
    PolicyContext,
    PolicyRequest,
    PrincipalRef,
)
from hivemind.swarm.errors import CommandDeniedError
from hivemind.swarm.events import SwarmRecorder
from waggle.ids import LeaseId, MessageId, TaskId, WorkerId

COMMAND_SENT_KIND = "swarm.command_sent"

__all__ = ["COMMAND_SENT_KIND", "CommandGuard", "DeviceCommand", "EnforcedCommandGuard"]


@dataclass(frozen=True, slots=True)
class DeviceCommand:
    """One command a session is about to send, described for the Guard and the trail.

    Attributes:
        operation: What it does: `exec`, `put_file`, `get_file`, `delete_file`, `start` or `stop`.
        lease_id: The lease it runs in.
        needs: The capabilities it needs, each checked at the `DEVICE_COMMAND` point (empty for
            a command, like stopping a process the lease started, that needs none).
        program: The program's base name for an exec or a background start, else None.
        argument_count: How many arguments follow the program; their text is never recorded.
        outside_scratch: Whether it touches a path outside the lease's scratch directory.
        task_id: The task the lease serves, if any.
        worker_id: The sub-bee it is run for, when the caller knows.
    """

    operation: str
    lease_id: LeaseId
    needs: tuple[Capability, ...]
    program: str | None = None
    argument_count: int = 0
    outside_scratch: bool = False
    task_id: TaskId | None = None
    worker_id: WorkerId | None = None


class CommandGuard(Protocol):
    """Authorise a device command before it is sent, and record it once it is."""

    async def authorise(self, command: DeviceCommand) -> None:
        """Check that every capability `command` needs is held; return only if all are.

        Args:
            command: What the session is about to send.

        Raises:
            CommandDeniedError: A needed capability is not held. Nothing has been sent, and the
                refusal is on the trail.
        """
        ...

    async def record_sent(self, command: DeviceCommand, request_id: MessageId) -> None:
        """Record `swarm.command_sent` for an authorised command, before its frame is sent.

        Args:
            command: What the session is sending.
            request_id: The envelope id the request will carry.
        """
        ...


class EnforcedCommandGuard:
    """The real guard: the Hive's Enforcer decides, and the swarm recorder writes the record."""

    def __init__(
        self,
        enforcer: Enforcer,
        recorder: SwarmRecorder,
        principal: PrincipalRef,
        held: CapabilitySet,
        context: PolicyContext,
    ) -> None:
        """Build the guard for one device Warden.

        Args:
            enforcer: Decides each request and records a refusal as `guard.denied`.
            recorder: Writes `swarm.command_sent`.
            principal: Who is sending: the device's Warden.
            held: Everything that Warden currently holds.
            context: Where the command happens: the node's Comb Shield tier and its access level,
                which the policy caps the held set to.
        """
        self._enforcer = enforcer
        self._recorder = recorder
        self._principal = principal
        self._held = held
        self._context = context

    async def authorise(self, command: DeviceCommand) -> None:
        """Check each needed capability at the `DEVICE_COMMAND` point; see `CommandGuard`."""
        for needed in command.needs:
            request = PolicyRequest(
                principal=self._principal,
                point=EnforcementPoint.DEVICE_COMMAND,
                needed=needed,
                held=self._held,
                context=self._context,
            )
            decision = await self._enforcer.check(request)
            if not decision.allowed:
                raise CommandDeniedError(command.lease_id, str(needed))

    async def record_sent(self, command: DeviceCommand, request_id: MessageId) -> None:
        """Write `swarm.command_sent` for the command; see `CommandGuard`."""
        event = self._recorder.swarm_event(
            COMMAND_SENT_KIND,
            command.lease_id,
            {
                "lease_id": command.lease_id,
                "request_id": request_id,
                "operation": command.operation,
                "program": command.program,
                "argument_count": command.argument_count,
                "outside_scratch": command.outside_scratch,
                "task_id": command.task_id,
                "worker_id": command.worker_id,
            },
            # The trail names who sent it: the device's Warden, not the process that recorded it.
            actor=self._principal.id,
        )
        await self._recorder.record(event)
