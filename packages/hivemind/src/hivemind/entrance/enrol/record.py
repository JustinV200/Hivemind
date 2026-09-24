"""Record one enrolment step: its trail event with the state change, then cut off and notify.

Every edge of the enrolled-device state machine is a ``guard.entrance_*`` event on the Pheromone
Trail (the Hive's audit log), written in the same atomic step as the status change (codingrules
Appendix C), pushed to every other device (codingrules 8.15), and, when the device leaves APPROVED,
followed by the end of its sessions and push subscriptions (ADR-0033). Doing those four things in
the right order in every flow is exactly the kind of repetition that drifts, so this module does
them once: ``apply_transition`` builds the edge's event from a ``Transition``, has the Entrance
tables apply the change and the event together, offboards the device when it left an approval,
and tells the ``SecurityNotifier``. It also holds the small payload helpers every flow shares, so
payloads stay identifiers, counts and bounded reasons, never a code, a key or a password.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. Called by the
    invite, redemption, decision and standing flows (``invite``, ``redeem``, ``decisions``,
    ``standing``). Calls into the Entrance tables through ``EnrolmentDeps``, the enrolment seams,
    ``hivemind.entrance.enrol.state`` and ``hivemind.entrance.enrol.models``.

Key invariants:
    - The event is on the trail (with the state change) before anyone is offboarded or told, so
      a notice never points at an event that does not exist.
    - A device is offboarded exactly when it moves from APPROVED or LOCKED to anything but
      APPROVED: a lock, a revocation or an expiry; never on an unlock.
    - No payload helper here passes text through unbounded: ids are capped at
      ``MAX_IDS_ON_TRAIL`` and a reason at ``MAX_REASON_CHARS`` of display text.

See Also:
    - hivemind.entrance.enrol.state for the kind each edge is recorded as.
    - hivemind.entrance.store.protocol for the atomic status change this calls.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Unpack

from pydantic import Field, JsonValue, TypeAdapter

from hivemind.entrance.enrol.deps import EnrolmentDeps, SecurityNotice
from hivemind.entrance.enrol.models import DisplayText, EnrolledDevice
from hivemind.entrance.enrol.state import DeviceStatus, trail_kind
from hivemind.pheromone import GuardEvent
from waggle.ids import DeviceId, EventId

if TYPE_CHECKING:
    # Type-only: hivemind.entrance.store imports this package's models (see deps.bundle).
    from hivemind.entrance.store.protocol import DeviceChanges

OPERATOR_ACTOR = "human"  # The trail's literal for the operator (pheromone ACTOR_LITERALS).
MAX_IDS_ON_TRAIL = 100  # Goal ids one event names; a count carries the rest, within 16 KiB.
MAX_REASON_CHARS = 200  # An operator's reason for a denial: a sentence, not a report.
# Statuses in which a device holds an approval: leaving one for anything but APPROVED cuts it off.
_ADMITTED = frozenset({DeviceStatus.APPROVED, DeviceStatus.LOCKED})
# An operator's free-text reason: display text (no control or bidirectional characters), bounded.
_REASON: TypeAdapter[str] = TypeAdapter(
    Annotated[DisplayText, Field(min_length=1, max_length=MAX_REASON_CHARS)]
)

__all__ = [
    "MAX_IDS_ON_TRAIL",
    "MAX_REASON_CHARS",
    "OPERATOR_ACTOR",
    "Transition",
    "apply_transition",
    "iso",
    "named_ids",
    "notify",
    "reason_text",
]


@dataclass(frozen=True, slots=True)
class Transition:
    """One status change a flow asks for, who takes it, and what its trail event says.

    Attributes:
        device_id: The device to move.
        expected: The status the flow decided from; the stored one must still be it.
        new: The status to move to; ``expected`` to ``new`` must be an edge.
        actor: Who takes it: a device id, ``"human"`` (the operator at the Hive Stand) or
            ``"system"``.
        payload: What the event records beside the device: ids, counts, bounded reasons.
    """

    device_id: DeviceId
    expected: DeviceStatus
    new: DeviceStatus
    actor: str
    payload: Mapping[str, JsonValue]


async def apply_transition(
    deps: EnrolmentDeps, transition: Transition, **changes: Unpack[DeviceChanges]
) -> EnrolledDevice:
    """Apply ``transition`` with its event, offboard the device if it left an approval, notify.

    Args:
        deps: The enrolment dependencies.
        transition: The status change and its event's actor and payload.
        **changes: Fields to set in the same step (``DeviceChanges``).

    Returns:
        The device as stored after the change.

    Raises:
        InvalidDeviceTransitionError: The transition is not an edge; nothing was written.
        DeviceNotFoundError: No such device.
        DeviceStatusConflictError: The device is no longer in ``transition.expected``.
        pydantic.ValidationError: The event or the changed record breaks a model rule.
    """
    records = deps.records
    # Built first, so a bad actor or payload is refused before the tables are touched.
    kind = trail_kind(transition.expected, transition.new)
    event = records.identity.event(
        records.clock, kind, transition.device_id, transition.payload, transition.actor
    )
    # Latency: one local SQLite transaction (or an in-memory step) writing the row and the event.
    device = await records.store.update_device_status(
        transition.device_id, transition.expected, transition.new, event, **changes
    )
    # A lock, a revocation or an expiry ends the device's sessions and subscriptions (ADR-0033).
    # Latency: local table deletes and socket closes in this process; nothing waits on a device.
    if transition.expected in _ADMITTED and transition.new is not DeviceStatus.APPROVED:
        await deps.seams.offboarder.offboard(device.id, transition.new)
    await notify(deps, device.id, event)
    return device


async def notify(deps: EnrolmentDeps, device_id: DeviceId, event: GuardEvent) -> None:
    """Tell the SecurityNotifier that ``event`` happened to ``device_id``.

    Args:
        deps: The enrolment dependencies.
        device_id: The device it happened to.
        event: The recorded event; its id becomes the push notice's reference.
    """
    notice = SecurityNotice(
        device_id=device_id, event_id=EventId(event.id), kind=event.kind, at=event.at
    )
    # Latency: the notifier queues the notice and returns; delivery is never awaited here.
    await deps.seams.notifier.notify(notice)


def iso(moment: datetime | None) -> str | None:
    """Render a timestamp for a payload: ISO 8601, or None for "never".

    Args:
        moment: The time, or None.

    Returns:
        ``moment.isoformat()``, or None.
    """
    return moment.isoformat() if moment is not None else None


def named_ids(ids: Sequence[str]) -> list[JsonValue]:
    """Return at most ``MAX_IDS_ON_TRAIL`` of ``ids`` for a payload, so an event stays bounded.

    Args:
        ids: The ids to name, in order.

    Returns:
        The first ``MAX_IDS_ON_TRAIL`` of them; the caller records the full count beside them.
    """
    return list(ids[:MAX_IDS_ON_TRAIL])


def reason_text(reason: str) -> str:
    """Check an operator's free-text reason before it is recorded.

    Args:
        reason: Why, as the operator typed it, e.g. ``"not my device"``.

    Returns:
        ``reason``, unchanged.

    Raises:
        pydantic.ValidationError: Empty, longer than ``MAX_REASON_CHARS``, or carrying control
            or bidirectional characters.
    """
    return _REASON.validate_python(reason)
