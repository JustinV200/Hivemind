"""Record a refused login, request or step-up on the trail, and hand back the one generic refusal.

Every way authentication fails at the Hive Entrance (the Hive's one HTTP door) reads the same to the
client, one ``AuthenticationFailedError`` (ADR-0041: a failure never says which factor failed), and
different to the Guard Bee, which watches the Pheromone Trail (the Hive's audit log) for failure
bursts: each refusal worth watching is a ``guard.entrance_login_failed`` event naming the reason
category (a bad device proof, a wrong password, a device that may not log in, a bad request
signature, a replayed nonce), the step it happened at, the listener and the address. Never the
proof, the token, the signature, a header value or the password: the categories are enough to
correlate, and anything more would put a credential on the trail.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.session``. Called
    by login, request and socket authentication and step-up. Calls into the Pheromone Trail
    through ``EnrolmentRecords`` and ``hivemind.entrance.errors``.

Key invariants:
    - The event is on the trail before the refusal is raised.
    - A payload carries enums, counts, the listener and a trail-safe address; nothing a client
      sent as a credential.
    - The subject is the device only when its record exists; otherwise the Hive itself.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Login is the device
      key plus the password, the key proof first".
    - hivemind.pheromone.events.families for ``guard.entrance_login_failed``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.session.models import Arrival
from hivemind.entrance.errors import AuthenticationFailedError
from waggle.ids import DeviceId

if TYPE_CHECKING:
    # Type-only: the enrolment bundle refers to the store protocol, which imports these models.
    from hivemind.entrance.enrol.deps import EnrolmentRecords

LOGIN_FAILED_KIND = "guard.entrance_login_failed"  # Every recorded refusal's trail kind.

log = get_logger(__name__)

__all__ = ["LOGIN_FAILED_KIND", "Failure", "FailureReason", "FailureStep", "record_failure"]


class FailureReason(Enum):
    """Why authentication was refused: on the trail, never told to the client."""

    PROOF = "proof"  # The device proof, its challenge or its binding key did not hold.
    PASSWORD = "password"  # noqa: S105 -- a failure category: a valid proof, a wrong password.
    DEVICE = "device"  # Unknown, not APPROVED, or its approval lapsed.
    LISTENER = "listener"  # The loopback-bound console tried the remote listener.
    REDUCED = "reduced"  # A remote login while the Entrance is reduced.
    REQUEST_SIGNATURE = "request_signature"  # A live token with a signature that did not verify.
    REQUEST_REPLAY = "request_replay"  # A live token and signature with a nonce already spent.


class FailureStep(Enum):
    """Which request a refusal answered."""

    CHALLENGE = "challenge"  # Asking for a login challenge.
    LOGIN = "login"  # Answering it with the proof and the password.
    REQUEST = "request"  # A signed HTTP request on a session.
    SOCKET = "socket"  # A WebSocket's first frame.
    STEP_UP = "step_up"  # Re-running the factors on a session.


@dataclass(frozen=True, slots=True)
class Failure:
    """One refusal worth recording.

    Attributes:
        reason: Its category.
        step: Where it happened.
        arrival: The listener and address it came from.
        device_id: The device, when its record exists; None otherwise.
        details: Counts to add to the payload (e.g. consecutive failures); never a credential.
    """

    reason: FailureReason
    step: FailureStep
    arrival: Arrival
    device_id: DeviceId | None = None
    details: Mapping[str, JsonValue] = field(default_factory=dict)


async def record_failure(records: EnrolmentRecords, failure: Failure) -> AuthenticationFailedError:
    """Record ``guard.entrance_login_failed`` for ``failure`` and return the refusal to raise.

    Args:
        records: The trail, the clock and the identity events are stamped with.
        failure: What was refused, where, and from where.

    Returns:
        The one generic ``AuthenticationFailedError``; the caller raises it (``from None``, so no
        cause reveals which check failed).
    """
    subject = failure.device_id if failure.device_id is not None else records.identity.hive_id
    payload: dict[str, JsonValue] = {
        "reason": failure.reason.value,
        "step": failure.step.value,
        "listener": failure.arrival.listener.value,
        "address": failure.arrival.trail_address,
        **failure.details,
    }
    event = records.identity.event(records.clock, LOGIN_FAILED_KIND, subject, payload)
    # Latency: one local trail write, awaited so the refusal is on record before it is raised.
    await records.trail.record(event)
    log.info(
        "entrance.authentication_refused",
        reason=failure.reason.value,
        step=failure.step.value,
        address=failure.arrival.trail_address,
    )
    return AuthenticationFailedError()
