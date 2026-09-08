"""Re-export the supervision family: how a bee is watched, alarmed about, questioned and steered.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Supervision
is how a bee is watched and steered: a Worker reports to its Warden (the always-on supervisor of
one Cell) and a Warden to the Queen (the central orchestrator), and the supervisor above pulls
levers back down. ``oversight`` holds the heartbeat, the inspect request with its reply and the
intervene lever; ``telemetry`` the state and context figures those carry; ``alarms`` an issue
escalated up the tree and closed at every level; ``questions`` a blocking question raised up the
chain and its answer back down. This package is the family's face: a caller imports any of its
messages, enums or value models from here without knowing which module defines them. The bounds
each module names stay in that module, because the spec makes the number normative, not the name.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages (the catalogue and
    the package face) and by every bee that builds or reads a supervision payload; calls into
    nothing beyond its own modules.

Key invariants:
    - Every class the registry registers under ``supervision.*`` is re-exported here
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; no message, enum or bound is defined here.

See Also:
    - docs/waggle/spec.md section 8.3 for the family's normative fields and rules.
    - waggle.messages.supervision.oversight, waggle.messages.supervision.telemetry,
      waggle.messages.supervision.alarms and waggle.messages.supervision.questions for the
      definitions.

Public API:
    - Oversight (oversight): Heartbeat, Inspect, InspectReply, Intervene, InterventionAction.
    - Telemetry (telemetry): ChildTelemetry, CompactView, ContextTelemetry, WardenState,
      WorkerState.
    - Alarms (alarms): AlarmContext, AlarmKind, AlarmRaised, AlarmResolution, AlarmResolved.
    - Questions (questions): Answer, AnswerSource, Question.
"""

from waggle.messages.supervision.alarms import (
    AlarmContext,
    AlarmKind,
    AlarmRaised,
    AlarmResolution,
    AlarmResolved,
)
from waggle.messages.supervision.oversight import (
    Heartbeat,
    Inspect,
    InspectReply,
    Intervene,
    InterventionAction,
)
from waggle.messages.supervision.questions import Answer, AnswerSource, Question
from waggle.messages.supervision.telemetry import (
    ChildTelemetry,
    CompactView,
    ContextTelemetry,
    WardenState,
    WorkerState,
)

__all__ = [
    "AlarmContext",
    "AlarmKind",
    "AlarmRaised",
    "AlarmResolution",
    "AlarmResolved",
    "Answer",
    "AnswerSource",
    "ChildTelemetry",
    "CompactView",
    "ContextTelemetry",
    "Heartbeat",
    "Inspect",
    "InspectReply",
    "Intervene",
    "InterventionAction",
    "Question",
    "WardenState",
    "WorkerState",
]
