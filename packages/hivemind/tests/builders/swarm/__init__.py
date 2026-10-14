"""Re-export the Swarm test builders: parties, enrolment values, the scripted device and the rig.

Every Swarm test starts from the same few things: a Hive Stand and a device with real signing
keys, an enrolled node, an invite and a request, a Pollen Packet double that answers like a
conforming gateway, and a running link between the Hive's side and that double. They live here so
a test states only the fact under test. Import them as `from builders.swarm import make_link_rig`.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by every test under
    tests/unit/swarm, the registry and RealCellSource contract suites and the listener tests.

Key invariants:
    - This file holds re-exports and __all__ only.

See Also:
    - tests/builders/README.md for how the builders are used.

Public API:
    - HiveParty, DeviceParty, make_hive_party, make_device_party, make_swarm_node, make_heartbeat,
      make_cell_ready, make_capacity_report: the parties, the registry row and the frames a
      device sends (parties).
    - make_endpoint, make_rules, make_swarm_invite, make_enrol_request, make_enrol_envelope,
      enrol_node: the enrolment values (enrolment).
    - RecordingGuard: a CommandGuard double (guards).
    - LinkRig, make_link_rig, make_source, open_rig_session: the running link (rig).
    - RawLink, make_raw_link: a link whose device end a test drives by hand (raw).
    - HiveRig, HubLog, WardenHandlerLog, make_hive_rig: the Hive Stand's registry, trail, door and
      hub, a hub listener that keeps what it heard and a Warden handler double (hive).
    - RouterRig, Dialled, make_router_rig: a ConnectionRouter served over in-memory connections
      (dial).
    - DeviceClient: a device dialling the listener over a real socket (client).
    - TlsMaterial, make_tls_material: a throwaway self-signed certificate (tls).
    - linux_probes, macos_probes, linux_route, linux_route6, macos_netstat, resolv_conf,
      scutil_dns: the text of a device's routing table and DNS settings (routes).
    - ScriptedDevice, DeviceScript, ExecScript, ReleaseScript: the device double (device).
    - LiveHive, LiveHiveOptions, TamperableSigner, free_port, start_live_hive: a Hive Stand on
      real time and real sockets, for a real gateway to dial (live).
    - LeasedDevice, lease_device, real_guard: lease a connected device with the real Guard in
      front and open its session (leasing).
"""

from builders.swarm.client import DeviceClient
from builders.swarm.device import (
    DeviceLease,
    DeviceProcess,
    DeviceScript,
    DeviceState,
    ExecScript,
    ReleaseScript,
    ScriptedDevice,
)
from builders.swarm.dial import Dialled, RouterRig, make_router_rig
from builders.swarm.enrolment import (
    enrol_node,
    make_endpoint,
    make_enrol_envelope,
    make_enrol_request,
    make_rules,
    make_swarm_invite,
)
from builders.swarm.guards import RecordingGuard
from builders.swarm.hive import HiveRig, HubLog, WardenHandlerLog, make_hive_rig
from builders.swarm.leasing import LeasedDevice, lease_device, real_guard
from builders.swarm.live import (
    LiveHive,
    LiveHiveOptions,
    TamperableSigner,
    free_port,
    start_live_hive,
)
from builders.swarm.parties import (
    DeviceParty,
    HiveParty,
    make_capacity_report,
    make_cell_ready,
    make_device_party,
    make_heartbeat,
    make_hive_party,
    make_swarm_node,
)
from builders.swarm.raw import RawLink, make_raw_link
from builders.swarm.rig import LinkRig, make_link_rig, make_source, open_rig_session
from builders.swarm.routes import (
    RouteRow,
    linux_probes,
    linux_route,
    linux_route6,
    macos_netstat,
    macos_probes,
    resolv_conf,
    scutil_dns,
)
from builders.swarm.tls import TlsMaterial, make_tls_material

__all__ = [
    "DeviceClient",
    "DeviceLease",
    "DeviceParty",
    "DeviceProcess",
    "DeviceScript",
    "DeviceState",
    "Dialled",
    "ExecScript",
    "HiveParty",
    "HiveRig",
    "HubLog",
    "LeasedDevice",
    "LinkRig",
    "LiveHive",
    "LiveHiveOptions",
    "RawLink",
    "RecordingGuard",
    "ReleaseScript",
    "RouteRow",
    "RouterRig",
    "ScriptedDevice",
    "TamperableSigner",
    "TlsMaterial",
    "WardenHandlerLog",
    "enrol_node",
    "free_port",
    "lease_device",
    "linux_probes",
    "linux_route",
    "linux_route6",
    "macos_netstat",
    "macos_probes",
    "make_capacity_report",
    "make_cell_ready",
    "make_device_party",
    "make_endpoint",
    "make_enrol_envelope",
    "make_enrol_request",
    "make_heartbeat",
    "make_hive_party",
    "make_hive_rig",
    "make_link_rig",
    "make_raw_link",
    "make_router_rig",
    "make_rules",
    "make_source",
    "make_swarm_invite",
    "make_swarm_node",
    "make_tls_material",
    "open_rig_session",
    "real_guard",
    "resolv_conf",
    "scutil_dns",
    "start_live_hive",
]
