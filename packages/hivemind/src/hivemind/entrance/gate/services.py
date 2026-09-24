"""Define what every route and view is handed: the Entrance's services, per listener and shared.

A Landing Board route is thin (ADR-0032): it validates, authorises, calls a subsystem's public API
and shapes the reply. What it calls is here, built once by the composition root: ``QueenDoor``
(the Queen's methods the Entrance writes through), ``HiveReads`` (the stores it reads directly,
since reading never changes state), ``PushServices``, ``GateGuards`` (the Guard's enforcer, the
denial-burst lock, the rate limiter), ``EntranceRules`` (the thresholds routes decide with),
``DoorControl`` (the running listeners, as routes see them) and the Entrance's own reducer, streams
and sockets, in one ``EntranceServices``. ``ListenerDeps`` is what
differs per listener: the login and enrolment dependencies carrying that listener's WebAuthn
relying party (``localhost`` on loopback, the exposure plan's name on the remote listener).
FastAPI hands them to routes through two dependencies, ``get_services`` and ``get_listener``, which
each application overrides with its own; neither is ever called unbound.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.gate``. Built by the
    Entrance's composition (``hive serve``); read by every route in ``hivemind.entrance.routes``
    and every view in ``hivemind.entrance.streams``. Calls into the Queen's and the Entrance's
    public types only.

Key invariants:
    - Every write a route makes goes through ``QueenDoor`` or an Entrance flow; ``HiveReads`` is
      only ever read.
    - A route never reaches for a service outside this bundle.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md, "The Entrance lives in the
      Queen's process and every write goes through her".
    - hivemind.entrance.app for where each application binds its own services.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from hivemind.brood_chamber import AnswerSource, BroodChamber, Task
from hivemind.cell import HoneyClearance
from hivemind.common.errors import InvariantViolationError
from hivemind.entrance.auth.confirm import DEFAULT_CONFIRMATION_TTL
from hivemind.entrance.auth.limits import DenialCounter, RateLimiter
from hivemind.entrance.auth.login import AuthDeps
from hivemind.entrance.auth.session.models import Listener
from hivemind.entrance.enrol.deps import EnrolmentDeps
from hivemind.entrance.push import LivePush, PushDispatcher
from hivemind.entrance.reducer import EntranceReducer
from hivemind.guard import Enforcer
from hivemind.queen import ChatLog, GoalRequest, GoalRequestStore, HumanInbox
from hivemind.supervision import Alarm
from waggle.clock import Clock
from waggle.ids import DeviceId, MessageId, TaskId

if TYPE_CHECKING:
    # Type-only: the streams package imports this module's services, so a runtime import cycles.
    from hivemind.entrance.streams.hub import StreamHub
    from hivemind.entrance.streams.registry import SocketRegistry

__all__ = [
    "DoorControl",
    "EntranceRules",
    "EntranceServices",
    "GateGuards",
    "HiveReads",
    "ListenerDeps",
    "PushServices",
    "QueenDoor",
    "StreamServices",
    "get_listener",
    "get_services",
]


class QueenDoor(Protocol):
    """The Queen's methods the Entrance writes through (``hivemind.queen.Queen`` has them all)."""

    @property
    def human_inbox(self) -> HumanInbox:
        """The Alarms waiting on the human (and, through the chamber, the questions)."""
        ...

    async def request_goal(self, request: GoalRequest) -> str:
        """Commit a goal request and wake the Queen; see ``Queen.request_goal``."""
        ...

    async def confirm_goal_request(self, request_id: str) -> GoalRequest:
        """Confirm a request held for the human's yes; see ``Queen.confirm_goal_request``."""
        ...

    async def decline_goal_request(self, request_id: str, reason: str) -> GoalRequest:
        """Refuse a request held for the human's yes; see ``Queen.decline_goal_request``."""
        ...

    async def post_human_message(
        self, text: str, device_id: DeviceId, task_id: TaskId | None = None
    ) -> str:
        """Append the human's words to the chat and wake her; see ``Queen.post_human_message``."""
        ...

    async def acknowledge_alarm(self, alarm_id: str) -> bool:
        """Resolve an Alarm the human acknowledged; see ``Queen.acknowledge_alarm``."""
        ...

    async def answer_question(
        self,
        question_id: MessageId,
        text: str,
        *,
        source: AnswerSource = ...,
        clearance: HoneyClearance = ...,
        chosen_option: int | None = None,
    ) -> Task:
        """Record the human's answer and forward it; see ``Queen.answer_question``."""
        ...

    async def escalate_to_human(self, alarm: Alarm) -> None:
        """Put an Alarm the Hive raised in front of the human; see ``Queen.escalate_to_human``."""
        ...

    async def cancel_goal(self, goal_id: TaskId, reason: str) -> bool:
        """Stop a goal, placed work on its Warden first; see ``Queen.cancel_goal``."""
        ...

    async def refuse_device_requests(self, device_id: DeviceId, reason: str) -> tuple[str, ...]:
        """Refuse a revoked device's unplanned requests; see ``Queen.refuse_device_requests``."""
        ...


