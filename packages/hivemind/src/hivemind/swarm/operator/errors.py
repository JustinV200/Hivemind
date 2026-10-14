"""Define the errors the operator's desk raises: an unknown or ambiguous device, a refusal.

The operator (the human running the Hive) names an enrolled device by a device id, a Cell id or a
label, and then revokes it, changes its access level or Comb Shield tier (its security tier), or
asks the Queen (the Hive's central orchestrator) to move its Warden (the per-device supervisor) up
or down the runtime ladder. Each way that can fail on purpose has a class here, rooted at
`SwarmError`, so the `hive swarm` commands catch one name, print its sentence and exit, and a
later caller (the Entrance's console, a script) handles the same errors. Every message is a full
sentence naming the ids involved and what to do next, and none carries a key, a token or an
invite.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.operator`.
    Raised by `lookup` and `desk`; read by the `hive swarm` commands (Layer 7). Calls into
    hivemind.swarm.errors and hivemind.common.errors only.

Key invariants:
    - Every class sets its own `code`, `hive.swarm.<name>`, and none shares one with another.
    - No message repeats more than a device's id, Cell id, label or tier name.

See Also:
    - hivemind.swarm.errors for the rest of the Swarm's error tree.
    - docs/waggle/enrolment.md section 11 for what revoking a device means.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import ClassVar

from hivemind.common.errors import ConflictError, NotFoundError, PermissionDeniedError
from hivemind.swarm.errors import SwarmError

__all__ = [
    "AmbiguousNodeError",
    "NodeReferenceError",
    "RungUnavailableError",
    "TierRefusedError",
    "UnknownNodeError",
]


class NodeReferenceError(SwarmError):
    """Root of the errors for a device reference that names no single enrolled device."""

    code: ClassVar[str] = "hive.swarm.node_reference"


class UnknownNodeError(NodeReferenceError, NotFoundError):
    """Raise when a reference matches no enrolled device by device id, Cell id or label."""

    code: ClassVar[str] = "hive.swarm.node_unknown"

    def __init__(self, reference: str) -> None:
        """Build the error for a reference that matched nothing.

        Args:
            reference: What the operator typed; an id or a label, never a secret.
        """
        super().__init__(
            f"No enrolled device matches {reference!r}: name a device id, a Cell id or a label "
            "shown by `hive swarm list`."
        )
        self.reference = reference


class AmbiguousNodeError(NodeReferenceError, ConflictError):
    """Raise when a label matches more than one enrolled device and no id was given."""

    code: ClassVar[str] = "hive.swarm.node_ambiguous"

    def __init__(self, reference: str, device_ids: Sequence[str]) -> None:
        """Build the error for a label that several devices carry.

        Args:
            reference: The label the operator typed.
            device_ids: The device ids that carry it, so the operator can name one.
        """
        super().__init__(
            f"The label {reference!r} names {len(device_ids)} devices ({', '.join(device_ids)}): "
            "name one of them by its device id or Cell id."
        )
        self.reference = reference
        self.device_ids = tuple(device_ids)


class RungUnavailableError(SwarmError, ConflictError):
    """Raise when `promote` or `demote` asks for a rung beyond either end of the ladder."""

    code: ClassVar[str] = "hive.swarm.rung_unavailable"

    def __init__(self, device_id: str, level: Enum, *, is_promotion: bool) -> None:
        """Build the error for a device already on the end of the ladder.

        Args:
            device_id: The device the operator named.
            level: The rung it is on.
            is_promotion: Whether the operator asked to climb (else to descend).
        """
        end = "top" if is_promotion else "bottom"
        verb = "promoted" if is_promotion else "demoted"
        super().__init__(
            f"Device {device_id} is on the {level.name} rung, the {end} of the ladder "
            f"(GATEWAY <-> COLONIZED <-> NUC); it cannot be {verb} further."
        )
        self.device_id = device_id
        self.level = level


class TierRefusedError(SwarmError, PermissionDeniedError):
    """Raise when the operator asks to put a device on the virtual-only Night Veil tier."""

    code: ClassVar[str] = "hive.swarm.tier_refused"

    def __init__(self, device_id: str) -> None:
        """Build the error for a Night Veil request on a borrowed device.

        Args:
            device_id: The device the operator named.
        """
        super().__init__(
            f"Device {device_id} cannot be set to NIGHT_VEIL: that tier exists only for Virtual "
            "Cells, which are provisioned and destroyed after one task, and a borrowed device is "
            "never torn down. Choose MEADOW or PROPOLIS."
        )
        self.device_id = device_id
