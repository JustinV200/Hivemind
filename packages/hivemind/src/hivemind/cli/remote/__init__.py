"""Reach a remote Hive from this laptop, as a device it enrolled: enrol, run a goal, answer.

Every client of the Hive Entrance (the Hive's one HTTP door) is a device enrolled with its own key
and approved at the Hive Stand, logging in with that key plus the operator's password (ADR-0033);
a laptop's ``hive`` is one. ``hive remote enrol`` mints the laptop's Ed25519 key and redeems the
invite the operator minted, keeping a profile (the Entrance, the Hive's id, the device id) with the
key beside it in the laptop's own secret store; ``hive run --remote`` submits a goal through the
Entrance and follows it to its end; ``hive inbox --remote`` reads and answers what waits on the
human. Roadmap step 10.8 and phase 10's second exit criterion.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli``. ``app`` is registered on the root ``hive``
    application by ``hivemind.cli.app``; ``remote_run`` and the ``remote_*`` inbox functions are
    called by ``hivemind.cli.run`` and ``hivemind.cli.readback.inbox``. Calls into
    ``hivemind.cli.landing`` and ``hivemind.entrance``'s models.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - The device's private key is only ever in the laptop's secret store, never in a profile file.

See Also:
    - hivemind.cli.landing for the signing client.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md.

Public API:
    - app, RemoteRun, remote_run, remote_inbox, remote_answer, remote_acknowledge, CA_FILE: the
      commands (commands).
    - RemoteProfile, ProfileStore, DEFAULT_PROFILE, check_profile_name: profiles and their keys
      (profiles).
    - EnrolmentOrder, InviteLink, OfflineEnrolment, enrol_device, enrol_offline,
      read_invite_link: enrolment, online or offline (enrol).
    - remote_session, run_remote, REMOTE, PROFILE: logging in as the device (session).
    - GoalAsk, FollowPace, FollowOutcome, Follower, submit_and_follow, follow_goal: a goal followed
      to its end (goals).
    - chat_lines, inbox_lines, outcome_line: what is printed (render).
"""

from hivemind.cli.remote.commands import (
    CA_FILE,
    RemoteRun,
    app,
    remote_acknowledge,
    remote_answer,
    remote_inbox,
    remote_run,
)
from hivemind.cli.remote.enrol import (
    EnrolmentOrder,
    InviteLink,
    OfflineEnrolment,
    enrol_device,
    enrol_offline,
    read_invite_link,
)
from hivemind.cli.remote.goals import (
    Follower,
    FollowOutcome,
    FollowPace,
    GoalAsk,
    follow_goal,
    submit_and_follow,
)
from hivemind.cli.remote.profiles import (
    DEFAULT_PROFILE,
    ProfileStore,
    RemoteProfile,
    check_profile_name,
)
from hivemind.cli.remote.render import chat_lines, inbox_lines, outcome_line
from hivemind.cli.remote.session import PROFILE, REMOTE, remote_session, run_remote

__all__ = [
    "CA_FILE",
    "DEFAULT_PROFILE",
    "PROFILE",
    "REMOTE",
    "EnrolmentOrder",
    "FollowOutcome",
    "FollowPace",
    "Follower",
    "GoalAsk",
    "InviteLink",
    "OfflineEnrolment",
    "ProfileStore",
    "RemoteProfile",
    "RemoteRun",
    "app",
    "chat_lines",
    "check_profile_name",
    "enrol_device",
    "enrol_offline",
    "follow_goal",
    "inbox_lines",
    "outcome_line",
    "read_invite_link",
    "remote_acknowledge",
    "remote_answer",
    "remote_inbox",
    "remote_run",
    "remote_session",
    "run_remote",
    "submit_and_follow",
]
