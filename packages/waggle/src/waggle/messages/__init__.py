"""Re-export the public face of every Waggle message family: the messages package.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance); a message
is a frozen pydantic model, one per kind, that travels as the payload of an Envelope (the outer
wrapper every Waggle message travels in). The package holds one sub-package per family (task,
supervision, forage, cell, session, honey, tool, capping, swarm, control, basket), each split by
responsibility into modules behind the family's own ``__init__``, plus the shared base, labels
and reports, the catalogue (the one kind list) and the registry that indexes it. This file
re-exports every message class, the shared labels and reports, the two base classes and the
registry API, so a caller writes ``from waggle.messages import TaskAssign`` without knowing
which family, let alone which module, defines it. It does not also carry each family's enums
and value models: those are the family package's face (``waggle.messages.task.WorkerRole``);
the bounds stay in the module that names them, and the shared constants, the id field aliases,
``UtcDatetime``, ``check_id`` and ``id_validator`` come from ``waggle.messages.base``.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called by waggle.envelope and waggle.codec through
    the registry, and by every bee that builds or reads a payload; calls into nothing else in
    the workspace.

Key invariants:
    - Every class the registry registers is re-exported here under its own name, so
      waggle.messages.<Class> resolves for every kind on the wire
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; the kind list lives in
      waggle.messages.catalogue and nowhere else.

See Also:
    - docs/waggle/spec.md section 8 for the catalogue and the family sections behind each module.
    - waggle.messages.base for the shared constants and id field aliases not re-exported here.
    - waggle.messages.registry for the lookups the envelope and the codec use.

Public API:
    - WaggleMessage, MessageShape (base): the class every message subclasses, and the request,
      reply or event shape the registry fixes per kind.
    - Labels (labels): AccessLevel, AccuracyBar, AlarmSeverity, CombShieldLevel, HandoffRef,
      HoneyClearance, OsFamily, PlannedLeaving, Postcondition, PostconditionKind, Tempo, Urgency.
    - Reports (reports): CellCapabilitiesReport, GpuReport, HostCapacityReport, PlatformReport.
    - Task (task): TaskAssign, TaskProgress, TaskResult, TaskCancel, TaskPause,
      TaskResume.
    - Supervision (supervision): Heartbeat,
      AlarmRaised, AlarmResolved, Inspect, InspectReply, Intervene, Question, Answer.
    - Forage (forage): CapacityReport, GrantIssued, GrantRevoked, ForageRequest,
      ForageReply, HostingDecided, CeilingsSet, PlanWritten, SourceAdded, SourceRemoved,
      SourceEvict.
    - Cell (cell): CellReady, CellHeartbeat, CellTeardownRequest,
      CellRequest, LeaseOpened, LeaseReleased, CellWaxProposed, CellWaxWritten, CellWaxCleared,
      CellSnapshotRequest, CellSnapshotReply, CellRollbackRequest, CellRollbackReply,
      CellTaintOrder.
    - Session (session): SessionOpen, SessionExec, SessionStdin,
      SessionOutput, SessionExit, SessionPutFile, SessionGetFile, SessionClose,
      SessionDeleteFile, SessionProcess, SessionProcessState.
    - Honey (honey): NectarDeposit, HoneyQuery, HoneyResponse.
    - Tool (tool): ToolRequest, ToolPromoted, ToolInvoke, ToolResult, ToolQuarantine,
      ToolQuarantineReport, ToolRetired.
    - Capping (capping): ProposalSubmitted, CheckResult, Verdict,
      PostconditionResult, RollbackDone.
    - Swarm (swarm): EnrolRequest, EnrolAccept, DeviceHeartbeat, NucPromote,
      NucPromoted, TrailSegmentSync, NodeUpdate, Colonize, Colonized, Observe, Observation.
    - Control (control): Ping, Pong, ErrorMessage, Shutdown, Cluster, Wake,
      HumanMessage, MaskOverride, QueenMoved.
    - Basket (basket): BasketOffer, BasketChunk, BasketAck, BasketRefuse.
    - Registry (registry): MessageSpec, MESSAGE_SPECS, spec_for, model_for, kind_for,
      all_kinds.
"""

