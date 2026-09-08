"""Re-export the public face of every Waggle message family: the messages package.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance); a message
is a frozen pydantic model, one per kind, that travels as the payload of an Envelope (the outer
wrapper every Waggle message travels in). The package holds one module per family (task,
supervision, forage, cell, session, honey, tool, capping, swarm, control), split by
responsibility where a family outgrew one file, plus the shared base, labels and reports, the
catalogue (the one kind list) and the registry that indexes it. This file re-exports every
message class, the shared labels and reports, the two base classes and the registry API, so a
caller writes ``from waggle.messages import TaskAssign`` without knowing which file a family
was split into. Under the codingrules 5.1 file limit it cannot also carry each family's enums,
value models and bounds: those stay importable from the family module that defines them
(``waggle.messages.task.WorkerRole``), and the shared constants, the id field aliases,
``UtcDatetime``, ``check_id`` and ``id_validator`` from ``waggle.messages.base``.

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
      HoneyClearance, OsFamily, Postcondition, PostconditionKind, Tempo, Urgency.
    - Reports (reports): CellCapabilitiesReport, GpuReport, HostCapacityReport, PlatformReport.
    - Task (task, task_reports): TaskAssign, TaskProgress, TaskResult, TaskCancel, TaskPause,
      TaskResume.
    - Supervision (supervision, supervision_alarms, supervision_questions): Heartbeat,
      AlarmRaised, AlarmResolved, Inspect, InspectReply, Intervene, Question, Answer.
    - Forage (forage, forage_hosting): CapacityReport, GrantIssued, GrantRevoked, ForageRequest,
      ForageReply, HostingDecided, CeilingsSet, PlanWritten.
    - Cell (cell, cell_leases, cell_wax): CellReady, CellHeartbeat, CellTeardownRequest,
      CellRequest, LeaseOpened, LeaseReleased, CellWaxProposed, CellWaxWritten, CellWaxCleared.
    - Session (session, session_output, session_files): SessionOpen, SessionExec, SessionStdin,
      SessionOutput, SessionExit, SessionPutFile, SessionGetFile, SessionClose.
    - Honey (honey): NectarDeposit, HoneyQuery, HoneyResponse.
    - Tool (tool, tool_call): ToolRequest, ToolPromoted, ToolInvoke, ToolResult.
    - Capping (capping, capping_verdict): ProposalSubmitted, CheckResult, Verdict,
      PostconditionResult, RollbackDone.
    - Swarm (swarm, swarm_colonized): EnrolRequest, EnrolAccept, DeviceHeartbeat, NucPromote,
      NucPromoted, TrailSegmentSync.
    - Control (control, control_hive): Ping, Pong, ErrorMessage, Shutdown, Cluster, Wake,
      HumanMessage, MaskOverride, QueenMoved.
    - Registry (registry): MessageSpec, MESSAGE_SPECS, spec_for, model_for, kind_for,
      all_kinds.
"""

from waggle.messages.base import MessageShape, WaggleMessage
from waggle.messages.capping import CheckResult, ProposalSubmitted
from waggle.messages.capping_verdict import PostconditionResult, RollbackDone, Verdict
from waggle.messages.cell import CellHeartbeat, CellReady
from waggle.messages.cell_leases import CellRequest, CellTeardownRequest, LeaseOpened, LeaseReleased
from waggle.messages.cell_wax import CellWaxCleared, CellWaxProposed, CellWaxWritten
from waggle.messages.control import Cluster, ErrorMessage, Ping, Pong, Shutdown, Wake
from waggle.messages.control_hive import HumanMessage, MaskOverride, QueenMoved
from waggle.messages.forage import ForageReply, ForageRequest, GrantIssued, GrantRevoked
from waggle.messages.forage_hosting import CapacityReport, CeilingsSet, HostingDecided, PlanWritten
from waggle.messages.honey import HoneyQuery, HoneyResponse, NectarDeposit
from waggle.messages.labels import (
    AccessLevel,
    AccuracyBar,
    AlarmSeverity,
    CombShieldLevel,
    HandoffRef,
    HoneyClearance,
    OsFamily,
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
from waggle.messages.session import SessionClose, SessionExec, SessionOpen, SessionStdin
from waggle.messages.session_files import SessionGetFile, SessionPutFile
from waggle.messages.session_output import SessionExit, SessionOutput
from waggle.messages.supervision import Heartbeat, Inspect, InspectReply, Intervene
from waggle.messages.supervision_alarms import AlarmRaised, AlarmResolved
from waggle.messages.supervision_questions import Answer, Question
from waggle.messages.swarm import DeviceHeartbeat, EnrolAccept, EnrolRequest
from waggle.messages.swarm_colonized import NucPromote, NucPromoted, TrailSegmentSync
from waggle.messages.task import TaskAssign, TaskCancel, TaskPause, TaskResume
from waggle.messages.task_reports import TaskProgress, TaskResult
from waggle.messages.tool import ToolPromoted, ToolRequest
from waggle.messages.tool_call import ToolInvoke, ToolResult

__all__ = [
    "MESSAGE_SPECS",
    "AccessLevel",
    "AccuracyBar",
    "AlarmRaised",
    "AlarmResolved",
    "AlarmSeverity",
    "Answer",
    "CapacityReport",
    "CeilingsSet",
    "CellCapabilitiesReport",
    "CellHeartbeat",
    "CellReady",
    "CellRequest",
    "CellTeardownRequest",
    "CellWaxCleared",
    "CellWaxProposed",
    "CellWaxWritten",
    "CheckResult",
    "Cluster",
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
    "NucPromote",
    "NucPromoted",
    "OsFamily",
    "Ping",
    "PlanWritten",
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
    "SessionExec",
    "SessionExit",
    "SessionGetFile",
    "SessionOpen",
    "SessionOutput",
    "SessionPutFile",
    "SessionStdin",
    "Shutdown",
    "TaskAssign",
    "TaskCancel",
    "TaskPause",
    "TaskProgress",
    "TaskResult",
    "TaskResume",
    "Tempo",
    "ToolInvoke",
    "ToolPromoted",
    "ToolRequest",
    "ToolResult",
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
