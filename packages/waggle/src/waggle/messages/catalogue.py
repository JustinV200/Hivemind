"""List every registered Waggle kind with its class, shape and reply kind: the one kind list.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Every
frame carries a ``kind`` string of the form ``<family>.<snake_name>`` that names the pydantic
model its payload validates with, and this module is the only place in the code that list
lives: one row per kind, in the order of the spec's catalogue table, naming the kind, its
message class, its ``MessageShape`` (request, reply or event, which decides what an envelope's
``correlation_id`` must hold) and the kind it replies to or usually follows from. A message
class never carries its own kind, so renaming a kind, changing its shape or adding a family is
a change to this file and nothing else (plus the spec, which the drift test keeps in step).
The rows are plain tuples rather than ``MessageSpec`` values because
``waggle.messages.registry`` defines that dataclass and builds its lookups from this list: a
list of specs here would import the registry while the registry imports the list.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Read by waggle.messages.registry only, which turns
    each row into a MessageSpec and indexes it; imports every family module under
    waggle.messages, and no family module ever imports it back.

Key invariants:
    - CATALOGUE holds one row per kind of the spec's catalogue table, in the table's order,
      with the table's class, shape and replies_to; tests/test_spec_drift.py fails until the
      two list the same sixty-six kinds in both directions.
    - This module is data only: the registry checks the kind pattern, uniqueness and dangling
      replies when it indexes the rows, and refuses to import otherwise.
    - control.error is a reply with no replies_to: it answers any kind (spec section 8.11).

See Also:
    - docs/waggle/spec.md section 3 (shapes) and section 8 (the catalogue table).
    - waggle.messages.registry for MessageSpec and the lookups built from this list.
    - waggle.messages.base for MessageShape and WaggleMessage.
"""

from __future__ import annotations

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

__all__ = ["CATALOGUE", "CatalogueRow"]

# One row of the catalogue: (kind, class, shape, replies_to), the first four columns of the
# spec's table in the table's order; the registry turns each into a MessageSpec.
type CatalogueRow = tuple[str, type[WaggleMessage], MessageShape, str | None]

# The three shapes under short names, so every row below fits on one line and reads like the
# table it mirrors.
_REQUEST = MessageShape.REQUEST
_REPLY = MessageShape.REPLY
_EVENT = MessageShape.EVENT

