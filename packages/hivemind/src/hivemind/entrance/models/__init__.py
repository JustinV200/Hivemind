"""Hold the Landing Board's request, response and frame models, one module per resource.

The Landing Board (the Hive Entrance's versioned API) is a published contract (ADR-0034): programs
are written from ``docs/entrance/openapi.json`` alone, and FastAPI generates that document from
these pydantic v2 models. Keeping them apart from the routes keeps each route module to its thin
handlers, and lets the stream views send the same shapes the routes answer with. Every model
forbids unknown fields, bounds every string and documents every field; the ``*_view`` functions
shape a stored record into its public view, leaving behind what a client never sees (key
material, a goal's text where it is not ``C2``-gated).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Used by
    ``hivemind.entrance.routes`` and ``hivemind.entrance.streams``; generated into the OpenAPI
    document by ``hivemind.entrance.landing_board``. Calls into the Hive's own record types.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - A change here is a contract change: additive within ``/v1/`` (ADR-0034).

See Also:
    - docs/adr/0034-landing-board-versioning-and-push.md for the versioning rule.

Public API:
    - ChallengeRequest, ChallengeView, LoginRequest, OpenedSessionView, StepUpRequest,
      SteppedUpView: login and step-up (auth).
    - HiveView, PasskeyOptionsRequest, PasskeyOptionsView, Ed25519Redemption,
      PasskeyRedemption, RedemptionView: enrolment (enrol).
    - ModeView, ChangedView, InviteRequest, InviteView, ApprovalBody, DenyBody,
      ConfirmationView, ConfirmationList, ConfirmedView, confirmation_view: the door (door).
    - DeviceView, DeviceList, RevokeBody, RevocationView, WidenBody, device_view: devices.
    - GoalSubmission, GoalAccepted, GoalView, DeclineBody, goal_view: goals.
    - ChatLine, ChatPage, ChatPost, ChatAccepted, chat_line: the chat.
    - QuestionView, AlarmView, InboxView, AnswerBody, AnsweredView, AcknowledgedView,
      question_view, alarm_view: the human's inbox (inbox).
    - SubscribeBody, SubscriptionView, VapidKeyView, HiveKeyView, subscription_view: push.
    - ChatFrame, SecurityFrame, SecurityEventView, security_frame: stream frames (streams).
"""

from hivemind.entrance.models.auth import (
    ChallengeRequest,
    ChallengeView,
    LoginRequest,
    OpenedSessionView,
    SteppedUpView,
    StepUpRequest,
)
from hivemind.entrance.models.chat import ChatAccepted, ChatLine, ChatPage, ChatPost, chat_line
from hivemind.entrance.models.devices import (
    DeviceList,
    DeviceView,
    RevocationView,
    RevokeBody,
    WidenBody,
    device_view,
)
from hivemind.entrance.models.door import (
    ApprovalBody,
    ChangedView,
    ConfirmationList,
    ConfirmationView,
    ConfirmedView,
    DenyBody,
    InviteRequest,
    InviteView,
    ModeView,
    confirmation_view,
)
from hivemind.entrance.models.enrol import (
    Ed25519Redemption,
    HiveView,
    PasskeyOptionsRequest,
    PasskeyOptionsView,
    PasskeyRedemption,
    RedemptionView,
)
from hivemind.entrance.models.goals import (
    DeclineBody,
    GoalAccepted,
    GoalSubmission,
    GoalView,
    goal_view,
)
from hivemind.entrance.models.inbox import (
    AcknowledgedView,
    AlarmView,
    AnswerBody,
    AnsweredView,
    InboxView,
    QuestionView,
    alarm_view,
    question_view,
)
from hivemind.entrance.models.push import (
    HiveKeyView,
    SubscribeBody,
    SubscriptionView,
    VapidKeyView,
    subscription_view,
)
from hivemind.entrance.models.streams import (
    ChatFrame,
    SecurityEventView,
    SecurityFrame,
    security_frame,
)

__all__ = [
    "AcknowledgedView",
    "AlarmView",
    "AnswerBody",
    "AnsweredView",
    "ApprovalBody",
    "ChallengeRequest",
    "ChallengeView",
    "ChangedView",
    "ChatAccepted",
    "ChatFrame",
    "ChatLine",
    "ChatPage",
    "ChatPost",
    "ConfirmationList",
    "ConfirmationView",
    "ConfirmedView",
    "DeclineBody",
    "DenyBody",
    "DeviceList",
    "DeviceView",
    "Ed25519Redemption",
    "GoalAccepted",
    "GoalSubmission",
    "GoalView",
    "HiveKeyView",
    "HiveView",
    "InboxView",
    "InviteRequest",
    "InviteView",
    "LoginRequest",
    "ModeView",
    "OpenedSessionView",
    "PasskeyOptionsRequest",
    "PasskeyOptionsView",
    "PasskeyRedemption",
    "QuestionView",
    "RedemptionView",
    "RevocationView",
    "RevokeBody",
    "SecurityEventView",
    "SecurityFrame",
    "StepUpRequest",
    "SteppedUpView",
    "SubscribeBody",
    "SubscriptionView",
    "VapidKeyView",
    "WidenBody",
    "alarm_view",
    "chat_line",
    "confirmation_view",
    "device_view",
    "goal_view",
    "question_view",
    "security_frame",
    "subscription_view",
]
