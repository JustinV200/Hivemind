"""Re-export the device session: a CellSession over Waggle for an enrolled Swarm device.

A Real Cell's Warden (the always-on supervisor of one Cell) drives its Cell through a `CellSession`
(the terminal every Cell offers), and for a Swarm device whose Warden still lives on the Hive Stand
(Level 0 of the runtime ladder) that terminal is a Pollen Packet (the thin gateway installed on
the device) reached over Waggle. `PollenSession` is that session. `guard` is the Hive's first wall
in front of it (the `CommandGuard` every command passes at the `DEVICE_COMMAND` enforcement point,
which also records `swarm.command_sent`), `paths` reasons about the device's own file paths by
their text, `codes` maps what the device answers onto the errors a session promises, and `exec`,
`files` and `process` are the three groups of operations, with `wire` holding what they share.
This file is the package's face: a caller writes `from hivemind.swarm.session import PollenSession`
without knowing the split (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm`. Built by
    `SwarmSource.open_session`; the guard is built by the composition root for each device
    Warden. Calls into hivemind.cell, hivemind.guard, the gateway and waggle.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules inside the package import each other by full module path, never through
      this face.

See Also:
    - docs/waggle/gateway-contract.md sections 9 to 12 for the messages and rules served.
    - tests/contracts/test_cell_session_contract.py for the behaviour every session shares.

Public API:
    - PollenSession: the session (session).
    - CommandGuard, DeviceCommand, EnforcedCommandGuard, COMMAND_SENT_KIND: the check and record
      in front of every command (guard).
    - DevicePathRule, DevicePaths: the device's path rules, as a lease's rule and for a session
      (paths).
    - absolute_device_path: a path the operator typed for the device, checked and normalised
      (paths).
    - device_text: a device path held as a `Path`, in the device's own spelling (paths).
    - ErrorContext, translate, exit_code_for: the device's codes as session errors (codes).
"""

from hivemind.swarm.session.codes import ErrorContext, exit_code_for, translate
from hivemind.swarm.session.guard import (
    COMMAND_SENT_KIND,
    CommandGuard,
    DeviceCommand,
    EnforcedCommandGuard,
)
from hivemind.swarm.session.paths import (
    DevicePathRule,
    DevicePaths,
    absolute_device_path,
    device_text,
)
from hivemind.swarm.session.session import PollenSession

__all__ = [
    "COMMAND_SENT_KIND",
    "CommandGuard",
    "DeviceCommand",
    "DevicePathRule",
    "DevicePaths",
    "EnforcedCommandGuard",
    "ErrorContext",
    "PollenSession",
    "absolute_device_path",
    "device_text",
    "exit_code_for",
    "translate",
]