# The complete kind list, in the spec's catalogue order. Shape and replies_to are the values of
# spec section 3; the drift test holds this tuple and the catalogue table to each other. A
# replies_to on an event names the request it usually follows from, never a requirement.
CATALOGUE: tuple[CatalogueRow, ...] = (
    # task (spec section 8.2): placing, steering and closing one task.
    ("task.assign", TaskAssign, _REQUEST, None),
    ("task.progress", TaskProgress, _EVENT, "task.assign"),
    ("task.result", TaskResult, _EVENT, "task.assign"),
    ("task.cancel", TaskCancel, _EVENT, None),
    ("task.pause", TaskPause, _EVENT, None),
    ("task.resume", TaskResume, _EVENT, None),
    # supervision (spec section 8.3): liveness, alarms, inspection and questions.
    ("supervision.heartbeat", Heartbeat, _EVENT, None),
    ("supervision.alarm_raised", AlarmRaised, _EVENT, None),
    ("supervision.alarm_resolved", AlarmResolved, _EVENT, None),
    ("supervision.inspect", Inspect, _REQUEST, None),
    ("supervision.inspect_reply", InspectReply, _REPLY, "supervision.inspect"),
    ("supervision.intervene", Intervene, _EVENT, None),
    ("supervision.question", Question, _REQUEST, None),
    ("supervision.answer", Answer, _REPLY, "supervision.question"),
    # forage (spec section 8.4): capacity as data, grants and hosting decisions.
    ("forage.capacity_report", CapacityReport, _EVENT, None),
    ("forage.grant_issued", GrantIssued, _EVENT, None),
    ("forage.grant_revoked", GrantRevoked, _EVENT, None),
    ("forage.request", ForageRequest, _REQUEST, None),
    ("forage.reply", ForageReply, _REPLY, "forage.request"),
    ("forage.hosting_decided", HostingDecided, _EVENT, None),
    ("forage.ceilings_set", CeilingsSet, _EVENT, None),
    ("forage.plan_written", PlanWritten, _EVENT, None),
    # cell (spec section 8.5): readiness, leases and Cell Wax cautions.
    ("cell.ready", CellReady, _EVENT, None),
    ("cell.heartbeat", CellHeartbeat, _EVENT, None),
    ("cell.teardown_request", CellTeardownRequest, _REQUEST, None),
    ("cell.request", CellRequest, _REQUEST, None),
    ("cell.lease_opened", LeaseOpened, _EVENT, "cell.request"),
    ("cell.lease_released", LeaseReleased, _EVENT, "cell.teardown_request"),
    ("cell.wax_proposed", CellWaxProposed, _REQUEST, None),
    ("cell.wax_written", CellWaxWritten, _EVENT, "cell.wax_proposed"),
    ("cell.wax_cleared", CellWaxCleared, _EVENT, "cell.wax_proposed"),
    # session (spec section 8.6): the terminal session a Warden drives on a device.
    ("session.open", SessionOpen, _REQUEST, None),
    ("session.exec", SessionExec, _REQUEST, None),
    ("session.stdin", SessionStdin, _EVENT, None),
    ("session.output", SessionOutput, _EVENT, "session.exec"),
    ("session.exit", SessionExit, _EVENT, "session.exec"),
    ("session.put_file", SessionPutFile, _EVENT, None),
    ("session.get_file", SessionGetFile, _REQUEST, None),
    ("session.close", SessionClose, _EVENT, None),
    # honey (spec section 8.7): raw Nectar in, distilled Honey out.
    ("honey.nectar_deposit", NectarDeposit, _EVENT, None),
    ("honey.query", HoneyQuery, _REQUEST, None),
    ("honey.response", HoneyResponse, _REPLY, "honey.query"),
    # tool (spec section 8.8): requesting, promoting and invoking tools.
    ("tool.request", ToolRequest, _REQUEST, None),
    ("tool.promoted", ToolPromoted, _EVENT, "tool.request"),
    ("tool.invoke", ToolInvoke, _REQUEST, None),
    ("tool.result", ToolResult, _REPLY, "tool.invoke"),
    # capping (spec section 8.9): the gate every proposed action passes through.
    ("capping.proposal_submitted", ProposalSubmitted, _REQUEST, None),
    ("capping.check_result", CheckResult, _EVENT, "capping.proposal_submitted"),
    ("capping.verdict", Verdict, _REPLY, "capping.proposal_submitted"),
    ("capping.postcondition_result", PostconditionResult, _EVENT, "capping.proposal_submitted"),
    ("capping.rollback_done", RollbackDone, _EVENT, "capping.proposal_submitted"),
    # swarm (spec section 8.10): enrolment, device liveness, local model servers and trails.
    ("swarm.enrol_request", EnrolRequest, _REQUEST, None),
    ("swarm.enrol_accept", EnrolAccept, _REPLY, "swarm.enrol_request"),
    ("swarm.device_heartbeat", DeviceHeartbeat, _EVENT, None),
    ("swarm.nuc_promote", NucPromote, _REQUEST, None),
    ("swarm.nuc_promoted", NucPromoted, _REPLY, "swarm.nuc_promote"),
    ("swarm.trail_segment_sync", TrailSegmentSync, _EVENT, None),
    # control (spec section 8.11): housekeeping and Hive-wide orders.
    ("control.ping", Ping, _REQUEST, None),
    ("control.pong", Pong, _REPLY, "control.ping"),
    # control.error answers any kind, so it registers no replies_to; its shape still requires a
    # correlation_id (spec section 3).
    ("control.error", ErrorMessage, _REPLY, None),
    ("control.shutdown", Shutdown, _EVENT, None),
    ("control.cluster", Cluster, _EVENT, None),
    ("control.wake", Wake, _EVENT, None),
    ("control.human_message", HumanMessage, _EVENT, None),
    ("control.mask_override", MaskOverride, _EVENT, None),
    ("control.queen_moved", QueenMoved, _EVENT, None),
)