class DoorControl(Protocol):
    """The running listeners as routes see them; the Entrance runtime implements it."""

    @property
    def exposed(self) -> bool:
        """Whether the manifest asks for a remote listener at all."""
        ...

    @property
    def remote_listening(self) -> bool:
        """Whether the remote listener is serving right now."""
        ...

    async def device_revoked(self, device_id: DeviceId) -> None:
        """Rebuild the remote listener's mutual-TLS revocation list after a revocation.

        Args:
            device_id: The device just revoked.
        """
        ...


@dataclass(frozen=True, slots=True)
class HiveReads:
    """The Hive's stores the Entrance reads directly (reading never changes state, ADR-0032).

    Attributes:
        goal_requests: The Queen's goal-request table.
        chat: The Queen's chat log.
        chamber: The Brood Chamber (questions waiting on the human).
    """

    goal_requests: GoalRequestStore
    chat: ChatLog
    chamber: BroodChamber


@dataclass(frozen=True, slots=True)
class PushServices:
    """The push channel as routes see it.

    Attributes:
        dispatcher: Registers and deletes subscriptions.
        live: The live WebSocket hub ``/v1/push/stream`` attaches to.
        hive_public_key: The Hive's Ed25519 public key, raw: what a webhook receiver verifies.
        vapid_public_key: The VAPID application server key a browser subscribes with; None when
            ``[entrance.push] web_push`` is off.
    """

    dispatcher: PushDispatcher
    live: LivePush
    hive_public_key: bytes
    vapid_public_key: str | None


@dataclass(frozen=True, slots=True)
class GateGuards:
    """What bounds and authorises every request.

    Attributes:
        enforcer: The Guard's enforcer; the Entrance route point checks every capability.
        denials: Counts capability denials; a burst locks the device.
        limiter: The per-device and per-address token buckets.
    """

    enforcer: Enforcer
    denials: DenialCounter
    limiter: RateLimiter


@dataclass(frozen=True, slots=True)
class EntranceRules:
    """The thresholds routes decide with, from the Hive Manifest.

    Attributes:
        step_up_spend: ``[entrance] step_up_spend``: a goal budget above it needs step-up.
        goal_spend_cap_usd: ``[forage] spend_cap_per_goal_usd``: what a goal without a budget of
            its own may spend, so what it counts for against a device's daily cap.
        steward_devices: ``[entrance] steward_devices``: the steward route is mounted.
        confirmation_ttl: How long a held request waits for a person.
    """

    step_up_spend: float
    goal_spend_cap_usd: float
    steward_devices: bool = False
    confirmation_ttl: timedelta = DEFAULT_CONFIRMATION_TTL


@dataclass(frozen=True, slots=True)
class StreamServices:
    """The Entrance's live streams.

    Attributes:
        hub: Follows the trail once and fans events out to every view.
        sockets: Every live socket, by session, device and listener, so each closes on time.
    """

    hub: StreamHub
    sockets: SocketRegistry


@dataclass(frozen=True, slots=True)
class ListenerDeps:
    """What differs per listener: the relying party its logins and enrolments are bound to.

    Attributes:
        listener: The listener.
        auth: Login, sessions and step-up, with this listener's relying party only.
        enrolment: Enrolment, with this listener's relying party.
    """

    listener: Listener
    auth: AuthDeps
    enrolment: EnrolmentDeps


@dataclass(frozen=True, slots=True)
class EntranceServices:
    """Everything a route or a view may call, built once by the composition root.

    Attributes:
        queen: The Queen's door: every write into the Hive.
        hive: The stores routes read.
        listeners: Each served listener's own dependencies.
        push: The push channel.
        guards: The enforcer, the denial counter and the rate limiter.
        reducer: The Entrance Reducer.
        streams: The stream hub and the socket registry.
        rules: The thresholds routes decide with.
        door: The running listeners.
        clock: The Entrance's clock.
    """

    queen: QueenDoor
    hive: HiveReads
    listeners: Mapping[Listener, ListenerDeps]
    push: PushServices
    guards: GateGuards
    reducer: EntranceReducer
    streams: StreamServices
    rules: EntranceRules
    door: DoorControl
    clock: Clock

    @property
    def enrolment(self) -> EnrolmentDeps:
        """The loopback listener's enrolment dependencies: the tables, trail, clock and seams."""
        return self.listeners[Listener.LOOPBACK].enrolment


def get_services() -> EntranceServices:
    """Stand in for the application's services; each application overrides this dependency.

    Returns:
        Never returns unbound.

    Raises:
        InvariantViolationError: Always: an application was built without its services.
    """
    raise InvariantViolationError("An Entrance application was built without its services.")


def get_listener() -> ListenerDeps:
    """Stand in for the listener's dependencies; each application overrides this dependency.

    Returns:
        Never returns unbound.

    Raises:
        InvariantViolationError: Always: an application was built without its listener.
    """
    raise InvariantViolationError("An Entrance application was built without its listener.")
