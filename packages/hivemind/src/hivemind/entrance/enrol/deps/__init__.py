"""Hold what device enrolment is built from: its dependency bundle, its identity and its seams.

Enrolment at the Hive Entrance (the Hive's one HTTP door; ADR-0041) is a set of functions over one
``EnrolmentDeps``: the Entrance tables and the Pheromone Trail (audit log), the Guard policy and
the ``[entrance]`` lifetimes, the Hive's key and the passkey ceremony's relying party and challenge
book, and three seams that later roadmap steps implement: ``SecurityNotifier`` (push "something
happened to device X" to every other device, 10.5b), ``DeviceOffboarder`` (end a device's sessions
and push subscriptions when it leaves APPROVED, 10.5e and 10.5b) and ``GoalLedger`` (list and
cancel a revoked device's goals, the Queen's goal table). Each seam ships a documented no-op, the
default until its step lands, and a recording fake for tests. ``EntranceIdentity`` is who every
``guard.entrance_*`` event is recorded as, and the one place such an event is built.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. Built by a
    composition root; read by every enrolment flow in ``hivemind.entrance.enrol`` and by the
    console bootstrap (the identity). Calls into ``hivemind.entrance.auth``, ``hivemind.guard``,
    ``hivemind.pheromone`` and waggle.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Every seam's default is a no-op that is safe on its own: the trail records every event
      whether or not anyone is told, and no device can hold a session, a subscription or a goal
      before the steps that implement those seams exist.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for enrolment.
    - docs/adr/0042-landing-board-versioning-and-push.md for what a security push carries.

Public API:
    - EnrolmentDeps, EnrolmentRecords, EnrolmentRules, EnrolmentCeremony, EnrolmentSeams: the
      bundle every flow takes (bundle).
    - EntranceIdentity: the Hive, node and actor events are stamped with (identity).
    - SecurityNotice, SecurityNotifier, NullSecurityNotifier: the security-push seam (notifier).
    - DeviceOffboarder, NullDeviceOffboarder: the session and subscription seam (offboarder).
    - GoalLedger, NullGoalLedger: the open-goal seam (goals).
    - RecordingSecurityNotifier, RecordingDeviceOffboarder, FakeGoalLedger: fakes (fake).
"""

from hivemind.entrance.enrol.deps.bundle import (
    EnrolmentCeremony,
    EnrolmentDeps,
    EnrolmentRecords,
    EnrolmentRules,
    EnrolmentSeams,
)
from hivemind.entrance.enrol.deps.fake import (
    FakeGoalLedger,
    RecordingDeviceOffboarder,
    RecordingSecurityNotifier,
)
from hivemind.entrance.enrol.deps.goals import GoalLedger, NullGoalLedger
from hivemind.entrance.enrol.deps.identity import EntranceIdentity
from hivemind.entrance.enrol.deps.notifier import (
    NullSecurityNotifier,
    SecurityNotice,
    SecurityNotifier,
)
from hivemind.entrance.enrol.deps.offboarder import DeviceOffboarder, NullDeviceOffboarder

__all__ = [
    "DeviceOffboarder",
    "EnrolmentCeremony",
    "EnrolmentDeps",
    "EnrolmentRecords",
    "EnrolmentRules",
    "EnrolmentSeams",
    "EntranceIdentity",
    "FakeGoalLedger",
    "GoalLedger",
    "NullDeviceOffboarder",
    "NullGoalLedger",
    "NullSecurityNotifier",
    "RecordingDeviceOffboarder",
    "RecordingSecurityNotifier",
    "SecurityNotice",
    "SecurityNotifier",
]
