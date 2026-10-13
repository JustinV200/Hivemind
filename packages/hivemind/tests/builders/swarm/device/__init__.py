"""Re-export the scripted Pollen Packet: a device double that answers like a conforming gateway.

The Swarm's Hive side is tested against something that behaves as the gateway contract says a
device does, and this package is that something. `ScriptedDevice` runs over any waggle transport;
`DeviceScript` and `ExecScript` are the knobs a test turns (how a program behaves, which access
the device grants, what it refuses or never answers); `DeviceState` is what it accumulates. The
services behind it (`leases`, `execs`, `files`, `procs`) each answer one family of requests.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the Swarm tests.

Key invariants:
    - This file holds re-exports and __all__ only.

See Also:
    - docs/waggle/gateway-contract.md for the behaviour the double mimics.

Public API:
    - ScriptedDevice: the device (core).
    - DeviceScript, ExecScript, ReleaseScript, DeviceState, DeviceLease, DeviceProcess: the script
      and the state (state).
"""

from builders.swarm.device.core import ScriptedDevice
from builders.swarm.device.state import (
    DeviceLease,
    DeviceProcess,
    DeviceScript,
    DeviceState,
    ExecScript,
    ReleaseScript,
)

__all__ = [
    "DeviceLease",
    "DeviceProcess",
    "DeviceScript",
    "DeviceState",
    "ExecScript",
    "ReleaseScript",
    "ScriptedDevice",
]
