"""Re-export the task family: a task handed down the tree and its progress reported back up.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance), and a task
is one placed unit of work that the Queen (the central orchestrator) hands to a Cell's Warden (the
always-on supervisor of one Cell, a unit of compute) and the Warden to a Worker (the bee that does
the work). The family is split by direction: ``assignment`` carries the downward orders (assign,
then cancel, pause or resume) and ``reports`` the upward events (progress on an attempt and the
result that closes it). This package is the family's face: a caller imports any of its messages,
enums or value models from here without knowing which module defines them. The bounds each module
names stay in that module, because the spec makes the number normative, not the name.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages (the catalogue and
    the package face) and by every bee that builds or reads a task payload; calls into
    nothing beyond its own modules.

Key invariants:
    - Every class the registry registers under ``task.*`` is re-exported here
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; no message, enum or bound is defined here.

See Also:
    - docs/waggle/spec.md section 8.2 for the family's normative fields and rules.
    - waggle.messages.task.assignment and waggle.messages.task.reports for the definitions.

Public API:
    - Assignment (assignment): TaskAssign, TaskCancel, TaskPause, TaskResume, WorkerRole.
    - Reports (reports): ArtifactRef, TaskOutcome, TaskProgress, TaskResult, TaskStage.
"""

from waggle.messages.task.assignment import (
    TaskAssign,
    TaskCancel,
    TaskPause,
    TaskResume,
    WorkerRole,
)
from waggle.messages.task.reports import (
    ArtifactRef,
    TaskOutcome,
    TaskProgress,
    TaskResult,
    TaskStage,
)

__all__ = [
    "ArtifactRef",
    "TaskAssign",
    "TaskCancel",
    "TaskOutcome",
    "TaskPause",
    "TaskProgress",
    "TaskResult",
    "TaskResume",
    "TaskStage",
    "WorkerRole",
]
