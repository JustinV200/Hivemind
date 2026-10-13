"""Re-export the swarm family: a device enrolled, colonized, promoted to a Nuc and watched.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The Swarm is
the set of enrolled devices, each carrying a Pollen Packet (the thin gateway that speaks the
protocol). ``enrolment`` holds the enrol request, the acceptance and the device heartbeat;
``ladder`` the traffic that moves a device up the rungs from gateway to colonized (the order to
colonize it, the packet's answer and the update of its standing terms); ``colonized`` what a device
does once it has a Warden (the always-on supervisor of one Cell): Nuc promotion (starting a local
model server), its outcome and the sync of a trail segment written while offline; ``watch`` the
read-only observation of a device by its Warden (the request and the packet's answer). This package
is the family's face: a caller imports any of its messages, enums or value models from here
without knowing which module defines them. The bounds each module names stay in that module,
because the spec makes the number normative, not the name.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages (the catalogue and
    the package face) and by every bee that builds or reads a swarm payload; calls into
    nothing beyond its own modules.

Key invariants:
    - Every class the registry registers under ``swarm.*`` is re-exported here
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; no message, enum or bound is defined here.

See Also:
    - docs/waggle/spec.md section 8.10 for the family's normative fields and rules.
    - waggle.messages.swarm.enrolment, waggle.messages.swarm.ladder,
      waggle.messages.swarm.colonized and waggle.messages.swarm.watch for the definitions.

Public API:
    - Enrolment (enrolment): DeviceHeartbeat, EnrolAccept, EnrolRequest, NodeKey, NucAction,
      NucOutcome, RuntimeLevel.
    - Ladder (ladder): Colonize, ColonizeAction, ColonizeOutcome, Colonized, NodeUpdate.
    - Colonized (colonized): NucPromote, NucPromoted, TrailSegmentSync, segment_signing_bytes.
    - Watch (watch): FileChange, FileChangeKind, LogLine, NetworkCounter, Observation,
      ObservationKind, Observe, ProcessSample, ResourceSample.
"""

from waggle.messages.swarm.colonized import (
    NucPromote,
    NucPromoted,
    TrailSegmentSync,
    segment_signing_bytes,
)
from waggle.messages.swarm.enrolment import (
    DeviceHeartbeat,
    EnrolAccept,
    EnrolRequest,
    NodeKey,
    NucAction,
    NucOutcome,
    RuntimeLevel,
)
from waggle.messages.swarm.ladder import (
    Colonize,
    ColonizeAction,
    Colonized,
    ColonizeOutcome,
    NodeUpdate,
)
from waggle.messages.swarm.watch import (
    FileChange,
    FileChangeKind,
    LogLine,
    NetworkCounter,
    Observation,
    ObservationKind,
    Observe,
    ProcessSample,
    ResourceSample,
)

__all__ = [
    "Colonize",
    "ColonizeAction",
    "ColonizeOutcome",
    "Colonized",
    "DeviceHeartbeat",
    "EnrolAccept",
    "EnrolRequest",
    "FileChange",
    "FileChangeKind",
    "LogLine",
    "NetworkCounter",
    "NodeKey",
    "NodeUpdate",
    "NucAction",
    "NucOutcome",
    "NucPromote",
    "NucPromoted",
    "Observation",
    "ObservationKind",
    "Observe",
    "ProcessSample",
    "ResourceSample",
    "RuntimeLevel",
    "TrailSegmentSync",
    "segment_signing_bytes",
]
