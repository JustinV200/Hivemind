"""Provide RecordingGuard: a CommandGuard double that keeps what it was asked and refuses on demand.

The session tests need to see what a `PollenSession` asked the Guard before it sent a byte, and to
make the Guard say no. `RecordingGuard` satisfies the `CommandGuard` protocol: it keeps every
`DeviceCommand` it authorised and every `(command, request id)` it recorded as sent, and raises
`CommandDeniedError` for any capability a test names in `deny`. It writes no trail and makes no
policy decision, so session tests state only the behaviour under test; the real guard has its own
tests.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the session, source and
    shield tests.

Key invariants:
    - A command is recorded as sent only after it was authorised, in the order the session did it.
    - A denied command is kept in `denied` and never in `sent`.

See Also:
    - hivemind.swarm.session.guard for the protocol this satisfies and the real guard.
"""

from __future__ import annotations

from hivemind.swarm.errors import CommandDeniedError
from hivemind.swarm.session.guard import DeviceCommand
from waggle.ids import MessageId

__all__ = ["RecordingGuard"]


class RecordingGuard:
    """A `CommandGuard` that allows everything but what a test denies, and keeps a log."""

    def __init__(self, deny: frozenset[str] = frozenset()) -> None:
        """Build a guard that refuses any command needing a capability written in `deny`.

        Args:
            deny: Capability strings (`exec:rm`, `fs:write:/x`) the guard refuses.
        """
        self.deny = set(deny)
        self.authorised: list[DeviceCommand] = []
        self.denied: list[DeviceCommand] = []
        self.sent: list[tuple[DeviceCommand, MessageId]] = []

    async def authorise(self, command: DeviceCommand) -> None:
        """Allow `command` unless one of its needs is in `deny`."""
        for need in command.needs:
            if str(need) in self.deny:
                self.denied.append(command)
                raise CommandDeniedError(command.lease_id, str(need))
        self.authorised.append(command)

    async def record_sent(self, command: DeviceCommand, request_id: MessageId) -> None:
        """Keep the command and the envelope id it is sent under."""
        self.sent.append((command, request_id))