from waggle.messages.base import MessageShape, WaggleMessage
from waggle.messages.basket import BasketAck, BasketChunk, BasketOffer, BasketRefuse
from waggle.messages.capping import (
    CheckResult,
    PostconditionResult,
    ProposalSubmitted,
    RollbackDone,
    Verdict,
)
from waggle.messages.cell import (
    CellHeartbeat,
    CellReady,
    CellRequest,
    CellRollbackReply,
    CellRollbackRequest,
    CellSnapshotReply,
    CellSnapshotRequest,
    CellTaintOrder,
    CellTeardownRequest,
    CellWaxCleared,
    CellWaxProposed,
    CellWaxWritten,
    LeaseOpened,
    LeaseReleased,
)
from waggle.messages.control import (
    Cluster,
    ErrorMessage,
    HumanMessage,
    MaskOverride,
    Ping,
    Pong,
    QueenMoved,
    Shutdown,
    Wake,
)
from waggle.messages.forage import (
    CapacityReport,
    CeilingsSet,
    ForageReply,
    ForageRequest,
    GrantIssued,
    GrantRevoked,
    HostingDecided,
    PlanWritten,
    SourceAdded,
    SourceEvict,
    SourceRemoved,
)
from waggle.messages.honey import HoneyQuery, HoneyResponse, NectarDeposit
from waggle.messages.labels import (
    AccessLevel,
    AccuracyBar,
    AlarmSeverity,
    CombShieldLevel,
    HandoffRef,
    HoneyClearance,
    OsFamily,
    PlannedLeaving,
    Postcondition,
    PostconditionKind,
    Tempo,
    Urgency,
)
from waggle.messages.registry import (
    MESSAGE_SPECS,
    MessageSpec,
    all_kinds,
    kind_for,
    model_for,
    spec_for,
)
from waggle.messages.reports import (
    CellCapabilitiesReport,
    GpuReport,
    HostCapacityReport,
    PlatformReport,
)
from waggle.messages.session import (
    SessionClose,
    SessionDeleteFile,
    SessionExec,
    SessionExit,
    SessionGetFile,
    SessionOpen,
    SessionOutput,
    SessionProcess,
    SessionProcessState,
    SessionPutFile,
    SessionStdin,
)
from waggle.messages.supervision import (
    AlarmRaised,
    AlarmResolved,
    Answer,
    Heartbeat,
    Inspect,
    InspectReply,
    Intervene,
    Question,
)
from waggle.messages.swarm import (
    Colonize,
    Colonized,
    DeviceHeartbeat,
    EnrolAccept,
    EnrolRequest,
    NodeUpdate,
    NucPromote,
    NucPromoted,
    Observation,
    Observe,
    TrailSegmentSync,
)
from waggle.messages.task import (
    TaskAssign,
    TaskCancel,
    TaskPause,
    TaskProgress,
    TaskResult,
    TaskResume,
)
from waggle.messages.tool import (
    ToolInvoke,
    ToolPromoted,
    ToolQuarantine,
    ToolQuarantineReport,
    ToolRequest,
    ToolResult,
    ToolRetired,
)

__all__ = [
    "MESSAGE_SPECS",
    "AccessLevel",
    "AccuracyBar",
    "AlarmRaised",
    "AlarmResolved",
    "AlarmSeverity",
    "Answer",
    "BasketAck",
    "BasketChunk",
    "BasketOffer",
    "BasketRefuse",
    "CapacityReport",
    "CeilingsSet",
    "CellCapabilitiesReport",
    "CellHeartbeat",
    "CellReady",
    "CellRequest",
    "CellRollbackReply",
    "CellRollbackRequest",
    "CellSnapshotReply",
    "CellSnapshotRequest",
    "CellTaintOrder",
    "CellTeardownRequest",
    "CellWaxCleared",
    "CellWaxProposed",
    "CellWaxWritten",
    "CheckResult",
    "Cluster",
    "Colonize",
    "Colonized",
    "CombShieldLevel",
    "DeviceHeartbeat",
    "EnrolAccept",
    "EnrolRequest",
    "ErrorMessage",
    "ForageReply",
    "ForageRequest",
    "GpuReport",
    "GrantIssued",
    "GrantRevoked",
    "HandoffRef",
    "Heartbeat",
    "HoneyClearance",
    "HoneyQuery",
    "HoneyResponse",
    "HostCapacityReport",
    "HostingDecided",
    "HumanMessage",
    "Inspect",
    "InspectReply",
    "Intervene",
    "LeaseOpened",
    "LeaseReleased",
    "MaskOverride",
    "MessageShape",
    "MessageSpec",
    "NectarDeposit",
    "NodeUpdate",
    "NucPromote",
    "NucPromoted",
    "Observation",
    "Observe",
    "OsFamily",
    "Ping",
    "PlanWritten",
    "PlannedLeaving",
    "PlatformReport",
    "Pong",
    "Postcondition",
    "PostconditionKind",
    "PostconditionResult",
    "ProposalSubmitted",
    "QueenMoved",
    "Question",
    "RollbackDone",
    "SessionClose",
    "SessionDeleteFile",
    "SessionExec",
    "SessionExit",
    "SessionGetFile",
    "SessionOpen",
    "SessionOutput",
    "SessionProcess",
    "SessionProcessState",
    "SessionPutFile",
    "SessionStdin",
    "Shutdown",
    "SourceAdded",
    "SourceEvict",
    "SourceRemoved",
    "TaskAssign",
    "TaskCancel",
    "TaskPause",
    "TaskProgress",
    "TaskResult",
    "TaskResume",
    "Tempo",
    "ToolInvoke",
    "ToolPromoted",
    "ToolQuarantine",
    "ToolQuarantineReport",
    "ToolRequest",
    "ToolResult",
    "ToolRetired",
    "TrailSegmentSync",
    "Urgency",
    "Verdict",
    "WaggleMessage",
    "Wake",
    "all_kinds",
    "kind_for",
    "model_for",
    "spec_for",
]
